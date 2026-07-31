from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

import custom_components.quilt as quilt_module
from custom_components.quilt import async_unload_entry
from custom_components.quilt.const import CONF_ENABLE_NOTIFIER, DOMAIN


def test_failed_platform_unload_preserves_runtime() -> None:
    class FakeNotifier:
        def __init__(self) -> None:
            self.stop_count = 0

        async def stop(self) -> None:
            self.stop_count += 1

    class FakeApi:
        def __init__(self) -> None:
            self.close_count = 0

        async def async_close(self) -> None:
            self.close_count += 1

    class FakeConfigEntries:
        async def async_unload_platforms(
            self, entry, platforms
        ) -> bool:  # noqa: ANN001
            del entry, platforms
            return False

    class FakeHass:
        def __init__(self) -> None:
            self.notifier = FakeNotifier()
            self.api = FakeApi()
            self.config_entries = FakeConfigEntries()
            self.data = {
                DOMAIN: {
                    "entry-id": {
                        "api": self.api,
                        "notifiers": {"system": self.notifier},
                    }
                }
            }

    hass = FakeHass()
    entry = type("Entry", (), {"entry_id": "entry-id"})()

    result = asyncio.run(async_unload_entry(hass, entry))

    assert result is False
    assert "entry-id" in hass.data[DOMAIN]
    assert hass.notifier.stop_count == 0
    assert hass.api.close_count == 0


def test_notifier_stop_failure_preserves_runtime_ownership() -> None:
    class FailingNotifier:
        async def stop(self) -> None:
            raise RuntimeError("worker survived shutdown")

    class FakeApi:
        def __init__(self) -> None:
            self.close_count = 0

        async def async_close(self) -> None:
            self.close_count += 1

    class FakeConfigEntries:
        async def async_unload_platforms(
            self, entry, platforms
        ) -> bool:  # noqa: ANN001
            del entry, platforms
            return True

    api = FakeApi()
    runtime = {"api": api, "notifiers": {"system": FailingNotifier()}}
    hass = type(
        "Hass",
        (),
        {
            "config_entries": FakeConfigEntries(),
            "data": {DOMAIN: {"entry-id": runtime}},
        },
    )()
    entry = type("Entry", (), {"entry_id": "entry-id"})()

    assert asyncio.run(async_unload_entry(hass, entry)) is False

    assert hass.data[DOMAIN]["entry-id"] is runtime
    assert api.close_count == 0


