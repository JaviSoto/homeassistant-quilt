from __future__ import annotations

import asyncio

from custom_components.quilt.proto_wire import decode_message, fixed32_to_float, get_first
from custom_components.quilt.quilt_parse import (
    QuiltComfortSetting,
    QuiltComfortSettingAttributes,
    QuiltComfortSettingHeader,
    QuiltComfortSettingRelationships,
    QuiltHdsSystem,
    QuiltSpace,
    QuiltSpaceControls,
    QuiltSpaceHeader,
    QuiltSpaceSettings,
    QuiltSpaceState,
    QuiltSystemInfo,
)
from custom_components.quilt.select import QuiltLouverModeSelect
from custom_components.quilt.quilt_parse import QuiltIndoorUnit, QuiltIndoorUnitControls, QuiltIndoorUnitHeader, QuiltIndoorUnitRelationships


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    async def async_update_indoor_unit(self, *, indoor_unit_message: bytes) -> bytes:  # noqa: ANN001
        self.calls.append(("UpdateIndoorUnit", indoor_unit_message))
        return b""


class FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data
        from homeassistant.core import HomeAssistant

        self.hass = HomeAssistant()

    async def async_request_refresh(self) -> None:
        return None


def _mk_fixture():
    system_id = "sys-1"
    space_id = "space-office"
    cs_active_id = "cs-active"
    cs_off_id = "cs-off"

    system = QuiltSystemInfo(system_id=system_id, name="Home", timezone="UTC")

    office = QuiltSpace(
        header=QuiltSpaceHeader(space_id=space_id, created=None, updated=None, system_id=system_id),
        relationships_parent_space_id="root",
        settings=QuiltSpaceSettings(name="Office", timezone="UTC"),
        controls=QuiltSpaceControls(
            hvac_mode=2,
            setpoint_c=20.0,
            cooling_setpoint_c=23.0,
            heating_setpoint_c=20.0,
            updated=None,
            unknown_field7=0,
            comfort_setting_override=None,
            comfort_setting_id=cs_active_id,
        ),
        state=QuiltSpaceState(updated=None, setpoint_c=20.0, ambient_c=21.0, hvac_state=2, comfort_setting_id=cs_active_id),
    )

    cs_active = QuiltComfortSetting(
        header=QuiltComfortSettingHeader(comfort_setting_id=cs_active_id, created=None, updated=None, system_id=system_id),
        attributes=QuiltComfortSettingAttributes(
            updated=None,
            name="Active",
            fan_speed_mode=1,
            fan_speed_percent=0.0,
            heating_setpoint_c=20.0,
            cooling_setpoint_c=23.0,
            comfort_setting_type=0,
            hvac_mode=2,
            louver_mode=4,  # automatic
            louver_fixed_position=0.0,
        ),
        relationships=QuiltComfortSettingRelationships(updated=None, space_id=space_id),
    )

    cs_off = QuiltComfortSetting(
        header=QuiltComfortSettingHeader(comfort_setting_id=cs_off_id, created=None, updated=None, system_id=system_id),
        attributes=QuiltComfortSettingAttributes(
            updated=None,
            name="Off",
            fan_speed_mode=1,
            fan_speed_percent=0.0,
            heating_setpoint_c=8.0,
            cooling_setpoint_c=8.0,
            comfort_setting_type=0,
            hvac_mode=1,
            louver_mode=4,
            louver_fixed_position=0.0,
        ),
        relationships=QuiltComfortSettingRelationships(updated=None, space_id=space_id),
    )

    iu = QuiltIndoorUnit(
        header=QuiltIndoorUnitHeader(indoor_unit_id="iu-1", created=None, updated=None, system_id=system_id),
        relationships=QuiltIndoorUnitRelationships(space_id=space_id),
        controls=QuiltIndoorUnitControls(
            updated=None,
            light_color_code=None,
            light_brightness=None,
            light_animation=None,
            fan_speed_mode=None,
            fan_speed_percent=None,
            louver_mode=4,  # automatic
            louver_fixed_position=0.0,
        ),
    )
    hds = QuiltHdsSystem(
        system_id=system_id,
        spaces={space_id: office},
        indoor_units={"iu-1": iu},
        indoor_units_by_space={space_id: [iu]},
        comfort_settings={cs_active_id: cs_active, cs_off_id: cs_off},
        comfort_settings_by_space={space_id: [cs_active, cs_off]},
        topic_ids={"space": {space_id}, "comfort_setting": {cs_active_id, cs_off_id}},
    )

    api = FakeApi()
    coord = FakeCoordinator(data=type("D", (), {"system": system, "hds": hds})())
    ent = QuiltLouverModeSelect(coordinator=coord, api=api, system_id=system_id, space_id=space_id, space_name="Office")
    return ent, api


def test_louver_select_options_and_current_option() -> None:
    ent, _ = _mk_fixture()
    assert ent.current_option == "Auto"
    assert "Fixed 25%" in ent.options
    assert "Sweep" in ent.options


def test_louver_select_sets_fixed_position() -> None:
    ent, api = _mk_fixture()
    asyncio.run(ent.async_select_option("Fixed 50%"))
    update_iu = next((payload for name, payload in api.calls if name == "UpdateIndoorUnit"), None)
    assert update_iu is not None
    top = decode_message(update_iu)
    controls = get_first(top, number=4, wire_type=2)
    assert controls is not None
    cmsg = decode_message(controls.value)
    louver = get_first(cmsg, number=10, wire_type=0)
    fixed = get_first(cmsg, number=11, wire_type=5)
    assert louver is not None and fixed is not None
    assert int(louver.value) == 3  # FIXED
    assert abs(fixed32_to_float(fixed.value) - 0.5) < 1e-6
