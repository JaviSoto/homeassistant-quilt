from __future__ import annotations

import asyncio

import pytest

from custom_components.quilt.cognito import CognitoError, initiate_custom_auth, refresh_with_refresh_token, respond_to_custom_challenge


def test_cognito_initiate_rejects_unexpected_challenge(monkeypatch) -> None:  # noqa: ANN001
    import custom_components.quilt.cognito as mod

    async def fake_post(session, target, payload):  # noqa: ANN001
        return {"ChallengeName": "NOPE"}

    monkeypatch.setattr(mod, "_cognito_post", fake_post)
    with pytest.raises(CognitoError):
        asyncio.run(initiate_custom_auth(session=None, email="x"))  # type: ignore[arg-type]


def test_cognito_missing_tokens(monkeypatch) -> None:  # noqa: ANN001
    import custom_components.quilt.cognito as mod

    async def fake_post(session, target, payload):  # noqa: ANN001
        return {"AuthenticationResult": {"IdToken": "id"}}

    monkeypatch.setattr(mod, "_cognito_post", fake_post)
    with pytest.raises(CognitoError):
        asyncio.run(
            respond_to_custom_challenge(session=None, challenge=mod.CognitoChallenge(session="s", username="u"), answer="a")  # type: ignore[arg-type]
        )


def test_cognito_refresh_keeps_refresh_token(monkeypatch) -> None:  # noqa: ANN001
    import custom_components.quilt.cognito as mod

    async def fake_post(session, target, payload):  # noqa: ANN001
        return {"AuthenticationResult": {"IdToken": "id"}}

    monkeypatch.setattr(mod, "_cognito_post", fake_post)
    tokens = asyncio.run(refresh_with_refresh_token(session=None, refresh_token="r1"))  # type: ignore[arg-type]
    assert tokens.id_token == "id"
    assert tokens.refresh_token == "r1"