def test_pre_start_unload_unregisters_notifier_start_callback(
    monkeypatch,
) -> None:  # noqa: ANN001
    class FakeBus:
        def __init__(self) -> None:
            self.listeners: list[object] = []

        def async_listen_once(self, event, callback):  # noqa: ANN001
            del event
            self.listeners.append(callback)

            def unsubscribe() -> None:
                if callback not in self.listeners:
                    raise RuntimeError("Unable to remove unknown job listener")
                self.listeners.remove(callback)

            return unsubscribe

        async def fire_started(self) -> None:
            for callback in list(self.listeners):
                await callback(object())

    class FakeEntry:
        entry_id = "entry-id"
        data = {
            "email": "test@example.com",
            "id_token": "id",
            "refresh_token": "refresh",
        }
        options = {CONF_ENABLE_NOTIFIER: True}

        def __init__(self) -> None:
            self.unload_callbacks: list[object] = []

        def async_on_unload(self, callback) -> None:  # noqa: ANN001
            self.unload_callbacks.append(callback)

        def add_update_listener(self, callback):  # noqa: ANN001
            del callback
            return lambda: None

    class FakeConfigEntries:
        async def async_forward_entry_setups(
            self, entry, platforms
        ) -> None:  # noqa: ANN001
            del entry, platforms

        async def async_unload_platforms(
            self, entry, platforms
        ) -> bool:  # noqa: ANN001
            del entry, platforms
            return True

    class FakeHass:
        is_running = False
        state = quilt_module.CoreState.starting

        def __init__(self) -> None:
            self.bus = FakeBus()
            self.config_entries = FakeConfigEntries()
            self.config = SimpleNamespace(path=lambda name: f"/tmp/{name}")
            self.data: dict = {}

    class FakeApi:
        def __init__(
            self, config, *, aiohttp_session, token_update_callback
        ) -> None:  # noqa: ANN001
            del config, aiohttp_session, token_update_callback

        async def async_connect(self) -> None:
            return None

        async def async_list_systems(self):  # noqa: ANN201
            return [SimpleNamespace(system_id="system", name="Test", timezone="UTC")]

        async def async_close(self) -> None:
            return None

    class FakeCoordinator:
        def __init__(
            self, hass, *, api, system, config_entry, poll_interval_seconds
        ) -> None:  # noqa: ANN001
            del hass, api, config_entry, poll_interval_seconds
            self.name = system.name

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeEnergyCoordinator:
        def __init__(self, hass, *, api, system, config_entry) -> None:  # noqa: ANN001
            del hass, api, system, config_entry

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeNotifier:
        started = 0

        def __init__(
            self, hass, *, api, coordinator, debug_dir
        ) -> None:  # noqa: ANN001
            del hass, api, coordinator, debug_dir

        def start(self) -> None:
            type(self).started += 1

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(quilt_module, "QuiltApi", FakeApi)
    monkeypatch.setattr(quilt_module, "QuiltCoordinator", FakeCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltEnergyCoordinator", FakeEnergyCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltNotifier", FakeNotifier)
    monkeypatch.setattr(quilt_module, "async_get_clientsession", lambda hass: None)

    hass = FakeHass()
    entry = FakeEntry()
    asyncio.run(quilt_module.async_setup_entry(hass, entry))

    assert FakeNotifier.started == 0
    for callback in entry.unload_callbacks:
        callback()
    asyncio.run(hass.bus.fire_started())

    assert FakeNotifier.started == 0

    started_hass = FakeHass()
    started_entry = FakeEntry()
    asyncio.run(quilt_module.async_setup_entry(started_hass, started_entry))
    asyncio.run(started_hass.bus.fire_started())

    assert FakeNotifier.started == 1
    for callback in started_entry.unload_callbacks:
        callback()


