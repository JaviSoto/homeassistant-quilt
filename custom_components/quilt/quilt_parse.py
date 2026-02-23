from __future__ import annotations

from dataclasses import dataclass

from .proto_wire import ProtoWireError, decode_message, fixed32_to_float, fixed64_to_double, get_all, get_first


@dataclass(frozen=True)
class QuiltTimestamp:
    seconds: int
    nanos: int


@dataclass(frozen=True)
class QuiltSystemInfo:
    system_id: str
    name: str
    timezone: str


@dataclass(frozen=True)
class QuiltEnergyMetricBucket:
    start_time: QuiltTimestamp
    status: int | None
    energy_usage_kwh: float


@dataclass(frozen=True)
class QuiltSpaceEnergyMetrics:
    space_id: str
    bucket_time_resolution: int | None
    energy_buckets: list[QuiltEnergyMetricBucket]


@dataclass(frozen=True)
class QuiltSpaceHeader:
    space_id: str
    created: QuiltTimestamp | None
    updated: QuiltTimestamp | None
    system_id: str


@dataclass(frozen=True)
class QuiltIndoorUnitHeader:
    indoor_unit_id: str
    created: QuiltTimestamp | None
    updated: QuiltTimestamp | None
    system_id: str


@dataclass(frozen=True)
class QuiltSpaceControls:
    hvac_mode: int | None
    setpoint_c: float | None
    cooling_setpoint_c: float | None
    heating_setpoint_c: float | None
    updated: QuiltTimestamp | None
    unknown_field7: int | None
    comfort_setting_override: int | None
    comfort_setting_id: str | None


@dataclass(frozen=True)
class QuiltSpaceState:
    updated: QuiltTimestamp | None
    setpoint_c: float | None
    ambient_c: float | None
    hvac_state: int | None
    comfort_setting_id: str | None


@dataclass(frozen=True)
class QuiltSpaceSettings:
    name: str | None
    timezone: str | None


@dataclass(frozen=True)
class QuiltSpace:
    header: QuiltSpaceHeader
    relationships_parent_space_id: str | None
    settings: QuiltSpaceSettings
    controls: QuiltSpaceControls
    state: QuiltSpaceState


@dataclass(frozen=True)
class QuiltIndoorUnitControls:
    updated: QuiltTimestamp | None
    light_color_code: int | None
    light_brightness: float | None
    light_animation: int | None
    fan_speed_mode: int | None
    fan_speed_percent: float | None
    louver_mode: int | None
    louver_fixed_position: float | None


@dataclass(frozen=True)
class QuiltIndoorUnitRelationships:
    space_id: str | None


@dataclass(frozen=True)
class QuiltIndoorUnit:
    header: QuiltIndoorUnitHeader
    relationships: QuiltIndoorUnitRelationships | None
    controls: QuiltIndoorUnitControls


@dataclass(frozen=True)
class QuiltHdsSystem:
    system_id: str | None
    spaces: dict[str, QuiltSpace]
    indoor_units: dict[str, QuiltIndoorUnit]
    indoor_units_by_space: dict[str, list[QuiltIndoorUnit]]
    comfort_settings: dict[str, "QuiltComfortSetting"]
    comfort_settings_by_space: dict[str, list["QuiltComfortSetting"]]
    topic_ids: dict[str, set[str]]

    def notifier_topics(self) -> set[str]:
        topics: set[str] = set()
        for topic_name, ids in self.topic_ids.items():
            for oid in ids:
                topics.add(f"hds/{topic_name}/{oid}")
        return topics


@dataclass(frozen=True)
class QuiltComfortSettingHeader:
    comfort_setting_id: str
    created: QuiltTimestamp | None
    updated: QuiltTimestamp | None
    system_id: str


@dataclass(frozen=True)
class QuiltComfortSettingAttributes:
    updated: QuiltTimestamp | None
    name: str | None
    fan_speed_mode: int | None
    fan_speed_percent: float | None
    heating_setpoint_c: float | None
    cooling_setpoint_c: float | None
    comfort_setting_type: int | None
    hvac_mode: int | None
    louver_mode: int | None
    louver_fixed_position: float | None


