from __future__ import annotations

from custom_components.quilt.proto_wire import (
    encode_bytes_field,
    encode_fixed32_float,
    encode_varint_field,
)
from custom_components.quilt.quilt_parse import (
    parse_get_home_datastore_system_response,
    parse_list_systems_response,
)


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
    controller_id = "controller-1"

    # Space header/settings/controls/state
    header = encode_bytes_field(1, space_id.encode("utf-8")) + encode_bytes_field(
        4, system_id.encode("utf-8")
    )
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
        cs_header = encode_bytes_field(1, cs_id.encode("utf-8")) + encode_bytes_field(
            4, system_id.encode("utf-8")
        )
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
        rel = encode_bytes_field(1, _ts()) + encode_bytes_field(
            2, space_id.encode("utf-8")
        )
        return (
            encode_bytes_field(1, cs_header)
            + encode_bytes_field(2, attrs)
            + encode_bytes_field(3, rel)
        )

    # Indoor unit object (field 9 in top-level)
    iu_header = encode_bytes_field(
        1, indoor_unit_id.encode("utf-8")
    ) + encode_bytes_field(4, system_id.encode("utf-8"))
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
    iu_state = (
        encode_bytes_field(1, _ts())
        + encode_fixed32_float(3, 17.0)
        + encode_fixed32_float(9, 22.75)
    )
    iu_msg = (
        encode_bytes_field(1, iu_header)
        + encode_bytes_field(2, iu_rel)
        + encode_bytes_field(4, iu_controls)
        + encode_bytes_field(5, iu_state)
    )

    controller_header = encode_bytes_field(
        1, controller_id.encode("utf-8")
    ) + encode_bytes_field(4, system_id.encode("utf-8"))
    controller_rel = encode_bytes_field(2, space_id.encode("utf-8"))
    controller_settings = encode_bytes_field(1, b"Dial QD1")
    controller_state = encode_fixed32_float(5, 24.5) + encode_bytes_field(15, _ts())
    controller_msg = b"".join(
        [
            encode_bytes_field(1, controller_header),
            encode_bytes_field(2, controller_rel),
            encode_bytes_field(3, controller_settings),
            encode_bytes_field(4, controller_state),
        ]
    )

    top = b"".join(
        [
            encode_bytes_field(3, space_msg),
            encode_bytes_field(13, cs_msg(cs_active_id, "Active")),
            encode_bytes_field(13, cs_msg(cs_off_id, "Off")),
            encode_bytes_field(9, iu_msg),
            encode_bytes_field(11, controller_msg),
        ]
    )

    hds = parse_get_home_datastore_system_response(top)
    assert hds.system_id == system_id
    assert space_id in hds.spaces
    assert indoor_unit_id in hds.indoor_units
    assert (
        hds.indoor_units_by_space[space_id][0].header.indoor_unit_id == indoor_unit_id
    )
    assert hds.indoor_units[indoor_unit_id].state is not None
    assert hds.indoor_units[indoor_unit_id].state.ambient_c == 22.75
    assert hds.controllers[controller_id].state is not None
    assert hds.controllers[controller_id].state.ambient_c == 24.5
    assert hds.controllers_by_space[space_id][0].header.controller_id == controller_id
    assert cs_active_id in hds.comfort_settings
    assert hds.comfort_settings_by_space[space_id]