@pytest.mark.parametrize(
    "failure",
    [
        "list",
        "auth",
        "refresh",
        "refresh_auth",
        "forward",
        "forward_unload_false",
        "notifier_stop",
        "api_close",
    ],
)
def test_setup_failure_rolls_back_runtime_and_api(
    monkeypatch, failure: str
) -> None:  # noqa: ANN001
    class FakeEntry:
        entry_id = "entry-id"
        data = {
            "email": "test@example.com",
            "id_token": "id",
            "refresh_token": "refresh",
        }
        options = {CONF_ENABLE_NOTIFIER: True}

        def __init__(self) -> None:
            self.unload_callbacks: list[object] = []

        def async_on_unload(self, callback) -> None:  # noqa: ANN001
            self.unload_callbacks.append(callback)

        def add_update_listener(self, callback):  # noqa: ANN001
            del callback
            return lambda: None

    class FakeApi:
        instance: FakeApi | None = None

        def __init__(
            self, config, *, aiohttp_session, token_update_callback
        ) -> None:  # noqa: ANN001
            del config, aiohttp_session, token_update_callback
            self.close_count = 0
            type(self).instance = self

        async def async_connect(self) -> None:
            return None

        async def async_list_systems(self):  # noqa: ANN201
            if failure == "list":
                raise RuntimeError("list failed")
            if failure == "auth":
                raise ConfigEntryAuthFailed("invalid refresh token")
            return [SimpleNamespace(system_id="system", name="Test", timezone="UTC")]

        async def async_close(self) -> None:
            self.close_count += 1
            if failure == "api_close":
                raise RuntimeError("API close failed")

    class FakeCoordinator:
        def __init__(
            self, hass, *, api, system, config_entry, poll_interval_seconds
        ) -> None:  # noqa: ANN001
            del hass, api, config_entry, poll_interval_seconds
            self.name = system.name

        async def async_config_entry_first_refresh(self) -> None:
            if failure == "refresh":
                raise RuntimeError("refresh failed")
            if failure == "refresh_auth":
                raise ConfigEntryAuthFailed("invalid refresh token")

    class FakeEnergyCoordinator:
        def __init__(self, hass, *, api, system, config_entry) -> None:  # noqa: ANN001
            del hass, api, system, config_entry

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeNotifier:
        instances: list[FakeNotifier] = []

        def __init__(
            self, hass, *, api, coordinator, debug_dir
        ) -> None:  # noqa: ANN001
            del hass, api, coordinator, debug_dir
            self.start_count = 0
            self.stop_count = 0
            type(self).instances.append(self)

        def start(self) -> None:
            self.start_count += 1
            if failure in {"forward_unload_false", "notifier_stop", "api_close"}:
                raise RuntimeError("notifier start failed")

        async def stop(self) -> None:
            self.stop_count += 1
            if failure == "notifier_stop":
                raise RuntimeError("notifier stop failed")

    class FakeConfigEntries:
        def __init__(self) -> None:
            self.unload_calls = 0

        async def async_forward_entry_setups(
            self, entry, platforms
        ) -> None:  # noqa: ANN001
            del entry, platforms
            if failure == "forward":
                raise RuntimeError("forward failed")

        async def async_unload_platforms(
            self, entry, platforms
        ) -> bool:  # noqa: ANN001
            del entry, platforms
            self.unload_calls += 1
            return failure != "forward_unload_false"

    class FakeHass:
        is_running = True
        state = quilt_module.CoreState.running

        def __init__(self) -> None:
            self.config_entries = FakeConfigEntries()
            self.config = SimpleNamespace(path=lambda name: f"/tmp/{name}")
            self.data: dict = {}

    monkeypatch.setattr(quilt_module, "QuiltApi", FakeApi)
    monkeypatch.setattr(quilt_module, "QuiltCoordinator", FakeCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltEnergyCoordinator", FakeEnergyCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltNotifier", FakeNotifier)
    monkeypatch.setattr(quilt_module, "async_get_clientsession", lambda hass: None)

    hass = FakeHass()
    entry = FakeEntry()
    if failure == "auth":
        error_type = ConfigEntryAuthFailed
    elif failure in {"list", "refresh"}:
        error_type = ConfigEntryNotReady
    elif failure == "refresh_auth":
        error_type = ConfigEntryAuthFailed
    else:
        error_type = RuntimeError

    with pytest.raises(error_type):
        asyncio.run(quilt_module.async_setup_entry(hass, entry))

    assert FakeApi.instance is not None
    if failure in {"forward_unload_false", "notifier_stop", "api_close"}:
        runtime = hass.data[DOMAIN][entry.entry_id]
        assert runtime["api"] is FakeApi.instance
        assert FakeApi.instance.close_count == (1 if failure == "api_close" else 0)
        assert FakeNotifier.instances[0].start_count == 1
        assert FakeNotifier.instances[0].stop_count == (
            0 if failure == "forward_unload_false" else 1
        )
        assert hass.config_entries.unload_calls == 1
        return

    assert FakeApi.instance.close_count == 1
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert all(notifier.start_count == 0 for notifier in FakeNotifier.instances)
    assert all(notifier.stop_count == 0 for notifier in FakeNotifier.instances)
    if failure == "forward":
        assert hass.config_entries.unload_calls == 1


