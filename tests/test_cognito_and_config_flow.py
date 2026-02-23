from __future__ import annotations

import asyncio

import custom_components.quilt.cognito as cognito
from custom_components.quilt.config_flow import QuiltConfigFlow, QuiltOptionsFlowHandler
from custom_components.quilt.const import CONF_ENABLE_NOTIFIER, CONF_EMAIL, CONF_ID_TOKEN, CONF_REFRESH_TOKEN


def test_cognito_initiate_and_respond(monkeypatch) -> None:  # noqa: ANN001
    calls: list[tuple[str, str]] = []

    async def fake_post(session, target: str, payload):  # noqa: ANN001
        calls.append((target, payload.get("AuthFlow") or payload.get("ChallengeName") or ""))
        if "InitiateAuth" in target and payload.get("AuthFlow") == "CUSTOM_AUTH":
            return {
                "ChallengeName": "CUSTOM_CHALLENGE",
                "ChallengeParameters": {"USERNAME": "u"},
                "Session": "sess",
            }
        if "RespondToAuthChallenge" in target:
            return {"AuthenticationResult": {"IdToken": "id", "RefreshToken": "ref"}}
        raise AssertionError("unexpected target")

    monkeypatch.setattr(cognito, "_cognito_post", fake_post)

    ch = asyncio.run(cognito.initiate_custom_auth(session=None, email="e@example.com"))  # type: ignore[arg-type]
    assert ch.session == "sess"
    assert ch.username == "u"

    tokens = asyncio.run(
        cognito.respond_to_custom_challenge(session=None, challenge=ch, answer="123")  # type: ignore[arg-type]
    )
    assert tokens.id_token == "id"
    assert tokens.refresh_token == "ref"
    assert calls


def test_config_flow_happy_path(monkeypatch) -> None:  # noqa: ANN001
    # Patch Cognito calls used by the flow.
    async def fake_initiate(session, email: str):  # noqa: ANN001
        return cognito.CognitoChallenge(session="sess", username="u")

    async def fake_respond(session, *, challenge, answer: str):  # noqa: ANN001
        assert answer == "123456"
        return cognito.CognitoTokens(id_token="id", refresh_token="ref")

    monkeypatch.setattr("custom_components.quilt.config_flow.initiate_custom_auth", fake_initiate)
    monkeypatch.setattr("custom_components.quilt.config_flow.respond_to_custom_challenge", fake_respond)

    flow = QuiltConfigFlow()
    # In real HA, hass/context are injected; in tests we supply minimal values.
    from homeassistant.core import HomeAssistant

    flow.hass = HomeAssistant()
    flow.context = {}

    res1 = asyncio.run(flow.async_step_user({CONF_EMAIL: "me@example.com", "accept_terms": True}))
    assert res1["type"] in ("form", "create_entry")

    res2 = asyncio.run(flow.async_step_code({"code": "123456"}))
    assert res2["type"] == "create_entry"
    data = res2["data"]
    assert data[CONF_EMAIL] == "me@example.com"
    assert data[CONF_ID_TOKEN] == "id"
    assert data[CONF_REFRESH_TOKEN] == "ref"


def test_config_flow_invalid_code(monkeypatch) -> None:  # noqa: ANN001
    async def fake_initiate(session, email: str):  # noqa: ANN001
        return cognito.CognitoChallenge(session="sess", username="u")

    async def fake_respond(session, *, challenge, answer: str):  # noqa: ANN001
        raise cognito.CognitoError("bad")

    monkeypatch.setattr("custom_components.quilt.config_flow.initiate_custom_auth", fake_initiate)
    monkeypatch.setattr("custom_components.quilt.config_flow.respond_to_custom_challenge", fake_respond)

    flow = QuiltConfigFlow()
    from homeassistant.core import HomeAssistant

    flow.hass = HomeAssistant()
    flow.context = {}
    asyncio.run(flow.async_step_user({CONF_EMAIL: "me@example.com", "accept_terms": True}))
    res = asyncio.run(flow.async_step_code({"code": "000"}))
    assert res["type"] == "form"
    assert res["errors"]["base"] == "invalid_code"


def test_config_flow_user_auth_failed(monkeypatch) -> None:  # noqa: ANN001
    async def fake_initiate(session, email: str):  # noqa: ANN001
        raise cognito.CognitoError("nope")

    monkeypatch.setattr("custom_components.quilt.config_flow.initiate_custom_auth", fake_initiate)

    flow = QuiltConfigFlow()
    from homeassistant.core import HomeAssistant

    flow.hass = HomeAssistant()
    flow.context = {}
    res = asyncio.run(flow.async_step_user({CONF_EMAIL: "me@example.com", "accept_terms": True}))
    assert res["type"] == "form"
    assert res["errors"]["base"] == "auth_failed"


def test_config_flow_user_unknown_error(monkeypatch) -> None:  # noqa: ANN001
    async def fake_initiate(session, email: str):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr("custom_components.quilt.config_flow.initiate_custom_auth", fake_initiate)

    flow = QuiltConfigFlow()
    from homeassistant.core import HomeAssistant

    flow.hass = HomeAssistant()
    flow.context = {}
    res = asyncio.run(flow.async_step_user({CONF_EMAIL: "me@example.com", "accept_terms": True}))
    assert res["type"] == "form"
    assert res["errors"]["base"] == "unknown"


def test_config_flow_requires_accept_terms() -> None:
    flow = QuiltConfigFlow()
    from homeassistant.core import HomeAssistant

    flow.hass = HomeAssistant()
    flow.context = {}
    res = asyncio.run(flow.async_step_user({CONF_EMAIL: "me@example.com", "accept_terms": False}))
    assert res["type"] == "form"
    assert res["errors"]["base"] == "accept_terms"


