from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import grpc
import pytest

import custom_components.quilt.notifier as notifier_module
from custom_components.quilt.notifier import QuiltNotifier
from custom_components.quilt.proto_wire import encode_bytes_field


def test_notifier_debug_dumps_are_disabled_without_directory() -> None:
    notifier = QuiltNotifier(hass=object(), api=object(), coordinator=object())  # type: ignore[arg-type]

    assert notifier._debug_dir is None  # noqa: SLF001
    notifier._debug_dump("req", b"secret")  # noqa: SLF001


def test_notifier_debug_dumps_use_opt_in_directory(tmp_path: Path) -> None:
    coordinator = type("Coordinator", (), {"name": "Quilt Office"})()
    notifier = QuiltNotifier(
        hass=object(),
        api=object(),
        coordinator=coordinator,  # type: ignore[arg-type]
        debug_dir=tmp_path,
    )

    notifier._debug_dump("req", b"payload")  # noqa: SLF001

    assert list(tmp_path.glob("*.b64"))


def test_notifier_start_failure_unroots_thread_and_listener(
    monkeypatch,
) -> None:  # noqa: ANN001
    unsubscribed = threading.Event()

    class Coordinator:
        name = "Quilt Start Failure"

        def async_add_listener(self, callback):  # noqa: ANN001
            del callback
            return unsubscribed.set

    def fail_start(self) -> None:  # noqa: ANN001
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    loop = asyncio.new_event_loop()
    notifier = QuiltNotifier(
        SimpleNamespace(loop=loop),
        api=object(),
        coordinator=Coordinator(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="can't start new thread"):
        notifier.start()

    assert notifier._thread is None  # noqa: SLF001
    assert unsubscribed.is_set()
    asyncio.run(notifier.stop())
    loop.close()


def test_notifier_stop_cancels_blocked_stream(monkeypatch) -> None:  # noqa: ANN001
    stream_started = threading.Event()
    stream_released = threading.Event()

    class BlockingCall:
        def __iter__(self):  # noqa: ANN204
            stream_started.set()
            stream_released.wait()
            return iter(())

        def cancel(self) -> None:
            stream_released.set()

        def code(self):  # noqa: ANN201
            return None

        def details(self):  # noqa: ANN201
            return None

        def trailing_metadata(self):  # noqa: ANN201
            return None

    call = BlockingCall()

    class FakeChannel:
        def __init__(self) -> None:
            self.close_thread_ids: list[int] = []

        def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return lambda *call_args, **call_kwargs: call

        def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return lambda *call_args, **call_kwargs: None

        def close(self) -> None:
            self.close_thread_ids.append(threading.get_ident())
            stream_released.set()

    channel = FakeChannel()
    monkeypatch.setattr(
        notifier_module.grpc, "secure_channel", lambda *args, **kwargs: channel
    )

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    class FakeApi:
        host = "example.com"

        async def async_get_authorization_header(self) -> str:
            return "token"

        def grpc_channel_options(self) -> list[tuple[str, int | str]]:
            return []

    coordinator = SimpleNamespace(
        data=SimpleNamespace(
            system=SimpleNamespace(system_id="system"),
            hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
        ),
        name="Quilt Test",
    )
    hass = SimpleNamespace(loop=loop)
    notifier = QuiltNotifier(hass, api=FakeApi(), coordinator=coordinator)
    notifier._desired_topics = {"topic"}  # noqa: SLF001
    worker = threading.Thread(target=notifier._run_thread, daemon=True)  # noqa: SLF001
    notifier._thread = worker  # noqa: SLF001

    try:
        worker.start()
        assert stream_started.wait(timeout=2)
        stop_future = asyncio.run_coroutine_threadsafe(notifier.stop(), loop)
        stop_future.result(timeout=2)
        assert not worker.is_alive()
        assert threading.get_ident() not in channel.close_thread_ids
    finally:
        call.cancel()
        worker.join(timeout=2)
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()


def test_notifier_topic_change_cancels_active_quiet_call(
    monkeypatch,
) -> None:  # noqa: ANN001
    async def scenario() -> None:
        stream_started = threading.Event()
        stream_released = threading.Event()
        call_cancelled = threading.Event()
        cancel_thread_ids: list[int] = []
        topics = {"old"}
        loop_thread_id = threading.get_ident()

        class FakeApi:
            host = "example.com"

            async def async_get_authorization_header(self) -> str:
                return "token"

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        class QuietCall:
            def __iter__(self):  # noqa: ANN204
                stream_started.set()
                stream_released.wait()
                return iter(())

            def cancel(self) -> None:
                cancel_thread_ids.append(threading.get_ident())
                call_cancelled.set()
                stream_released.set()

            def code(self):  # noqa: ANN201
                return None

            def details(self):  # noqa: ANN201
                return None

            def trailing_metadata(self):  # noqa: ANN201
                return None

        call = QuietCall()

        class FakeChannel:
            def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: call

            def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: None

            def close(self) -> None:
                stream_released.set()

        monkeypatch.setattr(
            notifier_module.grpc,
            "secure_channel",
            lambda *args, **kwargs: FakeChannel(),
        )
        data = SimpleNamespace(
            system=SimpleNamespace(system_id="system"),
            hds=SimpleNamespace(notifier_topics=lambda: set(topics)),
        )
        coordinator = SimpleNamespace(data=data, name="Quilt Topic Change")
        notifier = QuiltNotifier(
            SimpleNamespace(loop=asyncio.get_running_loop()),
            api=FakeApi(),
            coordinator=coordinator,
        )
        notifier._desired_topics = {"old"}  # noqa: SLF001
        worker = threading.Thread(
            target=notifier._run_thread, daemon=True
        )  # noqa: SLF001
        notifier._thread = worker  # noqa: SLF001

        worker.start()
        assert await asyncio.to_thread(stream_started.wait, 2)
        topics.clear()
        topics.add("new")
        await asyncio.wait_for(notifier._update_topics(), timeout=2)  # noqa: SLF001

        assert call_cancelled.is_set()
        assert cancel_thread_ids[0] != loop_thread_id
        await asyncio.wait_for(notifier.stop(), timeout=2)
        assert not worker.is_alive()

    asyncio.run(scenario())


def test_notifier_topic_change_before_call_publication_cancels_new_call(
    monkeypatch,
) -> None:  # noqa: ANN001
    async def scenario() -> None:
        publication_held = threading.Event()
        release_publication = threading.Event()
        stream_released = threading.Event()
        call_cancelled = threading.Event()
        topics = {"old"}

        class FakeApi:
            host = "example.com"

            async def async_get_authorization_header(self) -> str:
                return "token"

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        class Call:
            def __iter__(self):  # noqa: ANN204
                stream_released.wait()
                return iter(())

            def cancel(self) -> None:
                call_cancelled.set()
                stream_released.set()

            def code(self):  # noqa: ANN201
                return None

            def details(self):  # noqa: ANN201
                return None

            def trailing_metadata(self):  # noqa: ANN201
                return None

        call = Call()

        class FakeChannel:
            def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs

                def stub(*call_args, **call_kwargs):
                    del call_args, call_kwargs
                    publication_held.set()
                    assert release_publication.wait(timeout=2)
                    return call

                return stub

            def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: None

            def close(self) -> None:
                stream_released.set()

        monkeypatch.setattr(
            notifier_module.grpc,
            "secure_channel",
            lambda *args, **kwargs: FakeChannel(),
        )
        data = SimpleNamespace(
            system=SimpleNamespace(system_id="system"),
            hds=SimpleNamespace(notifier_topics=lambda: set(topics)),
        )
        coordinator = SimpleNamespace(data=data, name="Quilt Publication Race")
        notifier = QuiltNotifier(
            SimpleNamespace(loop=asyncio.get_running_loop()),
            api=FakeApi(),
            coordinator=coordinator,
        )
        notifier._desired_topics = {"old"}  # noqa: SLF001
        worker = threading.Thread(
            target=notifier._run_thread, daemon=True
        )  # noqa: SLF001
        notifier._thread = worker  # noqa: SLF001

        worker.start()
        assert await asyncio.to_thread(publication_held.wait, 2)
        topics.clear()
        topics.add("new")
        await asyncio.wait_for(notifier._update_topics(), timeout=2)  # noqa: SLF001
        assert notifier._active_call is None  # noqa: SLF001
        assert not call_cancelled.is_set()

        release_publication.set()
        assert await asyncio.to_thread(call_cancelled.wait, 2)
        await asyncio.wait_for(notifier.stop(), timeout=2)
        assert not worker.is_alive()

    asyncio.run(scenario())


def test_notifier_stop_cancels_and_drains_refresh_task(
    monkeypatch,
) -> None:  # noqa: ANN001
    async def scenario() -> None:
        refresh_started = threading.Event()
        cleanup_started = threading.Event()
        cleanup_release = threading.Event()
        stream_released = threading.Event()
        refresh_payload = encode_bytes_field(1, encode_bytes_field(1, b"hds/space/x"))

        class FakeApi:
            host = "example.com"

            async def async_get_authorization_header(self) -> str:
                return "token"

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        class Coordinator:
            name = "Quilt Refresh"
            data = SimpleNamespace(
                system=SimpleNamespace(system_id="system"),
                hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
            )

            async def async_request_refresh(self) -> None:
                refresh_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup_started.set()
                    await asyncio.to_thread(cleanup_release.wait, 2)
                    raise

        class BlockingCall:
            def __iter__(self):  # noqa: ANN204
                yield refresh_payload
                stream_released.wait()
                return iter(())

            def cancel(self) -> None:
                stream_released.set()

            def code(self):  # noqa: ANN201
                return None

            def details(self):  # noqa: ANN201
                return None

            def trailing_metadata(self):  # noqa: ANN201
                return None

        call = BlockingCall()

        class FakeChannel:
            def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: call

            def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: None

            def close(self) -> None:
                stream_released.set()

        monkeypatch.setattr(
            notifier_module.grpc,
            "secure_channel",
            lambda *args, **kwargs: FakeChannel(),
        )
        notifier = QuiltNotifier(
            SimpleNamespace(loop=asyncio.get_running_loop()),
            api=FakeApi(),
            coordinator=Coordinator(),
        )
        notifier._desired_topics = {"topic"}  # noqa: SLF001
        worker = threading.Thread(
            target=notifier._run_thread, daemon=True
        )  # noqa: SLF001
        notifier._thread = worker  # noqa: SLF001
        worker.start()

        assert await asyncio.to_thread(refresh_started.wait, 2)
        stop_task = asyncio.create_task(notifier.stop())
        assert await asyncio.to_thread(cleanup_started.wait, 2)
        await asyncio.sleep(0)
        assert not stop_task.done()
        cleanup_release.set()
        await asyncio.wait_for(stop_task, timeout=2)

        assert not worker.is_alive()
        assert not notifier._refresh_tasks  # noqa: SLF001
        await notifier.stop()

    asyncio.run(scenario())


def test_notifier_does_not_schedule_refresh_after_stop_barrier() -> None:
    async def scenario() -> None:
        calls = 0

        class Coordinator:
            name = "Quilt Refresh Race"

            async def async_request_refresh(self) -> None:
                nonlocal calls
                calls += 1

        notifier = QuiltNotifier(
            SimpleNamespace(loop=asyncio.get_running_loop()),
            api=object(),
            coordinator=Coordinator(),
        )
        notifier._stop.set()  # noqa: SLF001
        notifier._schedule_refresh()  # noqa: SLF001
        await asyncio.sleep(0)

        assert calls == 0
        assert not notifier._refresh_tasks  # noqa: SLF001

    asyncio.run(scenario())


def test_notifier_stop_racing_channel_publication(monkeypatch) -> None:  # noqa: ANN001
    publication_reached = threading.Event()
    release_publication = threading.Event()
    stop_finished = threading.Event()
    stop_errors: list[BaseException] = []

    class PublicationLock:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._block_next = True

        def __enter__(self):  # noqa: ANN204
            if self._block_next:
                self._block_next = False
                publication_reached.set()
                assert release_publication.wait(timeout=2)
            self._lock.acquire()
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:  # noqa: ANN001
            del exc_type, exc_value, traceback
            self._lock.release()

    class FakeChannel:
        def __init__(self) -> None:
            self.stream_calls = 0
            self.closed = threading.Event()

        def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            self.stream_calls += 1
            return lambda *call_args, **call_kwargs: iter(())

        def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return lambda *call_args, **call_kwargs: None

        def close(self) -> None:
            self.closed.set()

    channel = FakeChannel()
    monkeypatch.setattr(
        notifier_module.grpc, "secure_channel", lambda *args, **kwargs: channel
    )

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    class FakeApi:
        host = "example.com"

        async def async_get_authorization_header(self) -> str:
            return "token"

        def grpc_channel_options(self) -> list[tuple[str, int | str]]:
            return []

    coordinator = SimpleNamespace(
        data=SimpleNamespace(
            system=SimpleNamespace(system_id="system"),
            hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
        ),
        name="Quilt Race",
    )
    hass = SimpleNamespace(loop=loop)
    notifier = QuiltNotifier(hass, api=FakeApi(), coordinator=coordinator)
    notifier._desired_topics = {"topic"}  # noqa: SLF001
    notifier._stream_lock = PublicationLock()  # noqa: SLF001
    worker = threading.Thread(target=notifier._run_thread, daemon=True)  # noqa: SLF001
    notifier._thread = worker  # noqa: SLF001

    def stop_notifier() -> None:
        try:
            asyncio.run(notifier.stop())
        except BaseException as error:  # pragma: no cover - assertion below reports it
            stop_errors.append(error)
        finally:
            stop_finished.set()

    try:
        worker.start()
        assert publication_reached.wait(timeout=2)
        stop_thread = threading.Thread(target=stop_notifier, daemon=True)
        stop_thread.start()
        assert notifier._stop.wait(timeout=2)  # noqa: SLF001
        release_publication.set()
        assert stop_finished.wait(timeout=3)
        stop_thread.join(timeout=1)
        worker.join(timeout=1)

        assert not stop_errors
        assert not worker.is_alive()
        assert channel.stream_calls == 0
        assert channel.closed.is_set()
    finally:
        release_publication.set()
        notifier._stop.set()  # noqa: SLF001
        worker.join(timeout=2)
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()


def test_notifier_stop_cancels_blocked_parent_auth_and_can_restart(
    monkeypatch,
) -> None:  # noqa: ANN001
    async def scenario() -> None:
        first_auth_started = threading.Event()
        second_auth_started = threading.Event()
        auth_cancelled = threading.Event()

        class FakeApi:
            host = "example.com"

            def __init__(self) -> None:
                self.block_auth = True

            async def async_get_authorization_header(self) -> str:
                if self.block_auth:
                    first_auth_started.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        auth_cancelled.set()
                        raise
                second_auth_started.set()
                return "token"

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        class EmptyCall:
            def __iter__(self):  # noqa: ANN204
                return iter(())

            def cancel(self) -> None:
                return None

        class FakeChannel:
            def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: EmptyCall()

            def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: None

            def close(self) -> None:
                return None

        api = FakeApi()
        channel = FakeChannel()
        monkeypatch.setattr(
            notifier_module.grpc, "secure_channel", lambda *args, **kwargs: channel
        )

        coordinator = SimpleNamespace(
            data=SimpleNamespace(
                system=SimpleNamespace(system_id="system"),
                hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
            ),
            name="Quilt Auth",
            async_add_listener=lambda callback: lambda: None,
        )
        hass = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            async_create_task=lambda coro, name=None: asyncio.create_task(
                coro, name=name
            ),
        )
        notifier = QuiltNotifier(hass, api=api, coordinator=coordinator)

        notifier.start()
        assert await asyncio.to_thread(first_auth_started.wait, 2)
        await notifier.stop()
        assert await asyncio.to_thread(auth_cancelled.wait, 2)
        assert notifier._thread is None  # noqa: SLF001

        api.block_auth = False
        notifier.start()
        assert await asyncio.to_thread(second_auth_started.wait, 2)
        await notifier.stop()
        assert notifier._thread is None  # noqa: SLF001

    asyncio.run(scenario())


