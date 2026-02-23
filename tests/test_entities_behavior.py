from __future__ import annotations

import asyncio

from custom_components.quilt.climate import QuiltSpaceClimate
from custom_components.quilt.fan import QuiltFan
from custom_components.quilt.light import QuiltIndoorUnitLight
from custom_components.quilt.proto_wire import decode_message, fixed32_to_float, get_first
from custom_components.quilt.quilt_parse import (
    QuiltComfortSetting,
    QuiltComfortSettingAttributes,
    QuiltComfortSettingHeader,
    QuiltComfortSettingRelationships,
    QuiltHdsSystem,
    QuiltIndoorUnit,
    QuiltIndoorUnitControls,
    QuiltIndoorUnitHeader,
    QuiltIndoorUnitRelationships,
    QuiltSpace,
    QuiltSpaceControls,
    QuiltSpaceHeader,
    QuiltSpaceSettings,
    QuiltSpaceState,
    QuiltSystemInfo,
)


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    async def async_update_space(self, *, space_message: bytes) -> bytes:  # noqa: ANN001
        self.calls.append(("UpdateSpace", space_message))
        return b""

    async def async_update_comfort_setting(self, *, comfort_setting_message: bytes) -> bytes:  # noqa: ANN001
        self.calls.append(("UpdateComfortSetting", comfort_setting_message))
        return b""

    async def async_update_indoor_unit(self, *, indoor_unit_message: bytes) -> bytes:  # noqa: ANN001
        self.calls.append(("UpdateIndoorUnit", indoor_unit_message))
        return b""


class FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data
        self.name = "Quilt Test"
        from homeassistant.core import HomeAssistant

        self.hass = HomeAssistant()

    async def async_request_refresh(self) -> None:
        return None


def _mk_fixture() -> tuple[FakeCoordinator, FakeApi, str, str, str]:
    system_id = "sys-1"
    office_space_id = "space-office"
    indoor_unit_id = "iu-office"
    cs_active_id = "cs-active"
    cs_off_id = "cs-off"

    system = QuiltSystemInfo(system_id=system_id, name="Home", timezone="UTC")

    office = QuiltSpace(
        header=QuiltSpaceHeader(space_id=office_space_id, created=None, updated=None, system_id=system_id),
        relationships_parent_space_id="root",
        settings=QuiltSpaceSettings(name="Office", timezone="UTC"),
        controls=QuiltSpaceControls(
            hvac_mode=1,  # STANDBY (off)
            setpoint_c=20.0,
            cooling_setpoint_c=20.0,
            heating_setpoint_c=20.0,
            updated=None,
            unknown_field7=0,
            comfort_setting_override=None,
            comfort_setting_id=cs_off_id,
        ),
        state=QuiltSpaceState(updated=None, setpoint_c=20.0, ambient_c=21.0, hvac_state=1, comfort_setting_id=cs_off_id),
    )

    cs_active = QuiltComfortSetting(
        header=QuiltComfortSettingHeader(comfort_setting_id=cs_active_id, created=None, updated=None, system_id=system_id),
        attributes=QuiltComfortSettingAttributes(
            updated=None,
            name="Active",
            fan_speed_mode=1,
            fan_speed_percent=0.0,
            heating_setpoint_c=20.0,
            cooling_setpoint_c=20.0,
            comfort_setting_type=0,
            hvac_mode=1,
            louver_mode=4,
            louver_fixed_position=0.0,
        ),
        relationships=QuiltComfortSettingRelationships(updated=None, space_id=office_space_id),
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
        relationships=QuiltComfortSettingRelationships(updated=None, space_id=office_space_id),
    )

    iu = QuiltIndoorUnit(
        header=QuiltIndoorUnitHeader(indoor_unit_id=indoor_unit_id, created=None, updated=None, system_id=system_id),
        relationships=QuiltIndoorUnitRelationships(space_id=office_space_id),
        controls=QuiltIndoorUnitControls(
            updated=None,
            light_color_code=0xFF460064,
            light_brightness=0.0,
            light_animation=1,
            fan_speed_mode=1,
            fan_speed_percent=0.0,
            louver_mode=4,
            louver_fixed_position=0.0,
        ),
    )

    hds = QuiltHdsSystem(
        system_id=system_id,
        spaces={office_space_id: office},
        indoor_units={indoor_unit_id: iu},
        indoor_units_by_space={office_space_id: [iu]},
        comfort_settings={cs_active_id: cs_active, cs_off_id: cs_off},
        comfort_settings_by_space={office_space_id: [cs_active, cs_off]},
        topic_ids={"space": {office_space_id}, "indoor_unit": {indoor_unit_id}, "comfort_setting": {cs_active_id, cs_off_id}},
    )

    api = FakeApi()
    coord = FakeCoordinator(data=type("D", (), {"system": system, "hds": hds})())
    return coord, api, system_id, office_space_id, indoor_unit_id