@dataclass(frozen=True)
class QuiltComfortSettingRelationships:
    updated: QuiltTimestamp | None
    space_id: str | None


@dataclass(frozen=True)
class QuiltComfortSetting:
    header: QuiltComfortSettingHeader
    attributes: QuiltComfortSettingAttributes
    relationships: QuiltComfortSettingRelationships | None


def _parse_timestamp(raw: bytes) -> QuiltTimestamp | None:
    try:
        fields = decode_message(raw)
    except ProtoWireError:
        return None
    sec = get_first(fields, number=1, wire_type=0)
    ns = get_first(fields, number=2, wire_type=0)
    if sec is None:
        return None
    return QuiltTimestamp(seconds=int(sec.value), nanos=int(ns.value) if ns is not None else 0)


def _parse_header(raw: bytes) -> QuiltSpaceHeader | None:
    fields = decode_message(raw)
    sid = get_first(fields, number=1, wire_type=2)
    created = get_first(fields, number=2, wire_type=2)
    updated = get_first(fields, number=3, wire_type=2)
    system_id = get_first(fields, number=4, wire_type=2)
    if sid is None or system_id is None:
        return None
    return QuiltSpaceHeader(
        space_id=sid.value.decode("utf-8"),
        created=_parse_timestamp(created.value) if created is not None else None,
        updated=_parse_timestamp(updated.value) if updated is not None else None,
        system_id=system_id.value.decode("utf-8"),
    )


def _parse_indoor_unit_header(raw: bytes) -> QuiltIndoorUnitHeader | None:
    # Matches the "header" message layout used across HDS objects:
    # 1: id string
    # 2: created Timestamp
    # 3: updated Timestamp
    # 4: system_id string
    fields = decode_message(raw)
    oid = get_first(fields, number=1, wire_type=2)
    created = get_first(fields, number=2, wire_type=2)
    updated = get_first(fields, number=3, wire_type=2)
    system_id = get_first(fields, number=4, wire_type=2)
    if oid is None or system_id is None:
        return None
    return QuiltIndoorUnitHeader(
        indoor_unit_id=oid.value.decode("utf-8"),
        created=_parse_timestamp(created.value) if created is not None else None,
        updated=_parse_timestamp(updated.value) if updated is not None else None,
        system_id=system_id.value.decode("utf-8"),
    )


def _parse_settings(raw: bytes) -> QuiltSpaceSettings:
    fields = decode_message(raw)
    name = get_first(fields, number=1, wire_type=2)
    tz = get_first(fields, number=4, wire_type=2)
    return QuiltSpaceSettings(
        name=name.value.decode("utf-8") if name is not None else None,
        timezone=tz.value.decode("utf-8") if tz is not None else None,
    )


def _parse_controls(raw: bytes) -> QuiltSpaceControls:
    fields = decode_message(raw)
    hvac_mode = get_first(fields, number=1, wire_type=0)
    setpoint = get_first(fields, number=2, wire_type=5)
    updated = get_first(fields, number=3, wire_type=2)
    cooling = get_first(fields, number=4, wire_type=5)
    heating = get_first(fields, number=5, wire_type=5)
    unknown7 = get_first(fields, number=7, wire_type=0)
    override = get_first(fields, number=8, wire_type=0)
    comfort_id = get_first(fields, number=9, wire_type=2)
    return QuiltSpaceControls(
        hvac_mode=int(hvac_mode.value) if hvac_mode is not None else None,
        setpoint_c=fixed32_to_float(setpoint.value) if setpoint is not None else None,
        cooling_setpoint_c=fixed32_to_float(cooling.value) if cooling is not None else None,
        heating_setpoint_c=fixed32_to_float(heating.value) if heating is not None else None,
        updated=_parse_timestamp(updated.value) if updated is not None else None,
        unknown_field7=int(unknown7.value) if unknown7 is not None else None,
        comfort_setting_override=int(override.value) if override is not None else None,
        comfort_setting_id=comfort_id.value.decode("utf-8") if comfort_id is not None else None,
    )