def test_notifier_finalizer_cancels_heartbeat_auth_after_stub_failure(
    monkeypatch,
) -> None:  # noqa: ANN001
    async def scenario() -> None:
        heartbeat_auth_started = threading.Event()
        auth_cancelled = threading.Event()
        stub_failed = threading.Event()

        class FakeApi:
            host = "example.com"

            def __init__(self) -> None:
                self.auth_calls = 0

            async def async_get_authorization_header(self) -> str:
                self.auth_calls += 1
                if self.auth_calls == 1:
                    return "token"
                heartbeat_auth_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    auth_cancelled.set()
                    raise

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        class FakeChannel:
            def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs

                def stub(*call_args, **call_kwargs):
                    del call_args, call_kwargs
                    assert heartbeat_auth_started.wait(timeout=2)
                    stub_failed.set()
                    raise RuntimeError("stub failed after heartbeat start")

                return stub

            def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: None

            def close(self) -> None:
                return None

        api = FakeApi()
        channel = FakeChannel()
        monkeypatch.setattr(
            notifier_module.grpc, "secure_channel", lambda *args, **kwargs: channel
        )
        coordinator = SimpleNamespace(
            data=SimpleNamespace(
                system=SimpleNamespace(system_id="system"),
                hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
            ),
            name="Quilt Finalizer",
        )
        hass = SimpleNamespace(loop=asyncio.get_running_loop())
        notifier = QuiltNotifier(hass, api=api, coordinator=coordinator)
        notifier._desired_topics = {"topic"}  # noqa: SLF001
        worker = threading.Thread(
            target=notifier._run_thread, daemon=True
        )  # noqa: SLF001
        notifier._thread = worker  # noqa: SLF001
        worker.start()

        assert await asyncio.to_thread(stub_failed.wait, 2)
        assert await asyncio.to_thread(auth_cancelled.wait, 2)
        await notifier.stop()

        assert not worker.is_alive()
        assert notifier._active_channel is None  # noqa: SLF001

    asyncio.run(scenario())


