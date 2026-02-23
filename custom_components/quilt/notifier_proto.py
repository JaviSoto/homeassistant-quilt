from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .proto_wire import decode_message, encode_bytes_field, encode_varint_field, get_all, get_first


class SubscribeRequestType:
    # Kotlin enum order from the Quilt app sources: APPEND, REMOVE
    APPEND: Final[int] = 0
    REMOVE: Final[int] = 1


@dataclass(frozen=True)
class QuiltNotifierConfig:
    min_refresh_interval_seconds: float = 1.0


def encode_publish_request(events: list[tuple[str, bytes | None]]) -> bytes:
    """Best-effort encoder for NotifierService.Publish.

    Observed from the Quilt app sources:
    - Publish is used for `system/<system_id>/client_heartbeat` where payload is null.

    We treat the request as:
      message PublishRequest { repeated NotificationEvent events = 1; }
      message NotificationEvent { string topic = 1; bytes payload = 2; ... }

    Payload is optional and omitted when None.
    """
    msg = b""
    for topic, payload in events:
        ev = encode_bytes_field(1, topic.encode("utf-8"))
        if payload is not None:
            ev += encode_bytes_field(2, payload)
        msg += encode_bytes_field(1, ev)
    return msg


def decode_subscribe_response_summary(payload: bytes) -> dict[str, Any]:
    """Best-effort decode of SubscribeResponse for debugging/refresh gating."""
    try:
        top = decode_message(payload)
    except Exception:
        return {"ok": False, "error": "decode_failed", "len": len(payload)}

    event = get_first(top, number=1, wire_type=2)
    if event is None:
        return {"ok": True, "event": None}

    try:
        event_msg = decode_message(event.value)
    except Exception:
        return {"ok": True, "event": {"decode_failed": True, "len": len(event.value)}}

    notifier = get_all(event_msg, number=1, wire_type=2)
    control = get_all(event_msg, number=2, wire_type=2)
    system = get_all(event_msg, number=3, wire_type=2)

    topics: list[str] = []
    for ev in notifier[:10]:
        try:
            ev_msg = decode_message(ev.value)
            topic = get_first(ev_msg, number=1, wire_type=2)
            if topic is not None:
                topics.append(topic.value.decode("utf-8", "ignore"))
        except Exception:
            continue

    return {
        "ok": True,
        "event": {
            "notifier_events": len(notifier),
            "control_events": len(control),
            "system_events": len(system),
            "topics_sample": topics,
        },
    }


def encode_subscribe_request(req_type: int, topics: set[str], *, variant: str = "topics1_type2") -> bytes:
    # The app bundles high-level models, but not the .proto definitions. We therefore
    # keep the encoder flexible while we confirm the on-wire schema.
    #
    # Supported variants:
    # - type1_topics2: 1=type (enum), 2=topics (repeated string)
    # - topics1_type2: 1=topics (repeated string), 2=type (enum)
    if variant not in ("type1_topics2", "topics1_type2"):
        raise ValueError(f"unknown variant: {variant}")

    if variant == "type1_topics2":
        msg = encode_varint_field(1, req_type)
        for topic in sorted(topics):
            msg += encode_bytes_field(2, topic.encode("utf-8"))
        return msg

    msg = b""
    for topic in sorted(topics):
        msg += encode_bytes_field(1, topic.encode("utf-8"))
    msg += encode_varint_field(2, req_type)
    return msg


def should_refresh_from_subscribe_response(payload: bytes) -> bool:
    try:
        top = decode_message(payload)
    except Exception:
        return True

    event = get_first(top, number=1, wire_type=2)
    if event is None:
        return False
    # First response we observed is an empty event (b'\\n\\x00'); ignore it.
    if not event.value:
        return False

    try:
        event_msg = decode_message(event.value)
    except Exception:
        return True

    # Refresh only when we get actual notifier/system events.
    if get_all(event_msg, number=1, wire_type=2):
        return True
    if get_all(event_msg, number=3, wire_type=2):
        return True
    return False
