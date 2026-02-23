from __future__ import annotations

import pytest

from custom_components.quilt.proto_wire import ProtoWireError, decode_message, encode_key, fixed32_to_float


def test_decode_rejects_field_number_zero() -> None:
    # key with field_number=0, wire_type=0
    with pytest.raises(ProtoWireError):
        decode_message(b"\x00")


def test_decode_truncated_fixed32() -> None:
    # field 1 fixed32 -> key=0x0d, then only 1 byte
    with pytest.raises(ProtoWireError):
        decode_message(b"\x0d\x00")


def test_decode_truncated_varint() -> None:
    # key is fine, but value varint never terminates
    with pytest.raises(ProtoWireError):
        decode_message(b"\x08\x80")


def test_encode_negative_varint_rejected() -> None:
    from custom_components.quilt.proto_wire import encode_varint_field

    with pytest.raises(ProtoWireError):
        encode_varint_field(1, -1)


def test_decode_varint_too_long() -> None:
    # A varint that never terminates and exceeds the max shift threshold.
    with pytest.raises(ProtoWireError):
        decode_message(b"\x80" * 11)


def test_decode_truncated_fixed64() -> None:
    # field 1 fixed64 -> key=0x09, then only 2 bytes
    with pytest.raises(ProtoWireError):
        decode_message(b"\x09\x01\x02")


def test_decode_truncated_length_delimited() -> None:
    # field 1 length-delimited -> key=0x0a, length=10, payload too short
    with pytest.raises(ProtoWireError):
        decode_message(b"\x0a\x0a\x00")


def test_decode_unsupported_wire_type() -> None:
    # field 1, wire_type=3 is reserved/unsupported
    with pytest.raises(ProtoWireError):
        decode_message(b"\x0b")


def test_fixed32_to_float_rejects_wrong_length() -> None:
    with pytest.raises(ProtoWireError):
        fixed32_to_float(b"\x00")