def test_notifier_cancels_heartbeat_auth_after_late_finalizer_snapshot() -> None:
    async def scenario() -> None:
        heartbeat_stop = threading.Event()
        waiter: Future[str] = Future()
        notifier = QuiltNotifier(
            SimpleNamespace(loop=asyncio.get_running_loop()),
            api=object(),
            coordinator=SimpleNamespace(name="Quilt Late Auth"),
        )

        heartbeat_stop.set()
        notifier._cancel_auth_work(heartbeat_stop)  # noqa: SLF001
        with notifier._lifecycle_lock:  # noqa: SLF001
            notifier._auth_waiters[waiter] = heartbeat_stop  # noqa: SLF001
        asyncio.get_running_loop().call_soon(
            notifier._create_auth_task, waiter, heartbeat_stop  # noqa: SLF001
        )
        await asyncio.sleep(0)

        assert waiter.cancelled()
        assert not notifier._auth_tasks  # noqa: SLF001

    asyncio.run(scenario())


def test_notifier_stop_cancels_and_drains_auth_task_cleanup() -> None:
    async def scenario() -> None:
        auth_started = threading.Event()
        cleanup_started = threading.Event()
        cleanup_finished = threading.Event()
        cleanup_release = threading.Event()

        class FakeApi:
            host = "example.com"

            async def async_get_authorization_header(self) -> str:
                auth_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup_started.set()
                    await asyncio.to_thread(cleanup_release.wait, 2)
                    cleanup_finished.set()
                    raise

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        coordinator = SimpleNamespace(
            data=SimpleNamespace(
                system=SimpleNamespace(system_id="system"),
                hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
            ),
            name="Quilt Auth Cleanup",
        )
        notifier = QuiltNotifier(
            SimpleNamespace(loop=asyncio.get_running_loop()),
            api=FakeApi(),
            coordinator=coordinator,
        )
        notifier._desired_topics = {"topic"}  # noqa: SLF001
        worker = threading.Thread(
            target=notifier._run_thread, daemon=True
        )  # noqa: SLF001
        notifier._thread = worker  # noqa: SLF001
        worker.start()

        assert await asyncio.to_thread(auth_started.wait, 2)
        stop_task = asyncio.create_task(notifier.stop())
        assert await asyncio.to_thread(cleanup_started.wait, 2)
        await asyncio.sleep(0)
        assert not stop_task.done()
        cleanup_release.set()
        await asyncio.wait_for(stop_task, timeout=2)

        assert not worker.is_alive()
        assert cleanup_finished.is_set()
        assert not notifier._auth_tasks  # noqa: SLF001
        await notifier.stop()

    asyncio.run(scenario())