def _parse_state(raw: bytes) -> QuiltSpaceState:
    fields = decode_message(raw)
    updated = get_first(fields, number=1, wire_type=2)
    setpoint = get_first(fields, number=2, wire_type=5)
    ambient = get_first(fields, number=3, wire_type=5)
    hvac_state = get_first(fields, number=4, wire_type=0)
    comfort_id = get_first(fields, number=5, wire_type=2)
    return QuiltSpaceState(
        updated=_parse_timestamp(updated.value) if updated is not None else None,
        setpoint_c=fixed32_to_float(setpoint.value) if setpoint is not None else None,
        ambient_c=fixed32_to_float(ambient.value) if ambient is not None else None,
        hvac_state=int(hvac_state.value) if hvac_state is not None else None,
        comfort_setting_id=comfort_id.value.decode("utf-8") if comfort_id is not None else None,
    )


def _parse_relationships(raw: bytes) -> str | None:
    fields = decode_message(raw)
    parent = get_first(fields, number=2, wire_type=2)
    return parent.value.decode("utf-8") if parent is not None else None


def _parse_indoor_unit_relationships(raw: bytes) -> QuiltIndoorUnitRelationships | None:
    # Observed in GetHomeDatastoreSystem responses:
    # relationships {
    #   2: spaceIdString (string)
    #   ...
    # }
    fields = decode_message(raw)
    space_id = get_first(fields, number=2, wire_type=2)
    return QuiltIndoorUnitRelationships(space_id=space_id.value.decode("utf-8") if space_id is not None else None)


def _parse_indoor_unit_controls(raw: bytes) -> QuiltIndoorUnitControls:
    # Observed (from GetHomeDatastoreSystem and UpdateIndoorUnit captures):
    # controls {
    #   3: lightColorCode (u32 varint)
    #   4: lightBrightness (fixed32 float 0..1)
    #   5: fanSpeedMode (enum varint)
    #   6: fanSpeedPercent (fixed32 float 0..1)
    #   7: updatedTimestamp (Timestamp)
    #   10: louverMode (enum varint)
    #   11: louverFixedPosition (fixed32 float 0..1)
    #   12: lightAnimation (enum varint)
    # }
    fields = decode_message(raw)
    color = get_first(fields, number=3, wire_type=0)
    brightness = get_first(fields, number=4, wire_type=5)
    fan_mode = get_first(fields, number=5, wire_type=0)
    fan_percent = get_first(fields, number=6, wire_type=5)
    updated = get_first(fields, number=7, wire_type=2)
    louver_mode = get_first(fields, number=10, wire_type=0)
    louver_pos = get_first(fields, number=11, wire_type=5)
    anim = get_first(fields, number=12, wire_type=0)
    return QuiltIndoorUnitControls(
        updated=_parse_timestamp(updated.value) if updated is not None else None,
        light_color_code=int(color.value) if color is not None else None,
        light_brightness=fixed32_to_float(brightness.value) if brightness is not None else None,
        light_animation=int(anim.value) if anim is not None else None,
        fan_speed_mode=int(fan_mode.value) if fan_mode is not None else None,
        fan_speed_percent=fixed32_to_float(fan_percent.value) if fan_percent is not None else None,
        louver_mode=int(louver_mode.value) if louver_mode is not None else None,
        louver_fixed_position=fixed32_to_float(louver_pos.value) if louver_pos is not None else None,
    )


def _parse_comfort_setting_header(raw: bytes) -> QuiltComfortSettingHeader | None:
    fields = decode_message(raw)
    cid = get_first(fields, number=1, wire_type=2)
    created = get_first(fields, number=2, wire_type=2)
    updated = get_first(fields, number=3, wire_type=2)
    system_id = get_first(fields, number=4, wire_type=2)
    if cid is None or system_id is None:
        return None
    return QuiltComfortSettingHeader(
        comfort_setting_id=cid.value.decode("utf-8"),
        created=_parse_timestamp(created.value) if created is not None else None,
        updated=_parse_timestamp(updated.value) if updated is not None else None,
        system_id=system_id.value.decode("utf-8"),
    )


