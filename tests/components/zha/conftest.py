"""Test configuration for the ZHA component."""
from collections.abc import Callable
import itertools
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import zigpy
from zigpy.application import ControllerApplication
import zigpy.backups
import zigpy.config
from zigpy.const import SIG_EP_INPUT, SIG_EP_OUTPUT, SIG_EP_PROFILE, SIG_EP_TYPE
import zigpy.device
import zigpy.group
import zigpy.profiles
import zigpy.quirks
import zigpy.types
import zigpy.zdo.types as zdo_t

import homeassistant.components.zha.core.const as zha_const
import homeassistant.components.zha.core.device as zha_core_device
from homeassistant.setup import async_setup_component

from . import common

from tests.common import MockConfigEntry
from tests.components.light.conftest import mock_light_profiles  # noqa: F401

FIXTURE_GRP_ID = 0x1001
FIXTURE_GRP_NAME = "fixture group"


@pytest.fixture(scope="session", autouse=True)
def globally_load_quirks():
    """Load quirks automatically so that ZHA tests run deterministically in isolation.

    If portions of the ZHA test suite that do not happen to load quirks are run
    independently, bugs can emerge that will show up only when more of the test suite is
    run.
    """

    import zhaquirks

    zhaquirks.setup()


class _FakeApp(ControllerApplication):
    async def add_endpoint(self, descriptor: zdo_t.SimpleDescriptor):
        pass

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def force_remove(self, dev: zigpy.device.Device):
        pass

    async def load_network_info(self, *, load_devices: bool = False):
        pass

    async def permit_ncp(self, time_s: int = 60):
        pass

    async def permit_with_key(
        self, node: zigpy.types.EUI64, code: bytes, time_s: int = 60
    ):
        pass

    async def reset_network_info(self):
        pass

    async def send_packet(self, packet: zigpy.types.ZigbeePacket):
        pass

    async def start_network(self):
        pass

    async def write_network_info(self):
        pass

    async def request(
        self,
        device: zigpy.device.Device,
        profile: zigpy.types.uint16_t,
        cluster: zigpy.types.uint16_t,
        src_ep: zigpy.types.uint8_t,
        dst_ep: zigpy.types.uint8_t,
        sequence: zigpy.types.uint8_t,
        data: bytes,
        *,
        expect_reply: bool = True,
        use_ieee: bool = False,
        extended_timeout: bool = False,
    ):
        pass


@pytest.fixture
def zigpy_app_controller():
    """Zigpy ApplicationController fixture."""
    app = _FakeApp(
        {
            zigpy.config.CONF_DATABASE: None,
            zigpy.config.CONF_DEVICE: {zigpy.config.CONF_DEVICE_PATH: "/dev/null"},
        }
    )

    app.groups.add_group(FIXTURE_GRP_ID, FIXTURE_GRP_NAME, suppress_event=True)

    app.state.node_info.nwk = 0x0000
    app.state.node_info.ieee = zigpy.types.EUI64.convert("00:15:8d:00:02:32:4f:32")
    app.state.network_info.pan_id = 0x1234
    app.state.network_info.extended_pan_id = app.state.node_info.ieee
    app.state.network_info.channel = 15
    app.state.network_info.network_key.key = zigpy.types.KeyData(range(16))

    with patch("zigpy.device.Device.request"):
        yield app