def test_light_effect_list_present() -> None:
    coord, api, system_id, space_id, indoor_unit_id = _mk_fixture()
    ent = QuiltIndoorUnitLight(
        coordinator=coord,
        api=api,
        system_id=system_id,
        space_id=space_id,
        space_name="Office",
        indoor_unit_id=indoor_unit_id,
    )
    assert ent.effect is not None  # from fixture
    assert ent.effect_list is not None  # provided by base attr list


def test_light_turn_on_sets_rgb_and_effect() -> None:
    coord, api, system_id, space_id, indoor_unit_id = _mk_fixture()
    ent = QuiltIndoorUnitLight(
        coordinator=coord,
        api=api,
        system_id=system_id,
        space_id=space_id,
        space_name="Office",
        indoor_unit_id=indoor_unit_id,
    )
    asyncio.run(ent.async_turn_on(brightness=128, rgb_color=(0, 156, 255), effect="Dance"))
    assert api.calls and api.calls[-1][0] == "UpdateIndoorUnit"


def test_climate_fan_mode_roundtrip() -> None:
    coord, api, system_id, space_id, _ = _mk_fixture()
    ent = QuiltSpaceClimate(coordinator=coord, api=api, system_id=system_id, space_id=space_id, space_name="Office")
    # Default is auto.
    assert ent.fan_mode == "auto"
    asyncio.run(ent.async_set_fan_mode("40%"))
    assert any(c[0] == "UpdateComfortSetting" for c in api.calls)


def test_climate_set_temperature_when_off_only_updates_comfort() -> None:
    coord, api, system_id, space_id, _ = _mk_fixture()
    ent = QuiltSpaceClimate(coordinator=coord, api=api, system_id=system_id, space_id=space_id, space_name="Office")
    asyncio.run(ent.async_set_temperature(temperature=22.0))
    # When off, we only update comfort setting, not UpdateSpace.
    assert any(c[0] == "UpdateComfortSetting" for c in api.calls)
    assert not any(c[0] == "UpdateSpace" for c in api.calls)

def test_climate_set_temperature_with_hvac_mode_when_off_updates_space() -> None:
    coord, api, system_id, space_id, _ = _mk_fixture()
    ent = QuiltSpaceClimate(coordinator=coord, api=api, system_id=system_id, space_id=space_id, space_name="Office")

    # HomeKit commonly sends both hvac_mode and temperature in one call.
    from homeassistant.components.climate.const import HVACMode

    asyncio.run(ent.async_set_temperature(temperature=22.0, hvac_mode=HVACMode.HEAT))
    assert any(c[0] == "UpdateSpace" for c in api.calls)

    update_space = next((payload for name, payload in api.calls if name == "UpdateSpace"), None)
    assert update_space is not None
    msg = decode_message(update_space)
    controls_f = get_first(msg, number=4, wire_type=2)
    assert controls_f is not None
    controls = decode_message(controls_f.value)
    setpoint_f = get_first(controls, number=2, wire_type=5)
    assert setpoint_f is not None
    assert abs(fixed32_to_float(setpoint_f.value) - 22.0) < 1e-6


def test_climate_heat_cool_mode_exposes_and_sets_range_setpoints() -> None:
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
            hvac_mode=4,  # AUTOMATIC
            setpoint_c=21.5,
            cooling_setpoint_c=23.0,
            heating_setpoint_c=20.0,
            updated=None,
            unknown_field7=0,
            comfort_setting_override=None,
            comfort_setting_id=cs_active_id,
        ),
        state=QuiltSpaceState(updated=None, setpoint_c=21.5, ambient_c=21.0, hvac_state=4, comfort_setting_id=cs_active_id),
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
            hvac_mode=4,
            louver_mode=4,
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

    hds = QuiltHdsSystem(
        system_id=system_id,
        spaces={space_id: office},
        indoor_units={},
        indoor_units_by_space={},
        comfort_settings={cs_active_id: cs_active, cs_off_id: cs_off},
        comfort_settings_by_space={space_id: [cs_active, cs_off]},
        topic_ids={"space": {space_id}, "comfort_setting": {cs_active_id, cs_off_id}},
    )

    api = FakeApi()
    coord = FakeCoordinator(data=type("D", (), {"system": system, "hds": hds})())
    ent = QuiltSpaceClimate(coordinator=coord, api=api, system_id=system_id, space_id=space_id, space_name="Office")

    from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode

    assert ent.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    assert ent.target_temperature_low == 20.0
    assert ent.target_temperature_high == 23.0
    assert ent.target_temperature is None

    asyncio.run(ent.async_set_temperature(target_temp_low=19.0, target_temp_high=24.0, hvac_mode=HVACMode.HEAT_COOL))

    update_space = next((payload for name, payload in api.calls if name == "UpdateSpace"), None)
    assert update_space is not None
    msg = decode_message(update_space)
    controls_f = get_first(msg, number=4, wire_type=2)
    assert controls_f is not None
    controls = decode_message(controls_f.value)
    setpoint_f = get_first(controls, number=2, wire_type=5)
    cool_f = get_first(controls, number=4, wire_type=5)
    heat_f = get_first(controls, number=5, wire_type=5)
    assert setpoint_f is not None and cool_f is not None and heat_f is not None
    assert abs(fixed32_to_float(setpoint_f.value) - 21.5) < 1e-6
    assert abs(fixed32_to_float(heat_f.value) - 19.0) < 1e-6
    assert abs(fixed32_to_float(cool_f.value) - 24.0) < 1e-6

    update_cs = next((payload for name, payload in api.calls if name == "UpdateComfortSetting"), None)
    assert update_cs is not None
    top = decode_message(update_cs)
    attrs = get_first(top, number=2, wire_type=2)
    assert attrs is not None
    amsg = decode_message(attrs.value)
    heat_attr = get_first(amsg, number=5, wire_type=5)
    cool_attr = get_first(amsg, number=6, wire_type=5)
    assert heat_attr is not None and cool_attr is not None
    assert abs(fixed32_to_float(heat_attr.value) - 19.0) < 1e-6
    assert abs(fixed32_to_float(cool_attr.value) - 24.0) < 1e-6


