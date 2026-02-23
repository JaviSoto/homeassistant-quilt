from __future__ import annotations

from custom_components.quilt.proto_wire import encode_bytes_field, encode_fixed32_float, encode_varint_field
from custom_components.quilt.quilt_parse import parse_get_home_datastore_system_response, parse_list_systems_response


def _ts(sec: int = 1700000000, ns: int = 123) -> bytes:
    return encode_varint_field(1, sec) + encode_varint_field(2, ns)


def test_parse_list_systems_response() -> None:
    sys_id = "sys-1"
    name = "Home"
    tz = "UTC"
    entry = b"".join(
        [
            encode_bytes_field(1, sys_id.encode("utf-8")),
            encode_bytes_field(2, name.encode("utf-8")),
            encode_bytes_field(3, tz.encode("utf-8")),
        ]
    )
    top = encode_bytes_field(1, entry)
    systems = parse_list_systems_response(top)
    assert len(systems) == 1
    assert systems[0].system_id == sys_id
    assert systems[0].name == name
    assert systems[0].timezone == tz


def test_parse_get_home_datastore_system_response_minimal() -> None:
    system_id = "sys-1"
    space_id = "space-1"
    cs_active_id = "cs-active"
    cs_off_id = "cs-off"
    indoor_unit_id = "iu-1"

    # Space header/settings/controls/state
    header = encode_bytes_field(1, space_id.encode("utf-8")) + encode_bytes_field(4, system_id.encode("utf-8"))
    settings = encode_bytes_field(1, b"Office") + encode_bytes_field(4, b"UTC")
    controls = b"".join(
        [
            encode_varint_field(1, 2),  # COOL
            encode_fixed32_float(2, 21.0),
            encode_bytes_field(3, _ts()),
            encode_fixed32_float(4, 21.0),
            encode_fixed32_float(5, 21.0),
            encode_varint_field(7, 0),
            encode_varint_field(8, 2),
            encode_bytes_field(9, cs_active_id.encode("utf-8")),
        ]
    )
    state = b"".join(
        [
            encode_bytes_field(1, _ts()),
            encode_fixed32_float(2, 21.0),
            encode_fixed32_float(3, 22.0),
            encode_varint_field(4, 2),
            encode_bytes_field(5, cs_active_id.encode("utf-8")),
        ]
    )
    space_msg = b"".join(
        [
            encode_bytes_field(1, header),
            encode_bytes_field(3, settings),
            encode_bytes_field(4, controls),
            encode_bytes_field(5, state),
        ]
    )

    # Comfort setting messages (Active/Off)
    def cs_msg(cs_id: str, name: str) -> bytes:
        cs_header = encode_bytes_field(1, cs_id.encode("utf-8")) + encode_bytes_field(4, system_id.encode("utf-8"))
        attrs = b"".join(
            [
                encode_bytes_field(1, _ts()),
                encode_bytes_field(2, name.encode("utf-8")),
                encode_varint_field(3, 1),
                encode_fixed32_float(4, 0.0),
                encode_fixed32_float(5, 20.0),
                encode_fixed32_float(6, 20.0),
                encode_varint_field(7, 0),
                encode_varint_field(8, 2),
                encode_varint_field(9, 4),
                encode_fixed32_float(10, 0.0),
            ]
        )
        rel = encode_bytes_field(1, _ts()) + encode_bytes_field(2, space_id.encode("utf-8"))
        return encode_bytes_field(1, cs_header) + encode_bytes_field(2, attrs) + encode_bytes_field(3, rel)

    # Indoor unit object (field 9 in top-level)
    iu_header = encode_bytes_field(1, indoor_unit_id.encode("utf-8")) + encode_bytes_field(4, system_id.encode("utf-8"))
    iu_rel = b"".join([encode_bytes_field(2, space_id.encode("utf-8"))])
    iu_controls = b"".join(
        [
            encode_varint_field(3, 0xFF460064),
            encode_fixed32_float(4, 0.5),
            encode_varint_field(5, 1),
            encode_fixed32_float(6, 0.0),
            encode_bytes_field(7, _ts()),
            encode_varint_field(10, 4),
            encode_fixed32_float(11, 0.0),
            encode_varint_field(12, 4),
        ]
    )
    iu_msg = encode_bytes_field(1, iu_header) + encode_bytes_field(2, iu_rel) + encode_bytes_field(4, iu_controls)

    top = b"".join(
        [
            encode_bytes_field(3, space_msg),
            encode_bytes_field(13, cs_msg(cs_active_id, "Active")),
            encode_bytes_field(13, cs_msg(cs_off_id, "Off")),
            encode_bytes_field(9, iu_msg),
        ]
    )

    hds = parse_get_home_datastore_system_response(top)
    assert hds.system_id == system_id
    assert space_id in hds.spaces
    assert indoor_unit_id in hds.indoor_units
    assert hds.indoor_units_by_space[space_id][0].header.indoor_unit_id == indoor_unit_id
    assert cs_active_id in hds.comfort_settings
    assert hds.comfort_settings_by_space[space_id]

