from __future__ import annotations

import struct
from dataclasses import dataclass


class ProtoWireError(ValueError):
    pass


@dataclass(frozen=True)
class ProtoField:
    number: int
    wire_type: int
    value: int | bytes


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ProtoWireError("truncated varint")
        b = data[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, offset
        shift += 7
        if shift > 70:
            raise ProtoWireError("varint too long")


def decode_message(data: bytes) -> list[ProtoField]:
    fields: list[ProtoField] = []
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number == 0:
            raise ProtoWireError("field number 0 is invalid")

        if wire_type == 0:  # varint
            v, offset = _read_varint(data, offset)
            fields.append(ProtoField(field_number, wire_type, v))
        elif wire_type == 1:  # fixed64
            if offset + 8 > len(data):
                raise ProtoWireError("truncated fixed64")
            v = data[offset : offset + 8]
            offset += 8
            fields.append(ProtoField(field_number, wire_type, v))
        elif wire_type == 2:  # length-delimited
            length, offset = _read_varint(data, offset)
            if length < 0 or offset + length > len(data):
                raise ProtoWireError("truncated length-delimited field")
            v = data[offset : offset + length]
            offset += length
            fields.append(ProtoField(field_number, wire_type, v))
        elif wire_type == 5:  # fixed32
            if offset + 4 > len(data):
                raise ProtoWireError("truncated fixed32")
            v = data[offset : offset + 4]
            offset += 4
            fields.append(ProtoField(field_number, wire_type, v))
        else:
            raise ProtoWireError(f"unsupported wire type: {wire_type}")
    return fields


def _encode_varint(value: int) -> bytes:
    if value < 0:
        raise ProtoWireError("negative varint not supported")
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def encode_key(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def encode_length_delimited(payload: bytes) -> bytes:
    return _encode_varint(len(payload)) + payload


def encode_bytes_field(field_number: int, payload: bytes) -> bytes:
    return encode_key(field_number, 2) + encode_length_delimited(payload)


def encode_string(field_number: int, value: str) -> bytes:
    return encode_bytes_field(field_number, value.encode("utf-8"))


def encode_varint_field(field_number: int, value: int) -> bytes:
    return encode_key(field_number, 0) + _encode_varint(value)


def encode_fixed32_float(field_number: int, value: float) -> bytes:
    return encode_key(field_number, 5) + struct.pack("<f", float(value))


def encode_fixed64_double(field_number: int, value: float) -> bytes:
    return encode_key(field_number, 1) + struct.pack("<d", float(value))


def fixed32_to_float(value: bytes) -> float:
    if len(value) != 4:
        raise ProtoWireError("fixed32 must be 4 bytes")
    return struct.unpack("<f", value)[0]


def fixed64_to_double(value: bytes) -> float:
    if len(value) != 8:
        raise ProtoWireError("fixed64 must be 8 bytes")
    return struct.unpack("<d", value)[0]


def get_first(fields: list[ProtoField], *, number: int, wire_type: int | None = None) -> ProtoField | None:
    for f in fields:
        if f.number != number:
            continue
        if wire_type is not None and f.wire_type != wire_type:
            continue
        return f
    return None


def get_all(fields: list[ProtoField], *, number: int, wire_type: int | None = None) -> list[ProtoField]:
    out: list[ProtoField] = []
    for f in fields:
        if f.number != number:
            continue
        if wire_type is not None and f.wire_type != wire_type:
            continue
        out.append(f)
    return out