def _parse_comfort_setting_attributes(raw: bytes) -> QuiltComfortSettingAttributes:
    fields = decode_message(raw)
    updated = get_first(fields, number=1, wire_type=2)
    name = get_first(fields, number=2, wire_type=2)
    fan_mode = get_first(fields, number=3, wire_type=0)
    fan_percent = get_first(fields, number=4, wire_type=5)
    heat = get_first(fields, number=5, wire_type=5)
    cool = get_first(fields, number=6, wire_type=5)
    cs_type = get_first(fields, number=7, wire_type=0)
    hvac_mode = get_first(fields, number=8, wire_type=0)
    louver_mode = get_first(fields, number=9, wire_type=0)
    louver_fixed = get_first(fields, number=10, wire_type=5)
    return QuiltComfortSettingAttributes(
        updated=_parse_timestamp(updated.value) if updated is not None else None,
        name=name.value.decode("utf-8") if name is not None else None,
        fan_speed_mode=int(fan_mode.value) if fan_mode is not None else None,
        fan_speed_percent=fixed32_to_float(fan_percent.value) if fan_percent is not None else None,
        heating_setpoint_c=fixed32_to_float(heat.value) if heat is not None else None,
        cooling_setpoint_c=fixed32_to_float(cool.value) if cool is not None else None,
        comfort_setting_type=int(cs_type.value) if cs_type is not None else None,
        hvac_mode=int(hvac_mode.value) if hvac_mode is not None else None,
        louver_mode=int(louver_mode.value) if louver_mode is not None else None,
        louver_fixed_position=fixed32_to_float(louver_fixed.value) if louver_fixed is not None else None,
    )


def _parse_comfort_setting_relationships(raw: bytes) -> QuiltComfortSettingRelationships | None:
    try:
        fields = decode_message(raw)
    except ProtoWireError:
        return None
    updated = get_first(fields, number=1, wire_type=2)
    space_id = get_first(fields, number=2, wire_type=2)
    return QuiltComfortSettingRelationships(
        updated=_parse_timestamp(updated.value) if updated is not None else None,
        space_id=space_id.value.decode("utf-8") if space_id is not None else None,
    )


def parse_list_systems_response(data: bytes) -> list[QuiltSystemInfo]:
    # Observed structure (decode_raw):
    # 1 { 1: <uuid> 2: <name> 3: <tz> }
    out: list[QuiltSystemInfo] = []
    top = decode_message(data)
    for f in get_all(top, number=1, wire_type=2):
        msg = decode_message(f.value)
        sid = get_first(msg, number=1, wire_type=2)
        name = get_first(msg, number=2, wire_type=2)
        tz = get_first(msg, number=3, wire_type=2)
        if sid is None:
            continue
        out.append(
            QuiltSystemInfo(
                system_id=sid.value.decode("utf-8"),
                name=name.value.decode("utf-8") if name is not None else sid.value.decode("utf-8"),
                timezone=tz.value.decode("utf-8") if tz is not None else "",
            )
        )
    return out


