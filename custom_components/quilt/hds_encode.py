from __future__ import annotations

import time

from .proto_wire import encode_bytes_field, encode_fixed32_float, encode_varint_field
from .quilt_parse import QuiltComfortSetting, QuiltIndoorUnit, QuiltSpace


def _now_timestamp_fields() -> bytes:
    # protobuf Timestamp: seconds=1 (varint), nanos=2 (varint)
    now = time.time()
    seconds = int(now)
    nanos = int((now - seconds) * 1_000_000_000)
    return encode_varint_field(1, seconds) + encode_varint_field(2, nanos)


def _encode_space_header(space: QuiltSpace) -> bytes:
    # Observed in mitm decode_raw:
    # header {
    #   1: space_id (string)
    #   2: createdTimestamp (Timestamp)
    #   4: system_id (string)
    # }
    header = b""
    header += encode_bytes_field(1, space.header.space_id.encode("utf-8"))
    if space.header.created is not None:
        created = encode_varint_field(1, space.header.created.seconds) + encode_varint_field(2, space.header.created.nanos)
        header += encode_bytes_field(2, created)
    header += encode_bytes_field(4, space.header.system_id.encode("utf-8"))
    return header


def _encode_space_controls(
    *,
    hvac_mode: int,
    setpoint_c: float,
    cooling_setpoint_c: float,
    heating_setpoint_c: float,
    comfort_setting_override: int | None,
    comfort_setting_id: str | None,
) -> bytes:
    # Observed structure:
    # controls {
    #   1: hvacMode (enum varint)
    #   2: setpointTemperatureC (fixed32 float)
    #   3: updatedTimestamp (Timestamp)
    #   4: coolingTemperatureSetpointC (fixed32 float)
    #   5: heatingTemperatureSetpointC (fixed32 float)
    #   7: unknown (varint; observed as 0)
    #   8: comfortSettingOverride (enum varint)
    #   9: comfortSettingIdString (string) [optional]
    # }
    controls = b""
    controls += encode_varint_field(1, hvac_mode)
    controls += encode_fixed32_float(2, setpoint_c)
    controls += encode_bytes_field(3, _now_timestamp_fields())
    controls += encode_fixed32_float(4, cooling_setpoint_c)
    controls += encode_fixed32_float(5, heating_setpoint_c)
    # The official app always seems to send field 7 = 0 (based on our captures).
    controls += encode_varint_field(7, 0)
    if comfort_setting_override is not None:
        controls += encode_varint_field(8, comfort_setting_override)
    if comfort_setting_id is not None:
        controls += encode_bytes_field(9, comfort_setting_id.encode("utf-8"))
    return controls


def encode_space_diff(
    space: QuiltSpace,
    *,
    hvac_mode: int,
    setpoint_c: float,
    heat_c: float,
    cool_c: float,
    comfort_setting_id: str | None,
    comfort_setting_override: int,
) -> bytes:
    # Space message (diff):
    # 1: header (message)
    # 4: controls (message)
    msg = b""
    msg += encode_bytes_field(1, _encode_space_header(space))
    controls = _encode_space_controls(
        hvac_mode=hvac_mode,
        setpoint_c=setpoint_c,
        cooling_setpoint_c=cool_c,
        heating_setpoint_c=heat_c,
        comfort_setting_override=comfort_setting_override,
        comfort_setting_id=comfort_setting_id,
    )
    msg += encode_bytes_field(4, controls)
    return msg


def _encode_timestamp(ts_seconds: int, ts_nanos: int) -> bytes:
    return encode_varint_field(1, ts_seconds) + encode_varint_field(2, ts_nanos)


def _now_timestamp_message() -> bytes:
    now = time.time()
    seconds = int(now)
    nanos = int((now - seconds) * 1_000_000_000)
    return _encode_timestamp(seconds, nanos)


def _encode_comfort_setting_header(cs: QuiltComfortSetting) -> bytes:
    # Observed (decode_raw) header:
    # 1: id (string)
    # 2: createdTimestamp (Timestamp)
    # 4: systemId (string)
    header = b""
    header += encode_bytes_field(1, cs.header.comfort_setting_id.encode("utf-8"))
    if cs.header.created is not None:
        header += encode_bytes_field(2, _encode_timestamp(cs.header.created.seconds, cs.header.created.nanos))
    header += encode_bytes_field(4, cs.header.system_id.encode("utf-8"))
    return header


