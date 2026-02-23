from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientSession

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


async def _cognito_post(session: ClientSession, target: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://{COGNITO_HOST}/"
    headers = {
        "content-type": "application/x-amz-json-1.1",
        "x-amz-target": target,
    }

    async with session.post(url, headers=headers, data=json.dumps(payload), timeout=30) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise CognitoError(f"cognito error {resp.status}: {text[:200]}")
        if not text:
            return {}
        return json.loads(text)


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

    auth = data.get("AuthenticationResult") or {}
    id_token = auth.get("IdToken")
    refresh_token = auth.get("RefreshToken")
    if not id_token or not refresh_token:
        raise CognitoError("missing tokens in auth result")

    return CognitoTokens(id_token=id_token, refresh_token=refresh_token)


async def refresh_with_refresh_token(session: ClientSession, *, refresh_token: str) -> CognitoTokens:
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
