from __future__ import annotations

import asyncio

import pytest

from custom_components.quilt.cognito import CognitoError, _cognito_post, initiate_custom_auth


class _Resp:
    def __init__(self, *, status: int, text: str) -> None:
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


class _Sess:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp
        self.calls: list[tuple[str, dict[str, str], str]] = []

    def post(self, url: str, *, headers=None, data=None, timeout=None):  # noqa: ANN001
        assert timeout == 30
        self.calls.append((url, dict(headers or {}), str(data or "")))
        return self._resp


def test_cognito_post_raises_on_http_error() -> None:
    sess = _Sess(_Resp(status=400, text="bad"))
    with pytest.raises(CognitoError) as e:
        asyncio.run(_cognito_post(sess, "X", {"a": 1}))  # type: ignore[arg-type]
    assert "400" in str(e.value)
    assert sess.calls


def test_cognito_post_empty_body_returns_empty_dict() -> None:
    sess = _Sess(_Resp(status=200, text=""))
    data = asyncio.run(_cognito_post(sess, "X", {"a": 1}))  # type: ignore[arg-type]
    assert data == {}


def test_cognito_initiate_rejects_missing_username_or_session(monkeypatch) -> None:  # noqa: ANN001
    import custom_components.quilt.cognito as mod

    async def fake_post(session, target, payload):  # noqa: ANN001
        return {"ChallengeName": "CUSTOM_CHALLENGE", "ChallengeParameters": {}, "Session": None}

    monkeypatch.setattr(mod, "_cognito_post", fake_post)
    with pytest.raises(CognitoError):
        asyncio.run(initiate_custom_auth(session=None, email="x"))  # type: ignore[arg-type]

