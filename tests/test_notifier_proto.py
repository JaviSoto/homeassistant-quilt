from __future__ import annotations

from custom_components.quilt.notifier_proto import (
    SubscribeRequestType,
    decode_subscribe_response_summary,
    encode_publish_request,
    encode_subscribe_request,
    should_refresh_from_subscribe_response,
)
from custom_components.quilt.proto_wire import decode_message, encode_bytes_field, get_first


def test_encode_subscribe_request_variants() -> None:
    topics = {"a", "b"}
    r1 = encode_subscribe_request(SubscribeRequestType.APPEND, topics, variant="topics1_type2")
    m1 = decode_message(r1)
    assert [f.value.decode() for f in m1 if f.number == 1] == ["a", "b"]
    t = get_first(m1, number=2, wire_type=0)
    assert int(t.value) == SubscribeRequestType.APPEND

    r2 = encode_subscribe_request(SubscribeRequestType.REMOVE, topics, variant="type1_topics2")
    m2 = decode_message(r2)
    t2 = get_first(m2, number=1, wire_type=0)
    assert int(t2.value) == SubscribeRequestType.REMOVE
    try:
        encode_subscribe_request(0, topics, variant="nope")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_publish_request_allows_null_payload() -> None:
    req = encode_publish_request([("system/x/client_heartbeat", None)])
    m = decode_message(req)
    ev = get_first(m, number=1, wire_type=2)
    assert ev is not None


def test_should_refresh_from_subscribe_response() -> None:
    # Empty event (b'\\n\\x00') should not refresh.
    payload = encode_bytes_field(1, b"")
    assert should_refresh_from_subscribe_response(payload) is False

    # Notifier event should refresh.
    notifier_event = encode_bytes_field(1, encode_bytes_field(1, b"hds/space/x"))
    payload2 = encode_bytes_field(1, notifier_event)
    assert should_refresh_from_subscribe_response(payload2) is True
    s = decode_subscribe_response_summary(payload2)
    assert s["ok"] is True

    # Garbage should force refresh (defensive).
    assert should_refresh_from_subscribe_response(b"\xff") is True
    assert decode_subscribe_response_summary(b"\xff")["ok"] is False