@pytest.fixture(name="config_entry")
async def config_entry_fixture(hass):
    """Fixture representing a config entry."""
    entry = MockConfigEntry(
        version=2,
        domain=zha_const.DOMAIN,
        data={
            zigpy.config.CONF_DEVICE: {zigpy.config.CONF_DEVICE_PATH: "/dev/ttyUSB0"},
            zha_const.CONF_RADIO_TYPE: "ezsp",
        },
        options={
            zha_const.CUSTOM_CONFIGURATION: {
                zha_const.ZHA_OPTIONS: {
                    zha_const.CONF_ENABLE_ENHANCED_LIGHT_TRANSITION: True,
                    zha_const.CONF_GROUP_MEMBERS_ASSUME_STATE: False,
                },
                zha_const.ZHA_ALARM_OPTIONS: {
                    zha_const.CONF_ALARM_ARM_REQUIRES_CODE: False,
                    zha_const.CONF_ALARM_MASTER_CODE: "4321",
                    zha_const.CONF_ALARM_FAILED_TRIES: 2,
                },
            }
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def setup_zha(hass, config_entry, zigpy_app_controller):
    """Set up ZHA component."""
    zha_config = {zha_const.CONF_ENABLE_QUIRKS: False}

    p1 = patch(
        "bellows.zigbee.application.ControllerApplication.new",
        return_value=zigpy_app_controller,
    )

    async def _setup(config=None):
        config = config or {}
        with p1:
            status = await async_setup_component(
                hass, zha_const.DOMAIN, {zha_const.DOMAIN: {**zha_config, **config}}
            )
            assert status is True
            await hass.async_block_till_done()

    return _setup


@pytest.fixture
def cluster_handler():
    """ClusterHandler mock factory fixture."""

    def cluster_handler(name: str, cluster_id: int, endpoint_id: int = 1):
        ch = MagicMock()
        ch.name = name
        ch.generic_id = f"cluster_handler_0x{cluster_id:04x}"
        ch.id = f"{endpoint_id}:0x{cluster_id:04x}"
        ch.async_configure = AsyncMock()
        ch.async_initialize = AsyncMock()
        return ch

    return cluster_handler


@pytest.fixture
def zigpy_device_mock(zigpy_app_controller):
    """Make a fake device using the specified cluster classes."""

    def _mock_dev(
        endpoints,
        ieee="00:0d:6f:00:0a:90:69:e7",
        manufacturer="FakeManufacturer",
        model="FakeModel",
        node_descriptor=b"\x02@\x807\x10\x7fd\x00\x00*d\x00\x00",
        nwk=0xB79C,
        patch_cluster=True,
        quirk=None,
    ):
        """Make a fake device using the specified cluster classes."""
        device = zigpy.device.Device(
            zigpy_app_controller, zigpy.types.EUI64.convert(ieee), nwk
        )
        device.manufacturer = manufacturer
        device.model = model
        device.node_desc = zdo_t.NodeDescriptor.deserialize(node_descriptor)[0]
        device.last_seen = time.time()

        for epid, ep in endpoints.items():
            endpoint = device.add_endpoint(epid)
            endpoint.device_type = ep[SIG_EP_TYPE]
            endpoint.profile_id = ep.get(SIG_EP_PROFILE, 0x0104)
            endpoint.request = AsyncMock()

            for cluster_id in ep.get(SIG_EP_INPUT, []):
                endpoint.add_input_cluster(cluster_id)

            for cluster_id in ep.get(SIG_EP_OUTPUT, []):
                endpoint.add_output_cluster(cluster_id)

        device.status = zigpy.device.Status.ENDPOINTS_INIT

        if quirk:
            device = quirk(zigpy_app_controller, device.ieee, device.nwk, device)
        else:
            # Allow zigpy to apply quirks if we don't pass one explicitly
            device = zigpy.quirks.get_device(device)

        if patch_cluster:
            for endpoint in (ep for epid, ep in device.endpoints.items() if epid):
                endpoint.request = AsyncMock(return_value=[0])
                for cluster in itertools.chain(
                    endpoint.in_clusters.values(), endpoint.out_clusters.values()
                ):
                    common.patch_cluster(cluster)

        return device

    return _mock_dev


@pytest.fixture
def zha_device_joined(hass, setup_zha):
    """Return a newly joined ZHA device."""

    async def _zha_device(zigpy_dev):
        zigpy_dev.last_seen = time.time()
        await setup_zha()
        zha_gateway = common.get_zha_gateway(hass)
        await zha_gateway.async_device_initialized(zigpy_dev)
        await hass.async_block_till_done()
        return zha_gateway.get_device(zigpy_dev.ieee)

    return _zha_device


@pytest.fixture
def zha_device_restored(hass, zigpy_app_controller, setup_zha):
    """Return a restored ZHA device."""

    async def _zha_device(zigpy_dev, last_seen=None):
        zigpy_app_controller.devices[zigpy_dev.ieee] = zigpy_dev

        if last_seen is not None:
            zigpy_dev.last_seen = last_seen

        await setup_zha()
        zha_gateway = hass.data[zha_const.DATA_ZHA][zha_const.DATA_ZHA_GATEWAY]
        return zha_gateway.get_device(zigpy_dev.ieee)

    return _zha_device


@pytest.fixture(params=["zha_device_joined", "zha_device_restored"])
def zha_device_joined_restored(request):
    """Join or restore ZHA device."""
    named_method = request.getfixturevalue(request.param)
    named_method.name = request.param
    return named_method


@pytest.fixture
def zha_device_mock(
    hass, zigpy_device_mock
) -> Callable[..., zha_core_device.ZHADevice]:
    """Return a ZHA Device factory."""

    def _zha_device(
        endpoints=None,
        ieee="00:11:22:33:44:55:66:77",
        manufacturer="mock manufacturer",
        model="mock model",
        node_desc=b"\x02@\x807\x10\x7fd\x00\x00*d\x00\x00",
        patch_cluster=True,
    ) -> zha_core_device.ZHADevice:
        if endpoints is None:
            endpoints = {
                1: {
                    "in_clusters": [0, 1, 8, 768],
                    "out_clusters": [0x19],
                    "device_type": 0x0105,
                },
                2: {
                    "in_clusters": [0],
                    "out_clusters": [6, 8, 0x19, 768],
                    "device_type": 0x0810,
                },
            }
        zigpy_device = zigpy_device_mock(
            endpoints, ieee, manufacturer, model, node_desc, patch_cluster=patch_cluster
        )
        zha_device = zha_core_device.ZHADevice(hass, zigpy_device, MagicMock())
        return zha_device

    return _zha_device


@pytest.fixture
def hass_disable_services(hass):
    """Mock service register."""
    with patch.object(hass.services, "async_register"), patch.object(
        hass.services, "has_service", return_value=True
    ):
        yield hass