def _encode_comfort_setting_attributes(
    cs: QuiltComfortSetting,
    *,
    heat_c: float,
    cool_c: float,
    fan_speed_mode: int | None = None,
    fan_speed_percent: float | None = None,
    hvac_mode: int | None = None,
    louver_mode: int | None = None,
    louver_fixed_position: float | None = None,
) -> bytes:
    # Observed (decode_raw) attributes:
    # 1: updatedTimestamp (Timestamp)
    # 2: name (string)
    # 3: fanSpeedMode (enum varint)
    # 4: fanSpeedPercent (fixed32 float)
    # 5: heatingTemperatureSetpointC (fixed32 float)
    # 6: coolingTemperatureSetpointC (fixed32 float)
    # 7: comfortSettingType (enum varint)
    # 8: hvacMode (enum varint)
    # 9: louverMode (enum varint)
    # 10: louverFixedPosition (fixed32 float)
    a = cs.attributes
    msg = b""
    msg += encode_bytes_field(1, _now_timestamp_message())
    if a.name is not None:
        msg += encode_bytes_field(2, a.name.encode("utf-8"))
    mode = fan_speed_mode if fan_speed_mode is not None else a.fan_speed_mode
    if mode is not None:
        msg += encode_varint_field(3, mode)
    percent = fan_speed_percent if fan_speed_percent is not None else a.fan_speed_percent
    if percent is not None:
        msg += encode_fixed32_float(4, percent)
    msg += encode_fixed32_float(5, heat_c)
    msg += encode_fixed32_float(6, cool_c)
    if a.comfort_setting_type is not None:
        msg += encode_varint_field(7, a.comfort_setting_type)
    hvac = hvac_mode if hvac_mode is not None else a.hvac_mode
    if hvac is not None:
        msg += encode_varint_field(8, hvac)
    lm = louver_mode if louver_mode is not None else a.louver_mode
    if lm is not None:
        msg += encode_varint_field(9, lm)
    lfp = louver_fixed_position if louver_fixed_position is not None else a.louver_fixed_position
    if lfp is not None:
        msg += encode_fixed32_float(10, lfp)
    return msg


def encode_update_comfort_setting_request(
    cs: QuiltComfortSetting,
    *,
    heat_c: float,
    cool_c: float,
    fan_speed_mode: int | None = None,
    fan_speed_percent: float | None = None,
    hvac_mode: int | None = None,
    louver_mode: int | None = None,
    louver_fixed_position: float | None = None,
) -> bytes:
    # UpdateComfortSetting request wrapper:
    # 1 { header }
    # 2 { attributes }
    msg = b""
    msg += encode_bytes_field(1, _encode_comfort_setting_header(cs))
    msg += encode_bytes_field(
        2,
        _encode_comfort_setting_attributes(
            cs,
            heat_c=heat_c,
            cool_c=cool_c,
            fan_speed_mode=fan_speed_mode,
            fan_speed_percent=fan_speed_percent,
            hvac_mode=hvac_mode,
            louver_mode=louver_mode,
            louver_fixed_position=louver_fixed_position,
        ),
    )
    return msg


def _encode_indoor_unit_header(iu: QuiltIndoorUnit) -> bytes:
    # Mirrors other HDS object headers:
    # 1: id string
    # 2: created Timestamp (optional)
    # 4: systemId string
    header = b""
    header += encode_bytes_field(1, iu.header.indoor_unit_id.encode("utf-8"))
    if iu.header.created is not None:
        header += encode_bytes_field(2, _encode_timestamp(iu.header.created.seconds, iu.header.created.nanos))
    header += encode_bytes_field(4, iu.header.system_id.encode("utf-8"))
    return header


def _encode_indoor_unit_controls(
    iu: QuiltIndoorUnit,
    *,
    light_brightness: float | None = None,
    light_color_code: int | None = None,
    light_animation: int | None = None,
    louver_mode: int | None = None,
    louver_fixed_position: float | None = None,
) -> bytes:
    c = iu.controls
    msg = b""
    # These field numbers were observed in captures from the official app.
    if light_color_code is None:
        light_color_code = c.light_color_code
    if light_color_code is not None:
        msg += encode_varint_field(3, int(light_color_code))

    if light_brightness is None:
        light_brightness = c.light_brightness
    if light_brightness is not None:
        msg += encode_fixed32_float(4, float(light_brightness))

    if c.fan_speed_mode is not None:
        msg += encode_varint_field(5, int(c.fan_speed_mode))
    if c.fan_speed_percent is not None:
        msg += encode_fixed32_float(6, float(c.fan_speed_percent))

    msg += encode_bytes_field(7, _now_timestamp_message())

    lm = louver_mode if louver_mode is not None else c.louver_mode
    if lm is not None:
        msg += encode_varint_field(10, int(lm))

    lfp = louver_fixed_position if louver_fixed_position is not None else c.louver_fixed_position
    if lfp is not None:
        msg += encode_fixed32_float(11, float(lfp))

    if light_animation is None:
        light_animation = c.light_animation
    if light_animation is not None:
        msg += encode_varint_field(12, int(light_animation))

    return msg


def encode_update_indoor_unit_request(
    iu: QuiltIndoorUnit,
    *,
    light_brightness: float | None = None,
    light_color_code: int | None = None,
    light_animation: int | None = None,
    louver_mode: int | None = None,
    louver_fixed_position: float | None = None,
) -> bytes:
    # UpdateIndoorUnit diff payload (inner message) observed from the app:
    # 1 { header }
    # 4 { controls }
    msg = b""
    msg += encode_bytes_field(1, _encode_indoor_unit_header(iu))
    msg += encode_bytes_field(
        4,
        _encode_indoor_unit_controls(
            iu,
            light_brightness=light_brightness,
            light_color_code=light_color_code,
            light_animation=light_animation,
            louver_mode=louver_mode,
            louver_fixed_position=louver_fixed_position,
        ),
    )
    return msg