def _retry_setup_harness(monkeypatch):  # noqa: ANN001
    state = SimpleNamespace(
        list_error=None,
        notifier_start_error=False,
        notifier_stop_error=False,
        api_close_error=False,
        unload_result=True,
    )

    class FakeEntry:
        entry_id = "entry-id"
        data = {
            "email": "test@example.com",
            "id_token": "id",
            "refresh_token": "refresh",
        }
        options = {CONF_ENABLE_NOTIFIER: True}

        def __init__(self) -> None:
            self.unload_callbacks: list[object] = []

        def async_on_unload(self, callback) -> None:  # noqa: ANN001
            self.unload_callbacks.append(callback)

        def add_update_listener(self, callback):  # noqa: ANN001
            del callback
            return lambda: None

    class FakeApi:
        instances: list[FakeApi] = []

        def __init__(
            self, config, *, aiohttp_session, token_update_callback
        ) -> None:  # noqa: ANN001
            del config, aiohttp_session, token_update_callback
            self.close_count = 0
            type(self).instances.append(self)

        async def async_connect(self) -> None:
            return None

        async def async_list_systems(self):  # noqa: ANN201
            if state.list_error is not None:
                raise state.list_error
            return [SimpleNamespace(system_id="system", name="Test", timezone="UTC")]

        async def async_close(self) -> None:
            self.close_count += 1
            if state.api_close_error:
                raise RuntimeError("API close failed")

    class FakeCoordinator:
        def __init__(
            self, hass, *, api, system, config_entry, poll_interval_seconds
        ) -> None:  # noqa: ANN001
            del hass, api, config_entry, poll_interval_seconds
            self.name = system.name

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeEnergyCoordinator:
        def __init__(self, hass, *, api, system, config_entry) -> None:  # noqa: ANN001
            del hass, api, system, config_entry

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeNotifier:
        instances: list[FakeNotifier] = []

        def __init__(
            self, hass, *, api, coordinator, debug_dir
        ) -> None:  # noqa: ANN001
            del hass, api, coordinator, debug_dir
            self.stop_count = 0
            type(self).instances.append(self)

        def start(self) -> None:
            if state.notifier_start_error:
                raise RuntimeError("notifier start failed")

        async def stop(self) -> None:
            self.stop_count += 1
            if state.notifier_stop_error:
                raise RuntimeError("notifier stop failed")

    class FakeConfigEntries:
        def __init__(self) -> None:
            self.unload_calls = 0

        async def async_forward_entry_setups(
            self, entry, platforms
        ) -> None:  # noqa: ANN001
            del entry, platforms

        async def async_unload_platforms(
            self, entry, platforms
        ) -> bool:  # noqa: ANN001
            del entry, platforms
            self.unload_calls += 1
            return state.unload_result

    class FakeHass:
        state = quilt_module.CoreState.running

        def __init__(self) -> None:
            self.config_entries = FakeConfigEntries()
            self.config = SimpleNamespace(path=lambda name: f"/tmp/{name}")
            self.data: dict = {}

    monkeypatch.setattr(quilt_module, "QuiltApi", FakeApi)
    monkeypatch.setattr(quilt_module, "QuiltCoordinator", FakeCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltEnergyCoordinator", FakeEnergyCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltNotifier", FakeNotifier)
    monkeypatch.setattr(quilt_module, "async_get_clientsession", lambda hass: None)
    return SimpleNamespace(
        entry=FakeEntry(),
        hass=FakeHass(),
        state=state,
        apis=FakeApi.instances,
        notifiers=FakeNotifier.instances,
    )


def test_setup_retry_tears_down_old_runtime_before_replacement(
    monkeypatch,
) -> None:  # noqa: ANN001
    harness = _retry_setup_harness(monkeypatch)

    asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))
    old_runtime = harness.hass.data[DOMAIN][harness.entry.entry_id]
    old_api = harness.apis[0]
    old_notifier = harness.notifiers[0]

    asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))

    new_runtime = harness.hass.data[DOMAIN][harness.entry.entry_id]
    assert new_runtime is not old_runtime
    assert new_runtime["api"] is harness.apis[1]
    assert old_notifier.stop_count == 1
    assert old_api.close_count == 1
    assert harness.hass.config_entries.unload_calls == 1


