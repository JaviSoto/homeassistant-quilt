from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientConnectionError, ClientConnectorError

from custom_components.quilt.cognito import (
    CognitoAuthError,
    CognitoChallenge,
    CognitoError,
    _cognito_post,
    initiate_custom_auth,
    refresh_with_refresh_token,
    respond_to_custom_challenge,
)


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


class _FailingSess:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def post(self, *args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs
        raise self._error


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


@pytest.mark.parametrize(
    "error_type",
    [
        "NotAuthorizedException",
        "CodeMismatchException",
        "ExpiredCodeException",
        "UserNotFoundException",
    ],
)
def test_cognito_post_maps_credential_errors_to_auth_error(error_type: str) -> None:
    sess = _Sess(
        _Resp(
            status=400,
            text=f'{{"__type":"com.amazonaws.cognitoidentityprovider#{error_type}"}}',
        )
    )

    with pytest.raises(CognitoAuthError):
        asyncio.run(_cognito_post(sess, "X", {"a": 1}))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(400, "TooManyRequestsException"), (500, "InternalErrorException")],
)
def test_cognito_post_keeps_service_errors_non_auth(
    status: int, error_type: str
) -> None:
    sess = _Sess(_Resp(status=status, text=f'{{"__type":"{error_type}"}}'))

    with pytest.raises(CognitoError) as error:
        asyncio.run(_cognito_post(sess, "X", {"a": 1}))  # type: ignore[arg-type]

    assert not isinstance(error.value, CognitoAuthError)


@pytest.mark.parametrize("text", ["", "{"])
def test_cognito_refresh_protocol_errors_are_non_auth(text: str) -> None:
    sess = _Sess(_Resp(status=200, text=text))

    with pytest.raises(CognitoError) as error:
        asyncio.run(refresh_with_refresh_token(sess, refresh_token="refresh"))  # type: ignore[arg-type]

    assert not isinstance(error.value, CognitoAuthError)


@pytest.mark.parametrize(
    "error",
    [
        ClientConnectionError("connection lost"),
        ClientConnectorError(None, OSError("down")),
        TimeoutError("request timed out"),
    ],
)
def test_cognito_post_wraps_transport_and_timeout_errors(
    error: BaseException,
) -> None:
    with pytest.raises(CognitoError) as wrapped:
        asyncio.run(
            _cognito_post(_FailingSess(error), "X", {"a": 1})  # type: ignore[arg-type]
        )

    assert not isinstance(wrapped.value, CognitoAuthError)


def test_cognito_initiate_rejects_missing_username_or_session(
    monkeypatch,
) -> None:  # noqa: ANN001
    import custom_components.quilt.cognito as mod

    async def fake_post(session, target, payload):  # noqa: ANN001
        return {
            "ChallengeName": "CUSTOM_CHALLENGE",
            "ChallengeParameters": {},
            "Session": None,
        }

    monkeypatch.setattr(mod, "_cognito_post", fake_post)
    with pytest.raises(CognitoError):
        asyncio.run(initiate_custom_auth(session=None, email="x"))  # type: ignore[arg-type]


def test_cognito_200_next_challenge_without_auth_result_is_auth_error(
    monkeypatch,
) -> None:  # noqa: ANN001
    import custom_components.quilt.cognito as mod

    async def fake_post(session, target, payload):  # noqa: ANN001
        return {"ChallengeName": "CUSTOM_CHALLENGE"}

    monkeypatch.setattr(mod, "_cognito_post", fake_post)
    with pytest.raises(CognitoAuthError):
        asyncio.run(
            respond_to_custom_challenge(
                session=None,
                challenge=CognitoChallenge(session="s", username="u"),
                answer="wrong",
            )  # type: ignore[arg-type]
        )
