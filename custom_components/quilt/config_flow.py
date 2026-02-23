from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.data_entry_flow import FlowResult

from .cognito import CognitoChallenge, CognitoError, initiate_custom_auth, respond_to_custom_challenge
from .const import (
    CONF_ACCEPT_TERMS,
    CONF_EMAIL,
    CONF_ENABLE_NOTIFIER,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    DEFAULT_ENABLE_NOTIFIER,
    DEFAULT_HOST,
    DOMAIN,
)


class QuiltConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._challenge_session: str | None = None
        self._challenge_username: str | None = None
        self._email: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            if not user_input.get(CONF_ACCEPT_TERMS, False):
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._user_schema(),
                    errors={"base": "accept_terms"},
                )
            self._email = user_input[CONF_EMAIL]

            errors: dict[str, str] = {}
            try:
                session = async_get_clientsession(self.hass)
                challenge = await initiate_custom_auth(session, self._email)
                self._challenge_session = challenge.session
                self._challenge_username = challenge.username
            except CognitoError:
                errors["base"] = "auth_failed"
            except Exception:
                errors["base"] = "unknown"

            if errors:
                return self.async_show_form(step_id="user", data_schema=self._user_schema(), errors=errors)

            # Proceed to code entry step.
            self.context["title_placeholders"] = {"email": self._email}
            return await self.async_step_code()

        return self.async_show_form(step_id="user", data_schema=self._user_schema())

    def _user_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_ACCEPT_TERMS, default=False): bool,
            }
        )

    async def async_step_code(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["code"]
            try:
                if not self._challenge_session or not self._challenge_username or not self._email:
                    return self.async_abort(reason="unknown")

                session = async_get_clientsession(self.hass)
                challenge = CognitoChallenge(session=self._challenge_session, username=self._challenge_username)
                tokens = await respond_to_custom_challenge(session, challenge=challenge, answer=code)

                await self.async_set_unique_id(f"quilt:{self._email}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Quilt ({self._email})",
                    data={
                        CONF_EMAIL: self._email,
                        "host": DEFAULT_HOST,
                        CONF_ID_TOKEN: tokens.id_token,
                        CONF_REFRESH_TOKEN: tokens.refresh_token,
                    },
                )
            except CognitoError:
                errors["base"] = "invalid_code"
            except Exception:
                errors["base"] = "unknown"

        schema = vol.Schema({vol.Required("code"): str})
        return self.async_show_form(step_id="code", data_schema=schema, errors=errors)

    async def async_step_reauth(self, user_input: dict | None = None) -> FlowResult:
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return self.async_abort(reason="unknown")

        self._reauth_entry = entry
        self._email = entry.data.get(CONF_EMAIL)

        if not self._email:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        try:
            session = async_get_clientsession(self.hass)
            challenge = await initiate_custom_auth(session, self._email)
            self._challenge_session = challenge.session
            self._challenge_username = challenge.username
        except CognitoError:
            errors["base"] = "auth_failed"
        except Exception:
            errors["base"] = "unknown"

        if errors:
            return self.async_show_form(step_id="reauth", data_schema=vol.Schema({}), errors=errors)

        return await self.async_step_reauth_code()

    async def async_step_reauth_code(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                if not self._challenge_session or not self._challenge_username or not self._email or not self._reauth_entry:
                    return self.async_abort(reason="unknown")

                session = async_get_clientsession(self.hass)
                challenge = CognitoChallenge(session=self._challenge_session, username=self._challenge_username)
                tokens = await respond_to_custom_challenge(session, challenge=challenge, answer=user_input["code"])

                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_ID_TOKEN: tokens.id_token,
                        CONF_REFRESH_TOKEN: tokens.refresh_token,
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            except CognitoError:
                errors["base"] = "invalid_code"
            except Exception:
                errors["base"] = "unknown"

        schema = vol.Schema({vol.Required("code"): str})
        return self.async_show_form(step_id="reauth_code", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        return QuiltOptionsFlowHandler(config_entry)


class QuiltOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENABLE_NOTIFIER,
                    default=self._config_entry.options.get(CONF_ENABLE_NOTIFIER, DEFAULT_ENABLE_NOTIFIER),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