def test_setup_retry_preserves_old_runtime_when_teardown_fails(
    monkeypatch,
) -> None:  # noqa: ANN001
    harness = _retry_setup_harness(monkeypatch)

    asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))
    old_runtime = harness.hass.data[DOMAIN][harness.entry.entry_id]
    old_api = harness.apis[0]
    harness.state.notifier_stop_error = True

    with pytest.raises(ConfigEntryNotReady, match="Previous Quilt runtime"):
        asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))

    assert harness.hass.data[DOMAIN][harness.entry.entry_id] is old_runtime
    assert len(harness.apis) == 1
    assert harness.notifiers[0].stop_count == 1
    assert old_api.close_count == 0


def test_setup_retains_api_when_precompletion_cleanup_close_fails(
    monkeypatch,
) -> None:  # noqa: ANN001
    harness = _retry_setup_harness(monkeypatch)
    harness.state.list_error = RuntimeError("list failed")
    harness.state.api_close_error = True

    with pytest.raises(ConfigEntryNotReady, match="list systems"):
        asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))

    runtime = harness.hass.data[DOMAIN][harness.entry.entry_id]
    assert runtime["api"] is harness.apis[0]
    assert harness.apis[0].close_count == 1


@pytest.mark.parametrize(
    "failure",
    [
        "platform_unload_false",
        "notifier_stop",
        "api_close",
        "pre_forward_close",
    ],
)
def test_failed_setup_runtime_can_retry_exact_owned_resources(
    monkeypatch, failure: str
) -> None:  # noqa: ANN001
    harness = _retry_setup_harness(monkeypatch)
    harness.state.notifier_start_error = failure != "pre_forward_close"
    harness.state.unload_result = failure != "platform_unload_false"
    harness.state.notifier_stop_error = failure == "notifier_stop"
    harness.state.api_close_error = failure in {"api_close", "pre_forward_close"}
    if failure == "pre_forward_close":
        harness.state.list_error = RuntimeError("list failed")

    expected_error = (
        ConfigEntryNotReady if failure == "pre_forward_close" else RuntimeError
    )
    with pytest.raises(expected_error):
        asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))

    old_runtime = harness.hass.data[DOMAIN][harness.entry.entry_id]
    old_api = harness.apis[0]
    old_notifier = harness.notifiers[0] if harness.notifiers else None

    harness.state.notifier_start_error = False
    harness.state.unload_result = True
    harness.state.notifier_stop_error = False
    harness.state.api_close_error = False
    harness.state.list_error = None
    asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))

    new_runtime = harness.hass.data[DOMAIN][harness.entry.entry_id]
    assert new_runtime is not old_runtime
    assert new_runtime["api"] is harness.apis[1]
    assert old_api.close_count == (
        2 if failure in {"api_close", "pre_forward_close"} else 1
    )
    if old_notifier is not None:
        assert old_notifier.stop_count == (2 if failure == "notifier_stop" else 1)


def test_failed_setup_retry_preserves_exact_runtime_on_persistent_teardown_failure(
    monkeypatch,
) -> None:  # noqa: ANN001
    harness = _retry_setup_harness(monkeypatch)
    harness.state.notifier_start_error = True
    harness.state.notifier_stop_error = True

    with pytest.raises(RuntimeError):
        asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))
    old_runtime = harness.hass.data[DOMAIN][harness.entry.entry_id]

    with pytest.raises(ConfigEntryNotReady, match="Previous Quilt runtime"):
        asyncio.run(quilt_module.async_setup_entry(harness.hass, harness.entry))

    assert harness.hass.data[DOMAIN][harness.entry.entry_id] is old_runtime
    assert len(harness.apis) == 1


