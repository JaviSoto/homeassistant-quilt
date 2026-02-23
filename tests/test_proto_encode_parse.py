from __future__ import annotations

from custom_components.quilt.hds_encode import encode_update_indoor_unit_request
from custom_components.quilt.proto_wire import decode_message, encode_bytes_field, encode_fixed32_float, encode_varint_field, fixed32_to_float, get_first
from custom_components.quilt.quilt_parse import (
    QuiltIndoorUnit,
    QuiltIndoorUnitControls,
    QuiltIndoorUnitHeader,
    QuiltIndoorUnitRelationships,
    _parse_indoor_unit_controls,
    _parse_indoor_unit_relationships,
)


def test_parse_indoor_unit_relationships_space_id() -> None:
    rel = encode_bytes_field(2, b"space-123")
    parsed = _parse_indoor_unit_relationships(rel)
    assert parsed is not None
    assert parsed.space_id == "space-123"


def test_parse_indoor_unit_controls_fields() -> None:
    # Construct a controls message with the fields we observed from the official app.
    ts = encode_varint_field(1, 1700000000) + encode_varint_field(2, 123)
    raw = b"".join(
        [
            encode_varint_field(3, 0xFF460064),
            encode_fixed32_float(4, 0.5),
            encode_varint_field(5, 2),
            encode_fixed32_float(6, 0.7),
            encode_bytes_field(7, ts),
            encode_varint_field(10, 4),
            encode_fixed32_float(11, 1.0),
            encode_varint_field(12, 5),
        ]
    )
    c = _parse_indoor_unit_controls(raw)
    assert c.light_color_code == 0xFF460064
    assert c.light_animation == 5
    assert abs(float(c.light_brightness or 0.0) - 0.5) < 1e-6
    assert abs(float(c.fan_speed_percent or 0.0) - 0.7) < 1e-6
    assert c.louver_mode == 4
    assert abs(float(c.louver_fixed_position or 0.0) - 1.0) < 1e-6
    assert c.updated is not None


def test_encode_update_indoor_unit_request_contains_expected_fields() -> None:
    iu = QuiltIndoorUnit(
        header=QuiltIndoorUnitHeader(
            indoor_unit_id="iu-1",
            created=None,
            updated=None,
            system_id="sys-1",
        ),
        relationships=QuiltIndoorUnitRelationships(space_id="space-1"),
        controls=QuiltIndoorUnitControls(
            updated=None,
            light_color_code=0xFF460064,
            light_brightness=0.25,
            light_animation=3,
            fan_speed_mode=1,
            fan_speed_percent=0.0,
            louver_mode=4,
            louver_fixed_position=0.0,
        ),
    )
    req = encode_update_indoor_unit_request(iu, light_brightness=0.75, light_color_code=0x009CFF54, light_animation=4)

    msg = decode_message(req)
    header_f = get_first(msg, number=1, wire_type=2)
    controls_f = get_first(msg, number=4, wire_type=2)
    assert header_f is not None
    assert controls_f is not None

    controls = decode_message(controls_f.value)
    color = get_first(controls, number=3, wire_type=0)
    brightness = get_first(controls, number=4, wire_type=5)
    anim = get_first(controls, number=12, wire_type=0)
    assert int(color.value) == 0x009CFF54
    assert abs(fixed32_to_float(brightness.value) - 0.75) < 1e-6
    assert int(anim.value) == 4