def test_parse_get_home_datastore_system_response_remote_sensor() -> None:
    system_id = "sys-1"
    space_id = "space-1"
    indoor_unit_id = "iu-1"
    remote_sensor_id = "remote-1"

    space_header = encode_bytes_field(1, space_id.encode("utf-8")) + encode_bytes_field(
        4, system_id.encode("utf-8")
    )
    space_settings = encode_bytes_field(1, b"Office")
    space_state = encode_bytes_field(1, _ts()) + encode_fixed32_float(3, 22.0)
    space_msg = b"".join(
        [
            encode_bytes_field(1, space_header),
            encode_bytes_field(3, space_settings),
            encode_bytes_field(5, space_state),
        ]
    )

    iu_header = encode_bytes_field(
        1, indoor_unit_id.encode("utf-8")
    ) + encode_bytes_field(4, system_id.encode("utf-8"))
    iu_rel = encode_bytes_field(2, space_id.encode("utf-8"))
    iu_msg = encode_bytes_field(1, iu_header) + encode_bytes_field(2, iu_rel)

    sensor_header = encode_bytes_field(
        1, remote_sensor_id.encode("utf-8")
    ) + encode_bytes_field(4, system_id.encode("utf-8"))
    sensor_attrs = encode_bytes_field(1, b"AA:BB:CC:DD:EE:FF") + encode_bytes_field(
        2, _ts()
    )
    sensor_state = b"".join(
        [
            encode_fixed32_float(1, 23.25),
            encode_fixed32_float(2, 46.5),
            encode_fixed32_float(3, 87.0),
            encode_varint_field(4, 4294967253),  # signed -43 encoded as uint32
            encode_bytes_field(5, _ts()),
        ]
    )
    sensor_rel = encode_bytes_field(
        1, indoor_unit_id.encode("utf-8")
    ) + encode_bytes_field(2, _ts())
    sensor_controls = encode_varint_field(1, 2) + encode_bytes_field(2, _ts())
    sensor_msg = b"".join(
        [
            encode_bytes_field(1, sensor_header),
            encode_bytes_field(2, sensor_attrs),
            encode_bytes_field(3, sensor_state),
            encode_bytes_field(4, sensor_rel),
            encode_bytes_field(5, sensor_controls),
        ]
    )

    hds = parse_get_home_datastore_system_response(
        encode_bytes_field(3, space_msg)
        + encode_bytes_field(9, iu_msg)
        + encode_bytes_field(18, sensor_msg)
    )

    sensor = hds.remote_sensors[remote_sensor_id]
    assert sensor.attributes is not None
    assert sensor.attributes.mac == "AA:BB:CC:DD:EE:FF"
    assert sensor.relationships is not None
    assert sensor.relationships.indoor_unit_id == indoor_unit_id
    assert sensor.controls is not None
    assert sensor.controls.control_mode == 2
    assert sensor.state is not None
    assert sensor.state.ambient_c == 23.25
    assert sensor.state.humidity_percent == 46.5
    assert sensor.state.battery_percent == 87.0
    assert sensor.state.rssi == -43
    assert hds.remote_sensors_by_indoor_unit[indoor_unit_id] == [sensor]


def test_space_and_indoor_unit_online_follow_five_minute_freshness_rule() -> None:
    system_id = "sys-1"
    space_id = "space-1"
    indoor_unit_id = "iu-1"
    fresh_ts = 1_000
    stale_ts = 100

    header = encode_bytes_field(1, space_id.encode("utf-8")) + encode_bytes_field(
        4, system_id.encode("utf-8")
    )
    settings = encode_bytes_field(1, b"Office") + encode_bytes_field(4, b"UTC")
    stale_space_state = encode_bytes_field(1, _ts(stale_ts))
    space_msg = b"".join(
        [
            encode_bytes_field(1, header),
            encode_bytes_field(3, settings),
            encode_bytes_field(5, stale_space_state),
        ]
    )

    iu_header = encode_bytes_field(
        1, indoor_unit_id.encode("utf-8")
    ) + encode_bytes_field(4, system_id.encode("utf-8"))
    iu_rel = encode_bytes_field(2, space_id.encode("utf-8"))
    fresh_iu_state = encode_bytes_field(1, _ts(fresh_ts))
    iu_msg = b"".join(
        [
            encode_bytes_field(1, iu_header),
            encode_bytes_field(2, iu_rel),
            encode_bytes_field(5, fresh_iu_state),
        ]
    )

    hds = parse_get_home_datastore_system_response(
        encode_bytes_field(3, space_msg) + encode_bytes_field(9, iu_msg)
    )
    assert hds.indoor_unit_is_online(indoor_unit_id, now_seconds=fresh_ts + 60)
    assert hds.space_is_online(space_id, now_seconds=fresh_ts + 60)
    assert not hds.indoor_unit_is_online(indoor_unit_id, now_seconds=fresh_ts + 301)
    assert not hds.space_is_online(space_id, now_seconds=fresh_ts + 301)