def parse_get_home_datastore_system_response(data: bytes) -> QuiltHdsSystem:
    # Observed structure:
    # - repeated field 3 = Space message
    # Each Space message:
    # 1 { header: id + timestamps + system_id }
    # 2 { relationships: parentSpaceId? }
    # 3 { settings: name, timezone, ... }
    # 4 { controls: hvacMode, setpoints, comfortSetting info }
    # 5 { state: ambient, hvacState, ... }
    top = decode_message(data)
    spaces: dict[str, QuiltSpace] = {}
    indoor_units: dict[str, QuiltIndoorUnit] = {}
    indoor_units_by_space: dict[str, list[QuiltIndoorUnit]] = {}
    comfort_settings: dict[str, QuiltComfortSetting] = {}
    comfort_settings_by_space: dict[str, list[QuiltComfortSetting]] = {}
    system_id_box: list[str | None] = [None]
    topic_ids: dict[str, set[str]] = {}

    # Field number mapping inferred from the Quilt iOS app's HomeDatastoreSystem model.
    # Some systems omit certain lists, so parsing is best-effort.
    # Topic names mirror `HDSObjectType.topicName` from the app sources.
    field_to_topic: dict[int, str] = {
        3: "space",
        5: "outdoor_unit_hardware",
        6: "outdoor_unit",
        7: "quilt_smart_module",
        # On systems we've captured (Jan 2026), field 9 objects contain full IndoorUnit
        # relationships + controls, and field 8 contains the smaller "hardware" payload.
        # Keeping this mapping correct is important for notifier subscriptions.
        8: "indoor_unit_hardware",
        9: "indoor_unit",
        10: "controller",
        11: "controller_remote_sensor",
        12: "controller_hardware",
        13: "comfort_setting",
        14: "schedule_day",
        15: "schedule_week",
        16: "software_update_info",
        17: "location",
        18: "remote_sensor",
    }

    for field_no, topic_name in field_to_topic.items():
        for obj_f in get_all(top, number=field_no, wire_type=2):
            try:
                obj_msg = decode_message(obj_f.value)
            except ProtoWireError:
                continue
            header_f = get_first(obj_msg, number=1, wire_type=2)
            if header_f is None:
                continue
            try:
                header_msg = decode_message(header_f.value)
            except ProtoWireError:
                continue
            oid_f = get_first(header_msg, number=1, wire_type=2)
            sys_f = get_first(header_msg, number=4, wire_type=2)
            if oid_f is None:
                continue
            oid = oid_f.value.decode("utf-8")
            topic_ids.setdefault(topic_name, set()).add(oid)
            if system_id_box[0] is None and sys_f is not None:
                system_id_box[0] = sys_f.value.decode("utf-8")

    for f in get_all(top, number=9, wire_type=2):
        # IndoorUnit objects (used for thermostat/dial light controls).
        try:
            iu_fields = decode_message(f.value)
        except ProtoWireError:
            continue
        header_f = get_first(iu_fields, number=1, wire_type=2)
        rel_f = get_first(iu_fields, number=2, wire_type=2)
        controls_f = get_first(iu_fields, number=4, wire_type=2)

        header = _parse_indoor_unit_header(header_f.value) if header_f is not None else None
        if header is None:
            continue
        system_id_box[0] = system_id_box[0] or header.system_id
        rel = _parse_indoor_unit_relationships(rel_f.value) if rel_f is not None else None
        controls = (
            _parse_indoor_unit_controls(controls_f.value)
            if controls_f is not None
            else QuiltIndoorUnitControls(
                updated=None,
                light_color_code=None,
                light_brightness=None,
                light_animation=None,
                fan_speed_mode=None,
                fan_speed_percent=None,
                louver_mode=None,
                louver_fixed_position=None,
            )
        )

        iu = QuiltIndoorUnit(header=header, relationships=rel, controls=controls)
        indoor_units[header.indoor_unit_id] = iu
        if rel is not None and rel.space_id is not None:
            indoor_units_by_space.setdefault(rel.space_id, []).append(iu)
        topic_ids.setdefault("indoor_unit", set()).add(header.indoor_unit_id)
    for f in get_all(top, number=3, wire_type=2):
        space_fields = decode_message(f.value)
        header_f = get_first(space_fields, number=1, wire_type=2)
        settings_f = get_first(space_fields, number=3, wire_type=2)
        controls_f = get_first(space_fields, number=4, wire_type=2)
        state_f = get_first(space_fields, number=5, wire_type=2)
        rel_f = get_first(space_fields, number=2, wire_type=2)

        header = _parse_header(header_f.value) if header_f is not None else None
        if header is None:
            continue
        system_id_box[0] = system_id_box[0] or header.system_id
        settings = _parse_settings(settings_f.value) if settings_f is not None else QuiltSpaceSettings(None, None)
        controls = (
            _parse_controls(controls_f.value)
            if controls_f is not None
            else QuiltSpaceControls(None, None, None, None, None, None, None, None)
        )
        state = _parse_state(state_f.value) if state_f is not None else QuiltSpaceState(None, None, None, None, None)
        parent_space_id = _parse_relationships(rel_f.value) if rel_f is not None else None

        spaces[header.space_id] = QuiltSpace(
            header=header,
            relationships_parent_space_id=parent_space_id,
            settings=settings,
            controls=controls,
            state=state,
        )
        topic_ids.setdefault("space", set()).add(header.space_id)

    for f in get_all(top, number=13, wire_type=2):
        cs_fields = decode_message(f.value)
        header_f = get_first(cs_fields, number=1, wire_type=2)
        attrs_f = get_first(cs_fields, number=2, wire_type=2)
        rel_f = get_first(cs_fields, number=3, wire_type=2)

        header = _parse_comfort_setting_header(header_f.value) if header_f is not None else None
        if header is None or attrs_f is None:
            continue
        system_id_box[0] = system_id_box[0] or header.system_id
        attrs = _parse_comfort_setting_attributes(attrs_f.value)
        rel = _parse_comfort_setting_relationships(rel_f.value) if rel_f is not None else None
        cs = QuiltComfortSetting(header=header, attributes=attrs, relationships=rel)
        comfort_settings[header.comfort_setting_id] = cs
        if rel is not None and rel.space_id is not None:
            comfort_settings_by_space.setdefault(rel.space_id, []).append(cs)
        topic_ids.setdefault("comfort_setting", set()).add(header.comfort_setting_id)

    return QuiltHdsSystem(
        system_id=system_id_box[0],
        spaces=spaces,
        indoor_units=indoor_units,
        indoor_units_by_space=indoor_units_by_space,
        comfort_settings=comfort_settings,
        comfort_settings_by_space=comfort_settings_by_space,
        topic_ids=topic_ids,
    )


