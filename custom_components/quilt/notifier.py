from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import pathlib
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Final

import grpc
from homeassistant.core import HomeAssistant

from .api import QuiltApi
from .coordinator import QuiltCoordinator
from .debug_dump import write_debug_dump
from .notifier_proto import (
    QuiltNotifierConfig,
    SubscribeRequestType,
    encode_publish_request,
    encode_subscribe_request,
    should_refresh_from_subscribe_response,
)

_LOGGER: Final = logging.getLogger(__name__)
AUTH_TIMEOUT_SECONDS: Final = 30.0


class QuiltNotifier:
    """Maintains a notifier stream and requests coordinator refreshes on events.

    Implementation notes:
    - Uses synchronous gRPC streaming in dedicated worker threads so HA's event loop
      never blocks.
    - Subscribes to HDS topics derived from coordinator data.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api: QuiltApi,
        coordinator: QuiltCoordinator,
        config: QuiltNotifierConfig | None = None,
        debug_dir: str | pathlib.Path | None = None,
    ) -> None:
        self._hass = hass
        self._api = api
        self._coordinator = coordinator
        self._config = config or QuiltNotifierConfig()

        self._unsub_coordinator: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stream_lock = threading.Lock()
        self._desired_topics: set[str] = set()
        self._reconnect = threading.Event()
        self._debug_dir = pathlib.Path(debug_dir) if debug_dir else None
        self._active_call: grpc.Call | None = None
        self._active_channel: grpc.Channel | None = None
        self._auth_waiters: dict[Future[str], threading.Event | None] = {}
        self._auth_tasks: dict[
            asyncio.Task[None], tuple[threading.Event | None, Future[str]]
        ] = {}
        self._refresh_tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._unsub_coordinator = self._coordinator.async_add_listener(
            self._on_coordinator_update
        )
        self._stop.clear()
        self._reconnect.clear()
        thread = threading.Thread(
            target=self._run_thread,
            name=f"quilt_notifier_{self._coordinator.name}",
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:
            if self._unsub_coordinator is not None:
                self._unsub_coordinator()
                self._unsub_coordinator = None
            raise
        self._thread = thread
        self._on_coordinator_update()

    async def stop(self) -> None:
        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None

        thread = self._thread
        if thread is None:
            return

        self._stop.set()
        self._reconnect.set()
        self._cancel_auth_work()
        await self._cancel_auth_tasks()
        with self._stream_lock:
            active_call = self._active_call
            active_channel = self._active_channel
        if active_call is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(active_call.cancel)
        if active_channel is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(active_channel.close)
        await asyncio.to_thread(thread.join, timeout=10)
        if thread.is_alive():
            _LOGGER.error(
                "Quilt notifier thread failed to stop for %s", self._coordinator.name
            )
            raise RuntimeError(
                f"Quilt notifier thread failed to stop for {self._coordinator.name}"
            )
        await self._cancel_refresh_tasks()
        self._thread = None

    def _cancel_auth_work(self, cancel_event: threading.Event | None = None) -> None:
        """Cancel auth handoffs now and their HA-loop tasks on the loop."""
        with self._lifecycle_lock:
            waiters = tuple(
                waiter
                for waiter, waiter_event in self._auth_waiters.items()
                if cancel_event is None or waiter_event is cancel_event
            )
        for waiter in waiters:
            waiter.cancel()
        try:
            self._hass.loop.call_soon_threadsafe(
                self._cancel_auth_tasks_for_event, cancel_event
            )
        except RuntimeError:
            _LOGGER.debug("Unable to queue Quilt auth-task cancellation", exc_info=True)

    def _cancel_auth_tasks_for_event(
        self, cancel_event: threading.Event | None
    ) -> None:
        for task, (task_event, waiter) in tuple(self._auth_tasks.items()):
            if cancel_event is None or task_event is cancel_event:
                waiter.cancel()
                self._cancel_auth_task_once(task)

    @staticmethod
    def _cancel_auth_task_once(task: asyncio.Task[None]) -> None:
        """Request cancellation once so async cleanup can finish undisturbed."""
        if not task.done() and task.cancelling() == 0:
            task.cancel()

    async def _cancel_auth_tasks(self) -> None:
        """Cancel and drain all HA-loop auth tasks before shutdown completes."""
        tasks = tuple(self._auth_tasks)
        if not tasks:
            return

        for task in tasks:
            self._cancel_auth_task_once(task)
        # Returning exceptions deliberately consumes task results; the task done
        # callback retrieves and logs unexpected task failures.
        await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            self._auth_tasks.pop(task, None)

    async def _cancel_refresh_tasks(self) -> None:
        """Cancel and drain HA-loop refresh tasks before shutdown completes."""
        tasks = tuple(self._refresh_tasks)
        if not tasks:
            return

        for task in tasks:
            task.cancel()
        # Returning exceptions deliberately consumes task results; the task done
        # callback logs non-cancellation failures with the coordinator context.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._refresh_tasks.difference_update(tasks)

    def _on_refresh_task_done(self, task: asyncio.Task[None]) -> None:
        self._refresh_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            _LOGGER.error(
                "Quilt coordinator refresh failed for %s",
                self._coordinator.name,
                exc_info=True,
            )

    def _schedule_refresh(self) -> None:
        """Queue a refresh task on HA's loop, serialized with shutdown."""
        self._hass.loop.call_soon_threadsafe(self._create_refresh_task)

    def _create_refresh_task(self) -> None:
        """Create and own a refresh task from the HA event-loop thread."""
        if self._stop.is_set():
            return
        task = self._hass.loop.create_task(
            self._coordinator.async_request_refresh(),
            name=f"quilt_notifier_refresh_{self._coordinator.name}",
        )
        self._refresh_tasks.add(task)
        task.add_done_callback(self._on_refresh_task_done)

    async def _run_auth_task(
        self,
        waiter: Future[str],
        cancel_event: threading.Event | None,
    ) -> None:
        try:
            token = await self._api.async_get_authorization_header()
        except asyncio.CancelledError:
            waiter.cancel()
            raise
        except Exception as error:
            if not waiter.done():
                waiter.set_exception(error)
            raise
        else:
            if self._stop.is_set() or (
                cancel_event is not None and cancel_event.is_set()
            ):
                waiter.cancel()
            elif not waiter.done():
                waiter.set_result(token)

    def _create_auth_task(
        self,
        waiter: Future[str],
        cancel_event: threading.Event | None,
    ) -> None:
        if (
            waiter.cancelled()
            or self._stop.is_set()
            or (cancel_event is not None and cancel_event.is_set())
        ):
            waiter.cancel()
            return

        task = self._hass.loop.create_task(
            self._run_auth_task(waiter, cancel_event),
            name=f"quilt_notifier_auth_{self._coordinator.name}",
        )
        self._auth_tasks[task] = (cancel_event, waiter)
        task.add_done_callback(
            lambda completed: self._on_auth_task_done(completed, waiter)
        )

    def _on_auth_task_done(self, task: asyncio.Task[None], waiter: Future[str]) -> None:
        self._auth_tasks.pop(task, None)
        if task.cancelled():
            waiter.cancel()
            return
        try:
            task.result()
        except Exception:
            if not waiter.done():
                waiter.set_exception(task.exception())
            _LOGGER.debug(
                "Quilt notifier authorization task failed for %s",
                self._coordinator.name,
                exc_info=True,
            )

    def _cancel_auth_task_for_waiter(self, waiter: Future[str]) -> None:
        for task, (_, task_waiter) in tuple(self._auth_tasks.items()):
            if task_waiter is waiter:
                waiter.cancel()
                self._cancel_auth_task_once(task)

    def _on_coordinator_update(self) -> None:
        self._hass.async_create_task(
            self._update_topics(), name="quilt_notifier_update_topics"
        )

    async def _update_topics(self) -> None:
        data = self._coordinator.data
        if data is None:
            return

        topics = data.hds.notifier_topics()

        with self._lock:
            changed = topics != self._desired_topics
            self._desired_topics = topics
        if not changed:
            return

        self._reconnect.set()
        with self._stream_lock:
            active_call = self._active_call
        if active_call is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(active_call.cancel)

    def _debug_dump(self, direction: str, payload: bytes) -> None:
        if self._debug_dir is None:
            return
        try:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            safe_name = self._coordinator.name.replace("/", "_").replace(" ", "_")
            write_debug_dump(
                self._debug_dir,
                f"{ts}.notifier_{safe_name}.{direction}.b64",
                payload,
            )
        except Exception:
            # Debug-only best effort, but leave a diagnostic trail when enabled.
            _LOGGER.debug("Unable to write Quilt notifier debug dump", exc_info=True)

    def _run_thread(self) -> None:
        """Run the synchronous gRPC notifier loop in a background worker thread."""
        backoff = 1.0
        last_refresh = 0.0
        method = "/core.protos.notifier.NotifierService/Subscribe"

        def _get_id_token(cancel_event: threading.Event | None = None) -> str:
            waiter: Future[str] = Future()
            with self._lifecycle_lock:
                self._auth_waiters[waiter] = cancel_event
                try:
                    self._hass.loop.call_soon_threadsafe(
                        self._create_auth_task, waiter, cancel_event
                    )
                except BaseException:
                    self._auth_waiters.pop(waiter, None)
                    raise
            try:
                return waiter.result(timeout=AUTH_TIMEOUT_SECONDS)
            except BaseException:
                waiter.cancel()
                try:
                    self._hass.loop.call_soon_threadsafe(
                        self._cancel_auth_task_for_waiter, waiter
                    )
                except RuntimeError:
                    _LOGGER.debug(
                        "Unable to queue timed-out Quilt auth cancellation",
                        exc_info=True,
                    )
                raise
            finally:
                with self._lifecycle_lock:
                    self._auth_waiters.pop(waiter, None)

        def _wait_for_wake(timeout: float) -> None:
            self._reconnect.wait(timeout)
            self._reconnect.clear()

        while not self._stop.is_set():
            if self._coordinator.data is None:
                _wait_for_wake(1.0)
                continue

            with self._lock:
                topics = set(self._desired_topics)
                self._reconnect.clear()

            if not topics:
                _wait_for_wake(1.0)
                continue

            channel: grpc.Channel | None = None
            call: grpc.Call | None = None
            heartbeat_stop: threading.Event | None = None
            heartbeat_thread: threading.Thread | None = None
            heartbeat_started = False
            stream_healthy = False
            stop_sentinel = object()
            out_q: queue.Queue[object] | None = None
            try:
                id_token = _get_id_token()
                metadata = [("authorization", id_token)]
                req = encode_subscribe_request(SubscribeRequestType.APPEND, topics)
                self._debug_dump("req", req)

                _LOGGER.debug(
                    "Notifier connecting for %s (topics=%d)",
                    self._coordinator.name,
                    len(topics),
                )

                channel = grpc.secure_channel(
                    self._api.host,
                    grpc.ssl_channel_credentials(),
                    options=self._api.grpc_channel_options(),
                )
                with self._stream_lock:
                    self._active_channel = channel
                if self._stop.is_set():
                    continue
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

                out_q = queue.Queue()
                out_q.put(req)
                heartbeat_stop = threading.Event()

                def _heartbeat_loop(
                    system_id: str,
                    *,
                    heartbeat_event: threading.Event = heartbeat_stop,
                    publish_callable=publish,
                ) -> None:
                    topic = f"system/{system_id}/client_heartbeat"
                    payload = encode_publish_request([(topic, None)])
                    sent = 0
                    dumped = False
                    while (
                        not heartbeat_event.is_set()
                        and not self._stop.is_set()
                        and not self._reconnect.is_set()
                    ):
                        try:
                            idt = _get_id_token(heartbeat_event)
                            if not dumped:
                                self._debug_dump("publish_req", payload)
                                dumped = True
                            publish_callable(
                                payload, metadata=[("authorization", idt)], timeout=10
                            )
                        except Exception as e:
                            _LOGGER.debug(
                                "Notifier heartbeat publish failed for %s: %s",
                                self._coordinator.name,
                                e,
                            )
                        interval = 1.0 if sent < 10 else 30.0
                        sent += 1
                        heartbeat_event.wait(interval)

                system_id = self._coordinator.data.system.system_id  # type: ignore[union-attr]
                heartbeat_thread = threading.Thread(
                    target=_heartbeat_loop,
                    name=f"quilt_notifier_hb_{self._coordinator.name}",
                    args=(system_id,),
                    daemon=True,
                )
                heartbeat_thread.start()
                heartbeat_started = True

                def _request_iter(
                    *,
                    request_queue: queue.Queue[object] = out_q,
                    sentinel: object = stop_sentinel,
                ):
                    while True:
                        if self._stop.is_set() or self._reconnect.is_set():
                            return
                        try:
                            item = request_queue.get(timeout=1.0)
                        except queue.Empty:
                            continue
                        if item is sentinel:
                            return
                        yield item  # type: ignore[misc]

                call = stub(_request_iter(), metadata=metadata)
                with self._stream_lock:
                    self._active_call = call
                if self._stop.is_set() or self._reconnect.is_set():
                    with contextlib.suppress(Exception):
                        call.cancel()
                _LOGGER.debug("Notifier connected for %s", self._coordinator.name)

                for payload in call:
                    if self._stop.is_set() or self._reconnect.is_set():
                        break
                    if not payload:
                        continue
                    if not stream_healthy:
                        backoff = 1.0
                        stream_healthy = True
                    self._debug_dump("resp", payload)
                    if not should_refresh_from_subscribe_response(payload):
                        continue
                    now = time.monotonic()
                    if now - last_refresh < self._config.min_refresh_interval_seconds:
                        continue
                    last_refresh = now
                    self._schedule_refresh()

                if call is not None:
                    with contextlib.suppress(Exception):
                        _LOGGER.debug(
                            "Notifier stream ended for %s: code=%s details=%s trailing=%s",
                            self._coordinator.name,
                            call.code(),
                            call.details(),
                            call.trailing_metadata(),
                        )

            except grpc.RpcError as e:
                _LOGGER.debug(
                    "Notifier stream error for %s: %s", self._coordinator.name, e
                )
            except Exception as e:
                _LOGGER.debug(
                    "Notifier stream error for %s: %s", self._coordinator.name, e
                )
            finally:
                if heartbeat_stop is not None:
                    heartbeat_stop.set()
                self._cancel_auth_work(heartbeat_stop)
                if out_q is not None:
                    with contextlib.suppress(Exception):
                        out_q.put(stop_sentinel)
                if call is not None:
                    with contextlib.suppress(Exception):
                        call.cancel()
                if channel is not None:
                    with contextlib.suppress(Exception):
                        channel.close()
                if heartbeat_started and heartbeat_thread is not None:
                    heartbeat_thread.join()
                with self._stream_lock:
                    if self._active_call is call:
                        self._active_call = None
                    if self._active_channel is channel:
                        self._active_channel = None

            if self._stop.is_set():
                break
            if self._reconnect.is_set():
                self._reconnect.clear()
                continue
            if self._reconnect.wait(backoff):
                self._reconnect.clear()
                continue
            backoff = min(backoff * 2, 60.0)
