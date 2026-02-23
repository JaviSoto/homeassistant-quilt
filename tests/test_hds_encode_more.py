from __future__ import annotations

from custom_components.quilt.hds_encode import encode_space_diff, encode_update_comfort_setting_request
from custom_components.quilt.proto_wire import decode_message, fixed32_to_float, get_first
from custom_components.quilt.quilt_parse import (
    QuiltComfortSetting,
    QuiltComfortSettingAttributes,
    QuiltComfortSettingHeader,
    QuiltComfortSettingRelationships,
    QuiltSpace,
    QuiltSpaceControls,
    QuiltSpaceHeader,
    QuiltSpaceSettings,
    QuiltSpaceState,
)


def test_encode_space_diff_contains_controls_and_header() -> None:
    space = QuiltSpace(
        header=QuiltSpaceHeader(space_id="s1", created=None, updated=None, system_id="sys"),
        relationships_parent_space_id="root",
        settings=QuiltSpaceSettings(name="Office", timezone="UTC"),
        controls=QuiltSpaceControls(
            hvac_mode=2,
            setpoint_c=20.0,
            cooling_setpoint_c=20.0,
            heating_setpoint_c=20.0,
            updated=None,
            unknown_field7=0,
            comfort_setting_override=None,
            comfort_setting_id="cs",
        ),
        state=QuiltSpaceState(updated=None, setpoint_c=20.0, ambient_c=21.0, hvac_state=1, comfort_setting_id="cs"),
    )
    msg = encode_space_diff(
        space,
        hvac_mode=2,
        setpoint_c=22.5,
        heat_c=21.0,
        cool_c=24.0,
        comfort_setting_id="cs",
        comfort_setting_override=2,
    )
    top = decode_message(msg)
    header = get_first(top, number=1, wire_type=2)
    controls = get_first(top, number=4, wire_type=2)
    assert header is not None and controls is not None
    cmsg = decode_message(controls.value)
    setpoint = get_first(cmsg, number=2, wire_type=5)
    cool = get_first(cmsg, number=4, wire_type=5)
    heat = get_first(cmsg, number=5, wire_type=5)
    assert setpoint is not None
    assert heat is not None
    assert cool is not None
    assert abs(fixed32_to_float(setpoint.value) - 22.5) < 1e-6
    assert abs(fixed32_to_float(heat.value) - 21.0) < 1e-6
    assert abs(fixed32_to_float(cool.value) - 24.0) < 1e-6


def test_encode_update_comfort_setting_request_sets_fan_and_louver() -> None:
    cs = QuiltComfortSetting(
        header=QuiltComfortSettingHeader(comfort_setting_id="cs1", created=None, updated=None, system_id="sys"),
        attributes=QuiltComfortSettingAttributes(
            updated=None,
            name="Active",
            fan_speed_mode=1,
            fan_speed_percent=0.0,
            heating_setpoint_c=20.0,
            cooling_setpoint_c=20.0,
            comfort_setting_type=0,
            hvac_mode=2,
            louver_mode=4,
            louver_fixed_position=0.0,
        ),
        relationships=QuiltComfortSettingRelationships(updated=None, space_id="s1"),
    )
    msg = encode_update_comfort_setting_request(cs, heat_c=21.0, cool_c=21.0, fan_speed_mode=2, fan_speed_percent=0.6, louver_mode=2)
    top = decode_message(msg)
    attrs = get_first(top, number=2, wire_type=2)
    assert attrs is not None
    amsg = decode_message(attrs.value)
    fan_mode = get_first(amsg, number=3, wire_type=0)
    fan_pct = get_first(amsg, number=4, wire_type=5)
    louver = get_first(amsg, number=9, wire_type=0)
    assert int(fan_mode.value) == 2
    assert abs(fixed32_to_float(fan_pct.value) - 0.6) < 1e-6
    assert int(louver.value) == 2