def parse_get_energy_metrics_response(data: bytes) -> list[QuiltSpaceEnergyMetrics]:
    # Inferred from Quilt iOS app source:
    # - GetEnergyMetricsResponse:
    #   1: repeated SpaceEnergyMetrics
    # - SpaceEnergyMetrics:
    #   1: spaceID (string)
    #   2: bucketTimeResolution (enum)
    #   3: repeated EnergyMetricBucket
    # - EnergyMetricBucket:
    #   1: startTime (Timestamp)
    #   2: status (enum)
    #   3: energyUsage (double)  # kWh
    top = decode_message(data)
    out: list[QuiltSpaceEnergyMetrics] = []

    for f in get_all(top, number=1, wire_type=2):
        fields = decode_message(f.value)
        space_id_f = get_first(fields, number=1, wire_type=2)
        res_f = get_first(fields, number=2, wire_type=0)
        if space_id_f is None:
            continue

        buckets: list[QuiltEnergyMetricBucket] = []
        for b in get_all(fields, number=3, wire_type=2):
            b_fields = decode_message(b.value)
            ts_f = get_first(b_fields, number=1, wire_type=2)
            status_f = get_first(b_fields, number=2, wire_type=0)
            energy_f = get_first(b_fields, number=3, wire_type=1) or get_first(b_fields, number=3, wire_type=5)

            ts = _parse_timestamp(ts_f.value) if ts_f is not None else None
            if ts is None or energy_f is None:
                continue

            if energy_f.wire_type == 1:
                energy = float(fixed64_to_double(energy_f.value))
            else:
                energy = float(fixed32_to_float(energy_f.value))
            if energy != energy:  # NaN guard (matches app workaround)
                energy = 0.0

            buckets.append(
                QuiltEnergyMetricBucket(
                    start_time=ts,
                    status=int(status_f.value) if status_f is not None else None,
                    energy_usage_kwh=energy,
                )
            )

        out.append(
            QuiltSpaceEnergyMetrics(
                space_id=space_id_f.value.decode("utf-8"),
                bucket_time_resolution=int(res_f.value) if res_f is not None else None,
                energy_buckets=buckets,
            )
        )

    return out
