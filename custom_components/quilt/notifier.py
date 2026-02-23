from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime
import pathlib
import logging
import queue
import threading
import time
from typing import Final

import grpc

from homeassistant.core import HomeAssistant

from .api import QuiltApi
from .coordinator import QuiltCoordinator
from .notifier_proto import (
    QuiltNotifierConfig,
    SubscribeRequestType,
    encode_publish_request,
    encode_subscribe_request,
    should_refresh_from_subscribe_response,
)

_LOGGER: Final = logging.getLogger(__name__)


class QuiltNotifier:
    """Maintains a notifier stream and requests coordinator refreshes on events.

    Implementation notes:
    - Uses grpc.aio streaming in a dedicated background thread+event-loop so HA's
      event loop never blocks.
    - Subscribes to HDS topics derived from coordinator data.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api: QuiltApi,
        coordinator: QuiltCoordinator,
        config: QuiltNotifierConfig | None = None,
    ) -> None:
        self._hass = hass
        self._api = api
        self._coordinator = coordinator
        self._config = config or QuiltNotifierConfig()

        self._unsub_coordinator: callable | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._lock = threading.Lock()
        self._desired_topics: set[str] = set()
        self._reconnect = threading.Event()
        self._debug_dir = pathlib.Path(hass.config.path(".quilt_debug"))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._unsub_coordinator = self._coordinator.async_add_listener(self._on_coordinator_update)
        self._stop.clear()
        self._reconnect.clear()
        self._thread = threading.Thread(
            target=self._run_thread,
            name=f"quilt_notifier_{self._coordinator.name}",
            daemon=True,
        )
        self._thread.start()
        self._on_coordinator_update()

    async def stop(self) -> None:
        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None

        thread, self._thread = self._thread, None
        if thread is None:
            return

        self._stop.set()
        self._reconnect.set()
        await asyncio.to_thread(thread.join, timeout=10)

    def _on_coordinator_update(self) -> None:
        self._hass.async_create_task(self._update_topics(), name="quilt_notifier_update_topics")

    async def _update_topics(self) -> None:
        data = self._coordinator.data
        if data is None:
            return

        topics = data.hds.notifier_topics()

        with self._lock:
            changed = topics != self._desired_topics
            self._desired_topics = topics
            if changed:
                self._reconnect.set()

    def _debug_dump(self, direction: str, payload: bytes) -> None:
        if not payload:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            safe_name = self._coordinator.name.replace("/", "_").replace(" ", "_")
            path = self._debug_dir / f"{ts}.notifier_{safe_name}.{direction}.b64"
            path.write_text(base64.b64encode(payload).decode("ascii") + "\n", encoding="utf-8")
        except Exception:
            # Debug-only best effort
            return

    def _run_thread(self) -> None:
        """Runs the notifier loop in a background thread.

        Uses synchronous grpc in this thread to avoid mixing grpc.aio with HA's
        asyncio runtime (which has caused stability issues in practice).
        """
        backoff = 1.0
        last_refresh = 0.0
        method = "/core.protos.notifier.NotifierService/Subscribe"

        def _get_id_token() -> str:
            fut = asyncio.run_coroutine_threadsafe(self._api.async_get_authorization_header(), self._hass.loop)
            return fut.result(timeout=30)

        while not self._stop.is_set():
            try:
                if self._coordinator.data is None:
                    time.sleep(1.0)
                    continue

                with self._lock:
                    topics = set(self._desired_topics)
                    self._reconnect.clear()

                if not topics:
                    time.sleep(1.0)
                    continue

                id_token = _get_id_token()
                metadata = [("authorization", id_token)]
                req = encode_subscribe_request(SubscribeRequestType.APPEND, topics)
                self._debug_dump("req", req)

                _LOGGER.debug("Notifier connecting for %s (topics=%d)", self._coordinator.name, len(topics))

                channel = grpc.secure_channel(
                    self._api.host,
                    grpc.ssl_channel_credentials(),
                    options=self._api.grpc_channel_options(),
                )
                stub = channel.stream_stream(
                    method,
                    request_serializer=lambda x: x,
                    response_deserializer=lambda x: x,
                )
                publish = channel.unary_unary(
                    "/core.protos.notifier.NotifierService/Publish",
                    request_serializer=lambda x: x,
                    response_deserializer=lambda x: x,
                )

                stop_sentinel = object()
                out_q: queue.Queue[object] = queue.Queue()
                out_q.put(req)

                heartbeat_stop = threading.Event()

                def _heartbeat_loop(system_id: str) -> None:
                    topic = f"system/{system_id}/client_heartbeat"
                    payload = encode_publish_request([(topic, None)])
                    sent = 0
                    dumped = False
                    while not heartbeat_stop.is_set() and not self._stop.is_set() and not self._reconnect.is_set():
                        try:
                            idt = _get_id_token()
                            if not dumped:
                                self._debug_dump("publish_req", payload)
                                dumped = True
                            publish(payload, metadata=[("authorization", idt)], timeout=10)
                        except Exception as e:
                            _LOGGER.debug("Notifier heartbeat publish failed for %s: %s", self._coordinator.name, e)
                        interval = 1.0 if sent < 10 else 30.0
                        sent += 1
                        heartbeat_stop.wait(interval)

                system_id = self._coordinator.data.system.system_id  # type: ignore[union-attr]
                hb_thread = threading.Thread(
                    target=_heartbeat_loop,
                    name=f"quilt_notifier_hb_{self._coordinator.name}",
                    args=(system_id,),
                    daemon=True,
                )
                hb_thread.start()

                def _request_iter():
                    while True:
                        if self._stop.is_set() or self._reconnect.is_set():
                            return
                        try:
                            item = out_q.get(timeout=1.0)
                        except queue.Empty:
                            continue
                        if item is stop_sentinel:
                            return
                        yield item  # type: ignore[misc]

                call = stub(_request_iter(), metadata=metadata)
                backoff = 1.0
                _LOGGER.debug("Notifier connected for %s", self._coordinator.name)

                try:
                    for payload in call:
                        if self._stop.is_set() or self._reconnect.is_set():
                            break
                        if not payload:
                            continue
                        self._debug_dump("resp", payload)
                        if not should_refresh_from_subscribe_response(payload):
                            continue
                        now = time.monotonic()
                        if now - last_refresh < self._config.min_refresh_interval_seconds:
                            continue
                        last_refresh = now
                        asyncio.run_coroutine_threadsafe(self._coordinator.async_request_refresh(), self._hass.loop)
                finally:
                    heartbeat_stop.set()
                    with contextlib.suppress(Exception):
                        hb_thread.join(timeout=2)
                    with contextlib.suppress(Exception):
                        out_q.put(stop_sentinel)
                    with contextlib.suppress(Exception):
                        call.cancel()
                    with contextlib.suppress(Exception):
                        channel.close()

                with contextlib.suppress(Exception):
                    code = call.code()
                    details = call.details()
                    trailing = call.trailing_metadata()
                    _LOGGER.debug(
                        "Notifier stream ended for %s: code=%s details=%s trailing=%s",
                        self._coordinator.name,
                        code,
                        details,
                        trailing,
                    )

            except grpc.RpcError as e:
                _LOGGER.debug("Notifier stream error for %s: %s", self._coordinator.name, e)
            except Exception as e:
                _LOGGER.debug("Notifier stream error for %s: %s", self._coordinator.name, e)

            if self._stop.is_set():
                break
            if self._reconnect.is_set():
                continue
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