def test_climate_turn_off_does_not_force_min_setpoint() -> None:
    coord, api, system_id, space_id, _ = _mk_fixture()
    ent = QuiltSpaceClimate(coordinator=coord, api=api, system_id=system_id, space_id=space_id, space_name="Office")

    # Turn off when current setpoint is 20C in fixture.
    from homeassistant.components.climate.const import HVACMode

    asyncio.run(ent.async_set_hvac_mode(HVACMode.OFF))

    update_space = next((payload for name, payload in api.calls if name == "UpdateSpace"), None)
    assert update_space is not None

    msg = decode_message(update_space)
    controls_f = get_first(msg, number=4, wire_type=2)
    assert controls_f is not None
    controls = decode_message(controls_f.value)
    setpoint_f = get_first(controls, number=2, wire_type=5)
    assert setpoint_f is not None
    assert abs(fixed32_to_float(setpoint_f.value) - 20.0) < 1e-6


def test_climate_turn_on_prefers_active_comfort_setpoint() -> None:
    # Simulate: controls show min setpoint, but Active comfort setting has the desired target.
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
            hvac_mode=1,  # off
            setpoint_c=8.0,
            cooling_setpoint_c=8.0,
            heating_setpoint_c=8.0,
            updated=None,
            unknown_field7=0,
            comfort_setting_override=None,
            comfort_setting_id=cs_off_id,
        ),
        state=QuiltSpaceState(updated=None, setpoint_c=8.0, ambient_c=21.0, hvac_state=1, comfort_setting_id=cs_off_id),
    )

    cs_active = QuiltComfortSetting(
        header=QuiltComfortSettingHeader(comfort_setting_id=cs_active_id, created=None, updated=None, system_id=system_id),
        attributes=QuiltComfortSettingAttributes(
            updated=None,
            name="Active",
            fan_speed_mode=1,
            fan_speed_percent=0.0,
            heating_setpoint_c=21.5,
            cooling_setpoint_c=21.5,
            comfort_setting_type=0,
            hvac_mode=1,
            louver_mode=4,
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

    hds = QuiltHdsSystem(
        system_id=system_id,
        spaces={space_id: office},
        indoor_units={},
        indoor_units_by_space={},
        comfort_settings={cs_active_id: cs_active, cs_off_id: cs_off},
        comfort_settings_by_space={space_id: [cs_active, cs_off]},
        topic_ids={"space": {space_id}, "comfort_setting": {cs_active_id, cs_off_id}},
    )

    api = FakeApi()
    coord = FakeCoordinator(data=type("D", (), {"system": system, "hds": hds})())
    ent = QuiltSpaceClimate(coordinator=coord, api=api, system_id=system_id, space_id=space_id, space_name="Office")

    from homeassistant.components.climate.const import HVACMode

    asyncio.run(ent.async_set_hvac_mode(HVACMode.HEAT))

    update_space = next((payload for name, payload in api.calls if name == "UpdateSpace"), None)
    assert update_space is not None
    msg = decode_message(update_space)
    controls_f = get_first(msg, number=4, wire_type=2)
    assert controls_f is not None
    controls = decode_message(controls_f.value)
    setpoint_f = get_first(controls, number=2, wire_type=5)
    assert setpoint_f is not None
    assert abs(fixed32_to_float(setpoint_f.value) - 21.5) < 1e-6


def test_fan_entity_sets_percentage() -> None:
    coord, api, system_id, space_id, _ = _mk_fixture()
    ent = QuiltFan(coordinator=coord, api=api, system_id=system_id, space_id=space_id, space_name="Office")
    asyncio.run(ent.async_turn_on(percentage=60))
    assert any(c[0] == "UpdateComfortSetting" for c in api.calls)