def test_notifier_stop_cancels_blocked_heartbeat_auth_during_active_stream(
    monkeypatch,
) -> None:  # noqa: ANN001
    async def scenario() -> None:
        stream_started = threading.Event()
        heartbeat_auth_started = threading.Event()
        auth_cancelled = threading.Event()
        stream_released = threading.Event()

        class FakeApi:
            host = "example.com"

            def __init__(self) -> None:
                self.auth_calls = 0

            async def async_get_authorization_header(self) -> str:
                self.auth_calls += 1
                if self.auth_calls == 1:
                    return "token"
                heartbeat_auth_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    auth_cancelled.set()
                    raise

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        class BlockingCall:
            def __iter__(self):  # noqa: ANN204
                stream_started.set()
                stream_released.wait()
                return iter(())

            def cancel(self) -> None:
                stream_released.set()

            def code(self):  # noqa: ANN201
                return None

            def details(self):  # noqa: ANN201
                return None

            def trailing_metadata(self):  # noqa: ANN201
                return None

        call = BlockingCall()

        class FakeChannel:
            def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: call

            def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: None

            def close(self) -> None:
                stream_released.set()

        monkeypatch.setattr(
            notifier_module.grpc,
            "secure_channel",
            lambda *args, **kwargs: FakeChannel(),
        )
        coordinator = SimpleNamespace(
            data=SimpleNamespace(
                system=SimpleNamespace(system_id="system"),
                hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
            ),
            name="Quilt Active Auth",
        )
        hass = SimpleNamespace(loop=asyncio.get_running_loop())
        notifier = QuiltNotifier(hass, api=FakeApi(), coordinator=coordinator)
        notifier._desired_topics = {"topic"}  # noqa: SLF001
        worker = threading.Thread(
            target=notifier._run_thread, daemon=True
        )  # noqa: SLF001
        notifier._thread = worker  # noqa: SLF001
        worker.start()

        assert await asyncio.to_thread(stream_started.wait, 2)
        assert await asyncio.to_thread(heartbeat_auth_started.wait, 2)
        await asyncio.wait_for(notifier.stop(), timeout=2)

        assert await asyncio.to_thread(auth_cancelled.wait, 2)
        assert not worker.is_alive()
        assert not any(
            thread.is_alive() and thread.name == "quilt_notifier_hb_Quilt Active Auth"
            for thread in threading.enumerate()
        )
        await notifier.stop()

    asyncio.run(scenario())