def test_setup_retry_preserves_listener_handle_when_unsubscribe_fails_once(
    monkeypatch,
) -> None:  # noqa: ANN001
    class FakeBus:
        def __init__(self) -> None:
            self.listeners: list[object] = []
            self.unsubscribe_attempts = 0

        def async_listen_once(self, event, callback):  # noqa: ANN001
            del event
            self.listeners.append(callback)

            def unsubscribe() -> None:
                self.unsubscribe_attempts += 1
                if self.unsubscribe_attempts == 1:
                    raise RuntimeError("listener removal failed")
                self.listeners.remove(callback)

            return unsubscribe

    class FakeEntry:
        entry_id = "entry-id"
        data = {
            "email": "test@example.com",
            "id_token": "id",
            "refresh_token": "refresh",
        }
        options = {CONF_ENABLE_NOTIFIER: True}

        def __init__(self) -> None:
            self.unload_callbacks: list[object] = []

        def async_on_unload(self, callback) -> None:  # noqa: ANN001
            self.unload_callbacks.append(callback)

        def add_update_listener(self, callback):  # noqa: ANN001
            del callback
            return lambda: None

    class FakeApi:
        instances: list[FakeApi] = []

        def __init__(
            self, config, *, aiohttp_session, token_update_callback
        ) -> None:  # noqa: ANN001
            del config, aiohttp_session, token_update_callback
            self.close_count = 0
            type(self).instances.append(self)

        async def async_connect(self) -> None:
            return None

        async def async_list_systems(self):  # noqa: ANN201
            return [SimpleNamespace(system_id="system", name="Test", timezone="UTC")]

        async def async_close(self) -> None:
            self.close_count += 1

    class FakeCoordinator:
        def __init__(
            self, hass, *, api, system, config_entry, poll_interval_seconds
        ) -> None:  # noqa: ANN001
            del hass, api, config_entry, poll_interval_seconds
            self.name = system.name

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeEnergyCoordinator:
        def __init__(self, hass, *, api, system, config_entry) -> None:  # noqa: ANN001
            del hass, api, system, config_entry

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeNotifier:
        instances: list[FakeNotifier] = []

        def __init__(
            self, hass, *, api, coordinator, debug_dir
        ) -> None:  # noqa: ANN001
            del hass, api, coordinator, debug_dir
            self.stop_count = 0
            type(self).instances.append(self)

        def start(self) -> None:
            return None

        async def stop(self) -> None:
            self.stop_count += 1

    class FakeConfigEntries:
        def __init__(self) -> None:
            self.unload_calls = 0

        async def async_forward_entry_setups(
            self, entry, platforms
        ) -> None:  # noqa: ANN001
            del entry, platforms

        async def async_unload_platforms(
            self, entry, platforms
        ) -> bool:  # noqa: ANN001
            del entry, platforms
            self.unload_calls += 1
            return True

    class FakeHass:
        state = quilt_module.CoreState.starting

        def __init__(self) -> None:
            self.bus = FakeBus()
            self.config_entries = FakeConfigEntries()
            self.config = SimpleNamespace(path=lambda name: f"/tmp/{name}")
            self.data: dict = {}

    monkeypatch.setattr(quilt_module, "QuiltApi", FakeApi)
    monkeypatch.setattr(quilt_module, "QuiltCoordinator", FakeCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltEnergyCoordinator", FakeEnergyCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltNotifier", FakeNotifier)
    monkeypatch.setattr(quilt_module, "async_get_clientsession", lambda hass: None)

    hass = FakeHass()
    entry = FakeEntry()
    asyncio.run(quilt_module.async_setup_entry(hass, entry))
    old_runtime = hass.data[DOMAIN][entry.entry_id]
    old_api = FakeApi.instances[0]
    old_notifier = FakeNotifier.instances[0]
    assert len(hass.bus.listeners) == 1

    assert asyncio.run(quilt_module.async_unload_entry(hass, entry)) is False
    assert hass.data[DOMAIN][entry.entry_id] is old_runtime
    assert old_notifier.stop_count == 0
    assert old_api.close_count == 0
    assert len(hass.bus.listeners) == 1

    assert asyncio.run(quilt_module.async_unload_entry(hass, entry)) is True
    assert old_notifier.stop_count == 0
    assert old_api.close_count == 1
    assert hass.bus.unsubscribe_attempts == 2
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert len(FakeApi.instances) == 1