def test_config_flow_code_aborts_if_missing_challenge_state() -> None:
    flow = QuiltConfigFlow()
    from homeassistant.core import HomeAssistant

    flow.hass = HomeAssistant()
    flow.context = {}
    res = asyncio.run(flow.async_step_code({"code": "123"}))
    assert res["type"] == "abort"
    assert res["reason"] == "unknown"


def test_config_flow_reauth_success(monkeypatch) -> None:  # noqa: ANN001
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntry

    entry = ConfigEntry()
    entry.entry_id = "eid"
    entry.data = {CONF_EMAIL: "me@example.com", CONF_ID_TOKEN: "old", CONF_REFRESH_TOKEN: "oldr"}

    hass = HomeAssistant()

    class _CE:
        def async_get_entry(self, entry_id):  # noqa: ANN001
            return entry if entry_id == "eid" else None

        def async_update_entry(self, ent, data=None, options=None):  # noqa: ANN001
            ent.data = data

        async def async_reload(self, entry_id):  # noqa: ANN001
            return None

    hass.config_entries = _CE()

    async def fake_initiate(session, email: str):  # noqa: ANN001
        return cognito.CognitoChallenge(session="sess", username="u")

    async def fake_respond(session, *, challenge, answer: str):  # noqa: ANN001
        return cognito.CognitoTokens(id_token="newid", refresh_token="newref")

    monkeypatch.setattr("custom_components.quilt.config_flow.initiate_custom_auth", fake_initiate)
    monkeypatch.setattr("custom_components.quilt.config_flow.respond_to_custom_challenge", fake_respond)

    flow = QuiltConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": "eid"}
    r = asyncio.run(flow.async_step_reauth(None))
    assert r["type"] == "form" or r["type"] == "abort"
    r2 = asyncio.run(flow.async_step_reauth_code({"code": "123"}))
    assert r2["type"] == "abort"
    assert r2["reason"] == "reauth_successful"


def test_config_flow_reauth_aborts_if_entry_missing() -> None:
    from homeassistant.core import HomeAssistant

    flow = QuiltConfigFlow()
    flow.hass = HomeAssistant()
    flow.context = {"entry_id": "missing"}
    res = asyncio.run(flow.async_step_reauth(None))
    assert res["type"] == "abort"
    assert res["reason"] == "unknown"


def test_config_flow_reauth_shows_auth_failed(monkeypatch) -> None:  # noqa: ANN001
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntry

    entry = ConfigEntry()
    entry.entry_id = "eid"
    entry.data = {CONF_EMAIL: "me@example.com", CONF_ID_TOKEN: "old", CONF_REFRESH_TOKEN: "oldr"}

    hass = HomeAssistant()

    class _CE:
        def async_get_entry(self, entry_id):  # noqa: ANN001
            return entry if entry_id == "eid" else None

        def async_update_entry(self, ent, data=None, options=None):  # noqa: ANN001
            ent.data = data

        async def async_reload(self, entry_id):  # noqa: ANN001
            return None

    hass.config_entries = _CE()

    async def fake_initiate(session, email: str):  # noqa: ANN001
        raise cognito.CognitoError("nope")

    monkeypatch.setattr("custom_components.quilt.config_flow.initiate_custom_auth", fake_initiate)

    flow = QuiltConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": "eid"}
    res = asyncio.run(flow.async_step_reauth(None))
    assert res["type"] == "form"
    assert res["errors"]["base"] == "auth_failed"


def test_config_flow_reauth_code_aborts_if_missing_state() -> None:
    from homeassistant.core import HomeAssistant

    flow = QuiltConfigFlow()
    flow.hass = HomeAssistant()
    flow.context = {}
    res = asyncio.run(flow.async_step_reauth_code({"code": "123"}))
    assert res["type"] == "abort"
    assert res["reason"] == "unknown"


def test_config_flow_reauth_code_unknown_error(monkeypatch) -> None:  # noqa: ANN001
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntry

    entry = ConfigEntry()
    entry.entry_id = "eid"
    entry.data = {CONF_EMAIL: "me@example.com", CONF_ID_TOKEN: "old", CONF_REFRESH_TOKEN: "oldr"}

    hass = HomeAssistant()

    class _CE:
        def async_get_entry(self, entry_id):  # noqa: ANN001
            return entry if entry_id == "eid" else None

        def async_update_entry(self, ent, data=None, options=None):  # noqa: ANN001
            ent.data = data

        async def async_reload(self, entry_id):  # noqa: ANN001
            return None

    hass.config_entries = _CE()

    async def fake_initiate(session, email: str):  # noqa: ANN001
        return cognito.CognitoChallenge(session="sess", username="u")

    async def fake_respond(session, *, challenge, answer: str):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr("custom_components.quilt.config_flow.initiate_custom_auth", fake_initiate)
    monkeypatch.setattr("custom_components.quilt.config_flow.respond_to_custom_challenge", fake_respond)

    flow = QuiltConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": "eid"}
    asyncio.run(flow.async_step_reauth(None))
    res = asyncio.run(flow.async_step_reauth_code({"code": "123"}))
    assert res["type"] == "form"
    assert res["errors"]["base"] == "unknown"


def test_options_flow(monkeypatch) -> None:  # noqa: ANN001
    from homeassistant.config_entries import ConfigEntry

    entry = ConfigEntry()
    entry.options = {CONF_ENABLE_NOTIFIER: True}
    handler = QuiltOptionsFlowHandler(entry)
    res = asyncio.run(handler.async_step_init(None))
    assert res["type"] == "form"
    res2 = asyncio.run(handler.async_step_init({CONF_ENABLE_NOTIFIER: False}))
    assert res2["type"] == "create_entry"
    assert res2["data"][CONF_ENABLE_NOTIFIER] is False