def test_notifier_stop_wakes_high_backoff_wait(monkeypatch) -> None:  # noqa: ANN001
    async def scenario() -> None:
        high_backoff = threading.Event()
        waits: list[float] = []

        class FastReconnectEvent:
            def __init__(self) -> None:
                self._event = threading.Event()

            def clear(self) -> None:
                self._event.clear()

            def set(self) -> None:
                self._event.set()

            def is_set(self) -> bool:
                return self._event.is_set()

            def wait(self, timeout: float | None = None) -> bool:
                if timeout is None or timeout < 60:
                    if timeout is not None:
                        waits.append(timeout)
                    return False
                waits.append(timeout)
                high_backoff.set()
                return self._event.wait(timeout)

        class FakeApi:
            host = "example.com"

            async def async_get_authorization_header(self) -> str:
                return "token"

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        class FailingCall:
            def __iter__(self):  # noqa: ANN204
                raise grpc.RpcError()

            def cancel(self) -> None:
                return None

        class FakeChannel:
            def stream_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: FailingCall()

            def unary_unary(self, *args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                return lambda *call_args, **call_kwargs: None

            def close(self) -> None:
                return None

        monkeypatch.setattr(
            notifier_module.grpc,
            "secure_channel",
            lambda *args, **kwargs: FakeChannel(),
        )
        coordinator = SimpleNamespace(
            data=SimpleNamespace(
                system=SimpleNamespace(system_id="system"),
                hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
            ),
            name="Quilt Backoff",
        )
        hass = SimpleNamespace(loop=asyncio.get_running_loop())
        notifier = QuiltNotifier(hass, api=FakeApi(), coordinator=coordinator)
        notifier._desired_topics = {"topic"}  # noqa: SLF001
        notifier._reconnect = FastReconnectEvent()  # noqa: SLF001
        worker = threading.Thread(
            target=notifier._run_thread, daemon=True
        )  # noqa: SLF001
        notifier._thread = worker  # noqa: SLF001
        worker.start()

        assert await asyncio.to_thread(high_backoff.wait, 2)
        await notifier.stop()

        assert not worker.is_alive()
        assert waits[:6] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
        assert waits[6] == 60.0

    asyncio.run(scenario())


def test_notifier_auth_timeout_cancels_task_before_discard(
    monkeypatch,
) -> None:  # noqa: ANN001
    async def scenario() -> None:
        auth_started = threading.Event()
        auth_cancelled = threading.Event()

        class FakeApi:
            host = "example.com"

            async def async_get_authorization_header(self) -> str:
                auth_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    auth_cancelled.set()
                    raise

            def grpc_channel_options(self) -> list[tuple[str, int | str]]:
                return []

        coordinator = SimpleNamespace(
            data=SimpleNamespace(
                system=SimpleNamespace(system_id="system"),
                hds=SimpleNamespace(notifier_topics=lambda: {"topic"}),
            ),
            name="Quilt Auth Timeout",
        )
        hass = SimpleNamespace(loop=asyncio.get_running_loop())
        notifier = QuiltNotifier(hass, api=FakeApi(), coordinator=coordinator)
        notifier._desired_topics = {"topic"}  # noqa: SLF001
        worker = threading.Thread(
            target=notifier._run_thread, daemon=True
        )  # noqa: SLF001
        notifier._thread = worker  # noqa: SLF001
        monkeypatch.setattr(notifier_module, "AUTH_TIMEOUT_SECONDS", 0.05)
        worker.start()

        assert await asyncio.to_thread(auth_started.wait, 2)
        assert await asyncio.to_thread(auth_cancelled.wait, 2)
        await notifier.stop()

        assert not worker.is_alive()
        with notifier._lifecycle_lock:  # noqa: SLF001
            assert not notifier._auth_waiters  # noqa: SLF001
        assert not notifier._auth_tasks  # noqa: SLF001

    asyncio.run(scenario())