def test_unload_removes_deferred_start_listener_before_api_close(
    monkeypatch,
) -> None:  # noqa: ANN001
    class FakeBus:
        def __init__(self) -> None:
            self.listeners: list[object] = []

        def async_listen_once(self, event, callback):  # noqa: ANN001
            del event
            self.listeners.append(callback)

            def unsubscribe() -> None:
                if callback not in self.listeners:
                    raise RuntimeError("Unable to remove unknown job listener")
                self.listeners.remove(callback)

            return unsubscribe

        async def fire_started(self) -> None:
            for callback in list(self.listeners):
                await callback(object())

    class FakeEntry:
        entry_id = "entry-id"
        data = {
            "email": "test@example.com",
            "id_token": "id",
            "refresh_token": "refresh",
        }
        options = {CONF_ENABLE_NOTIFIER: True}

        def __init__(self) -> None:
            self.unload_callbacks: list[object] = []

        def async_on_unload(self, callback) -> None:  # noqa: ANN001
            self.unload_callbacks.append(callback)

        def add_update_listener(self, callback):  # noqa: ANN001
            del callback
            return lambda: None

    class FakeConfigEntries:
        async def async_forward_entry_setups(
            self, entry, platforms
        ) -> None:  # noqa: ANN001
            del entry, platforms

        async def async_unload_platforms(
            self, entry, platforms
        ) -> bool:  # noqa: ANN001
            del entry, platforms
            return True

    class FakeApi:
        def __init__(
            self, config, *, aiohttp_session, token_update_callback
        ) -> None:  # noqa: ANN001
            del config, aiohttp_session, token_update_callback
            self.close_started = threading.Event()
            self.close_release = threading.Event()

        async def async_connect(self) -> None:
            return None

        async def async_list_systems(self):  # noqa: ANN201
            return [SimpleNamespace(system_id="system", name="Test", timezone="UTC")]

        async def async_close(self) -> None:
            self.close_started.set()
            await asyncio.to_thread(self.close_release.wait, 2)

    class FakeCoordinator:
        def __init__(
            self, hass, *, api, system, config_entry, poll_interval_seconds
        ) -> None:  # noqa: ANN001
            del hass, api, config_entry, poll_interval_seconds
            self.name = system.name

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeEnergyCoordinator:
        def __init__(self, hass, *, api, system, config_entry) -> None:  # noqa: ANN001
            del hass, api, system, config_entry

        async def async_config_entry_first_refresh(self) -> None:
            return None

    class FakeNotifier:
        started = 0

        def __init__(
            self, hass, *, api, coordinator, debug_dir
        ) -> None:  # noqa: ANN001
            del hass, api, coordinator, debug_dir

        def start(self) -> None:
            type(self).started += 1

        async def stop(self) -> None:
            return None

    class FakeHass:
        state = quilt_module.CoreState.starting

        def __init__(self) -> None:
            self.bus = FakeBus()
            self.config_entries = FakeConfigEntries()
            self.config = SimpleNamespace(path=lambda name: f"/tmp/{name}")
            self.data: dict = {}

    monkeypatch.setattr(quilt_module, "QuiltApi", FakeApi)
    monkeypatch.setattr(quilt_module, "QuiltCoordinator", FakeCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltEnergyCoordinator", FakeEnergyCoordinator)
    monkeypatch.setattr(quilt_module, "QuiltNotifier", FakeNotifier)
    monkeypatch.setattr(quilt_module, "async_get_clientsession", lambda hass: None)

    async def scenario() -> None:
        hass = FakeHass()
        entry = FakeEntry()
        await quilt_module.async_setup_entry(hass, entry)
        api = hass.data[DOMAIN][entry.entry_id]["api"]
        assert FakeNotifier.started == 0
        assert len(hass.bus.listeners) == 1

        unload_task = asyncio.create_task(quilt_module.async_unload_entry(hass, entry))
        assert await asyncio.to_thread(api.close_started.wait, 2)
        await hass.bus.fire_started()
        assert FakeNotifier.started == 0
        api.close_release.set()
        assert await unload_task is True

        for callback in entry.unload_callbacks:
            callback()

    asyncio.run(scenario())
