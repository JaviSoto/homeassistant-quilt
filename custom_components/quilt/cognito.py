from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from aiohttp import ClientError, ClientSession

from .const import COGNITO_CLIENT_ID, COGNITO_HOST


@dataclass(frozen=True)
class CognitoChallenge:
    session: str
    username: str


@dataclass(frozen=True)
class CognitoTokens:
    id_token: str
    refresh_token: str


class CognitoError(RuntimeError):
    pass


class CognitoAuthError(CognitoError):
    """Cognito rejected the supplied credentials, rather than the service call."""


COGNITO_AUTH_ERROR_TYPES: Final[frozenset[str]] = frozenset(
    {
        "CodeMismatchException",
        "ExpiredCodeException",
        "NotAuthorizedException",
        "UserNotFoundException",
    }
)


def _normalize_error_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.split(":", 1)[0].rsplit("#", 1)[-1]


def _response_error_type(text: str, headers: object) -> str | None:
    try:
        payload = json.loads(text) if text else {}
    except (TypeError, ValueError):
        payload = {}

    if isinstance(payload, dict):
        for key in ("__type", "code", "errorCode"):
            error_type = _normalize_error_type(payload.get(key))
            if error_type is not None:
                return error_type

    if hasattr(headers, "get"):
        return _normalize_error_type(headers.get("x-amzn-errortype"))  # type: ignore[union-attr]
    return None


async def _cognito_post(
    session: ClientSession, target: str, payload: dict[str, Any]
) -> dict[str, Any]:
    url = f"https://{COGNITO_HOST}/"
    headers = {
        "content-type": "application/x-amz-json-1.1",
        "x-amz-target": target,
    }

    try:
        async with session.post(
            url, headers=headers, data=json.dumps(payload), timeout=30
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                error_type = _response_error_type(text, getattr(resp, "headers", None))
                error_class = (
                    CognitoAuthError
                    if error_type in COGNITO_AUTH_ERROR_TYPES
                    else CognitoError
                )
                raise error_class(f"cognito error {resp.status}: {text[:200]}")
            if not text:
                return {}
            try:
                response = json.loads(text)
            except (TypeError, ValueError) as error:
                raise CognitoError("malformed Cognito response") from error
            if not isinstance(response, dict):
                raise CognitoError("malformed Cognito response")
            return response
    except (ClientError, TimeoutError) as error:
        raise CognitoError("Cognito request failed") from error


async def initiate_custom_auth(session: ClientSession, email: str) -> CognitoChallenge:
    data = await _cognito_post(
        session,
        "AWSCognitoIdentityProviderService.InitiateAuth",
        {
            "ClientId": COGNITO_CLIENT_ID,
            "ClientMetadata": {},
            "AuthFlow": "CUSTOM_AUTH",
            "AuthParameters": {"USERNAME": email},
        },
    )

    if data.get("ChallengeName") != "CUSTOM_CHALLENGE":
        raise CognitoError(f"unexpected challenge: {data.get('ChallengeName')}")

    challenge_params = data.get("ChallengeParameters") or {}
    username = challenge_params.get("USERNAME")
    session_token = data.get("Session")
    if not username or not session_token:
        raise CognitoError("missing username/session in challenge response")

    return CognitoChallenge(session=session_token, username=username)


async def respond_to_custom_challenge(
    session: ClientSession, *, challenge: CognitoChallenge, answer: str
) -> CognitoTokens:
    data = await _cognito_post(
        session,
        "AWSCognitoIdentityProviderService.RespondToAuthChallenge",
        {
            "ChallengeName": "CUSTOM_CHALLENGE",
            "ClientId": COGNITO_CLIENT_ID,
            "ClientMetadata": {},
            "Session": challenge.session,
            "ChallengeResponses": {
                "USERNAME": challenge.username,
                "ANSWER": answer,
            },
        },
    )

    authentication_result = data.get("AuthenticationResult")
    if not authentication_result:
        raise CognitoAuthError("Cognito rejected the verification code")
    auth = authentication_result
    id_token = auth.get("IdToken")
    refresh_token = auth.get("RefreshToken")
    if not id_token or not refresh_token:
        raise CognitoError("missing tokens in auth result")

    return CognitoTokens(id_token=id_token, refresh_token=refresh_token)


async def refresh_with_refresh_token(
    session: ClientSession, *, refresh_token: str
) -> CognitoTokens:
    # Not observed in the mitm capture (the app tends to re-auth via code), but Cognito typically
    # supports this flow. If the pool disallows it, callers should fall back to reauth.
    data = await _cognito_post(
        session,
        "AWSCognitoIdentityProviderService.InitiateAuth",
        {
            "ClientId": COGNITO_CLIENT_ID,
            "ClientMetadata": {},
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "AuthParameters": {"REFRESH_TOKEN": refresh_token},
        },
    )

    auth = data.get("AuthenticationResult") or {}
    id_token = auth.get("IdToken")
    new_refresh_token = auth.get("RefreshToken") or refresh_token
    if not id_token:
        raise CognitoError("missing id token in refresh result")

    return CognitoTokens(id_token=id_token, refresh_token=new_refresh_token)
