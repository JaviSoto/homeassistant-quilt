from __future__ import annotations

import math

import pytest

from custom_components.quilt.proto_wire import (
    ProtoWireError,
    encode_bytes_field,
    encode_fixed32_float,
    encode_fixed64_double,
    encode_string,
    encode_varint_field,
    fixed64_to_double,
)
from custom_components.quilt.quilt_parse import parse_get_energy_metrics_response


def _ts(seconds: int, nanos: int = 0) -> bytes:
    return encode_varint_field(1, seconds) + encode_varint_field(2, nanos)


def test_fixed64_to_double_roundtrip() -> None:
    raw = encode_fixed64_double(3, 1.25)
    # Strip the key and read fixed64 bytes directly.
    assert raw[:1]  # has key
    v = fixed64_to_double(raw[1:])
    assert abs(v - 1.25) < 1e-12


def test_fixed64_to_double_rejects_wrong_length() -> None:
    with pytest.raises(ProtoWireError):
        fixed64_to_double(b"\x00\x00")


def test_parse_get_energy_metrics_response_basic() -> None:
    bucket1 = b"".join(
        [
            encode_bytes_field(1, _ts(1700000000, 0)),
            encode_varint_field(2, 1),  # COMPLETE
            encode_fixed64_double(3, 0.5),
        ]
    )
    bucket2 = b"".join(
        [
            encode_bytes_field(1, _ts(1700003600, 0)),
            encode_varint_field(2, 2),  # INCOMPLETE
            encode_fixed64_double(3, 1.25),
        ]
    )
    space_metrics = b"".join(
        [
            encode_string(1, "space-1"),
            encode_varint_field(2, 1),  # HOURLY
            encode_bytes_field(3, bucket1),
            encode_bytes_field(3, bucket2),
        ]
    )
    resp = encode_bytes_field(1, space_metrics)

    parsed = parse_get_energy_metrics_response(resp)
    assert len(parsed) == 1
    m = parsed[0]
    assert m.space_id == "space-1"
    assert m.bucket_time_resolution == 1
    assert len(m.energy_buckets) == 2
    assert abs(m.energy_buckets[0].energy_usage_kwh - 0.5) < 1e-12
    assert abs(m.energy_buckets[1].energy_usage_kwh - 1.25) < 1e-12


def test_parse_get_energy_metrics_response_seconds_only_timestamp_and_float32_energy() -> None:
    bucket = b"".join(
        [
            encode_bytes_field(1, encode_varint_field(1, 1700000000)),  # Timestamp { seconds=... } only
            encode_varint_field(2, 1),
            encode_fixed32_float(3, 0.75),
        ]
    )
    space_metrics = b"".join([encode_string(1, "space-1"), encode_varint_field(2, 1), encode_bytes_field(3, bucket)])
    resp = encode_bytes_field(1, space_metrics)
    parsed = parse_get_energy_metrics_response(resp)
    b = parsed[0].energy_buckets[0]
    assert b.start_time.seconds == 1700000000
    assert b.start_time.nanos == 0
    assert abs(b.energy_usage_kwh - 0.75) < 1e-6


def test_parse_get_energy_metrics_response_nan_is_zero() -> None:
    bucket = b"".join(
        [
            encode_bytes_field(1, _ts(1700000000, 0)),
            encode_varint_field(2, 1),
            encode_fixed64_double(3, math.nan),
        ]
    )
    space_metrics = b"".join([encode_string(1, "space-1"), encode_varint_field(2, 1), encode_bytes_field(3, bucket)])
    resp = encode_bytes_field(1, space_metrics)
    parsed = parse_get_energy_metrics_response(resp)
    assert parsed[0].energy_buckets[0].energy_usage_kwh == 0.0
