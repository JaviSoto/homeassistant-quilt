from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import grpc

try:
    from homeassistant.exceptions import ConfigEntryAuthFailed
except ModuleNotFoundError:  # pragma: no cover
    class ConfigEntryAuthFailed(RuntimeError):
        pass

from .const import DEFAULT_HOST
from .cognito import CognitoError, refresh_with_refresh_token
from .notifier_proto import encode_publish_request
from .proto_wire import encode_bytes_field, encode_string, encode_varint_field
from .quilt_parse import (
    QuiltHdsSystem,
    QuiltSystemInfo,
    QuiltSpaceEnergyMetrics,
    parse_get_home_datastore_system_response,
    parse_get_energy_metrics_response,
    parse_list_systems_response,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuiltApiConfig:
    host: str = DEFAULT_HOST
    email: str = ""
    id_token: str = ""
    refresh_token: str = ""
    debug_dir: str | None = None


class QuiltApi:
    def __init__(
        self,
        config: QuiltApiConfig,
        *,
        aiohttp_session,
        token_update_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self._config = config
        self._channel: grpc.Channel | None = None
        self._id_token = config.id_token
        self._refresh_token = config.refresh_token
        self._aiohttp_session = aiohttp_session
        self._token_update_callback = token_update_callback
        self._debug_dir = Path(config.debug_dir).expanduser() if config.debug_dir else None
        self._token_lock = asyncio.Lock()

    async def async_connect(self) -> None:
        if self._channel is not None:
            return
        self._channel = grpc.secure_channel(
            self._config.host,
            grpc.ssl_channel_credentials(),
            options=self._grpc_channel_options(),
        )

    @property
    def host(self) -> str:
        return self._config.host

    def grpc_channel_options(self) -> list[tuple[str, int | str]]:
        return self._grpc_channel_options()

    @staticmethod
    def _grpc_channel_options() -> list[tuple[str, int | str]]:
        # Match the Quilt mobile app's gRPC user-agent. Some streaming endpoints can be
        # sensitive to client identity and/or transport settings.
        return [
            ("grpc.primary_user_agent", "grpc-swift-nio/1.26.0"),
            # Keep the connection alive for long-lived notifier streams.
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
        ]

    async def async_close(self) -> None:
        if self._channel is None:
            return
        await asyncio.to_thread(self._channel.close)
        self._channel = None

    def _metadata(self) -> list[tuple[str, str]]:
        # Quilt gRPC uses the Cognito *IdToken* as the raw `authorization` header value.
        return [("authorization", self._id_token)]

    @staticmethod
    def _jwt_exp_unix(jwt: str) -> int | None:
        try:
            parts = jwt.split(".")
            if len(parts) < 2:
                return None
            payload_b64 = parts[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")))
            exp = payload.get("exp")
            return int(exp) if exp is not None else None
        except Exception:
            return None

    def token_expires_soon(self, within_seconds: int = 120) -> bool:
        exp = self._jwt_exp_unix(self._id_token)
        if exp is None:
            return True
        return exp <= int(time.time()) + within_seconds

    async def _ensure_connected(self) -> grpc.Channel:
        await self.async_connect()
        if self._channel is None:
            raise RuntimeError("gRPC channel not initialized")
        return self._channel

    async def _ensure_fresh_token(self) -> None:
        async with self._token_lock:
            if not self.token_expires_soon():
                return
            if not self._refresh_token:
                raise CognitoError("missing refresh token")

            try:
                tokens = await refresh_with_refresh_token(self._aiohttp_session, refresh_token=self._refresh_token)
            except CognitoError as e:
                raise ConfigEntryAuthFailed(str(e)) from e
            self._id_token = tokens.id_token
            if tokens.refresh_token:
                self._refresh_token = tokens.refresh_token
            if self._token_update_callback is not None:
                self._token_update_callback(self._id_token, self._refresh_token)

    async def _unary_unary(self, method: str, request: bytes) -> bytes:
        channel = await self._ensure_connected()
        await self._ensure_fresh_token()
        metadata = list(self._metadata())

        await self._debug_dump("req", method, request)
        _LOGGER.debug("Quilt gRPC request %s len=%d", method, len(request))

        def _do_call() -> bytes:
            call = channel.unary_unary(
                method,
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )
            return call(request, metadata=metadata, timeout=30)

        try:
            resp = await asyncio.to_thread(_do_call)
        except grpc.RpcError as e:
            _LOGGER.error("Quilt gRPC error %s: %s", method, e)
            raise

        _LOGGER.debug("Quilt gRPC response %s len=%d", method, len(resp))
        await self._debug_dump("resp", method, resp)
        return resp

    async def _debug_dump(self, direction: str, method: str, payload: bytes) -> None:
        if self._debug_dir is None:
            return
        safe_method = method.strip("/").replace("/", "__").replace(".", "_")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        path = self._debug_dir / f"{ts}.{safe_method}.{direction}.b64"
        data = base64.b64encode(payload).decode("ascii")
        # Write as a single-line base64 file for easy copy/paste or diffing.
        await asyncio.to_thread(path.write_text, data + "\n", encoding="utf-8")

    async def async_list_systems(self) -> list[QuiltSystemInfo]:
        raw = await self._unary_unary("/core.protos.app.SystemInformationService/ListSystems", b"")
        return parse_list_systems_response(raw)

    async def async_get_home_datastore_system(self, system_id: str) -> QuiltHdsSystem:
        req = encode_string(1, system_id)
        raw = await self._unary_unary(
            "/core.protos.home_datastore.HomeDatastoreService/GetHomeDatastoreSystem",
            req,
        )
        return parse_get_home_datastore_system_response(raw)

    async def async_get_energy_metrics(
        self,
        *,
        system_id: str,
        start_time: datetime,
        end_time: datetime,
        preferred_time_resolution: int = 1,  # HOURLY
    ) -> list[QuiltSpaceEnergyMetrics]:
        def _encode_timestamp(ts: datetime) -> bytes:
            if ts.tzinfo is None:
                raise ValueError("energy metrics timestamps must be timezone-aware")
            unix = ts.timestamp()
            seconds = int(unix)
            nanos = int((unix - seconds) * 1_000_000_000)
            return encode_varint_field(1, seconds) + encode_varint_field(2, nanos)

        req = b"".join(
            [
                encode_string(1, system_id),
                encode_bytes_field(2, _encode_timestamp(start_time)),
                encode_bytes_field(3, _encode_timestamp(end_time)),
                encode_varint_field(4, int(preferred_time_resolution)),
            ]
        )
        raw = await self._unary_unary("/core.protos.app.SystemInformationService/GetEnergyMetrics", req)
        return parse_get_energy_metrics_response(raw)

    async def async_update_space(self, *, space_message: bytes) -> bytes:
        req = encode_bytes_field(1, space_message)
        return await self._unary_unary("/core.protos.home_datastore.HomeDatastoreService/UpdateSpace", req)

    async def async_update_comfort_setting(self, *, comfort_setting_message: bytes) -> bytes:
        req = encode_bytes_field(1, comfort_setting_message)
        return await self._unary_unary(
            "/core.protos.home_datastore.HomeDatastoreService/UpdateComfortSetting",
            req,
        )

    async def async_update_indoor_unit(self, *, indoor_unit_message: bytes) -> bytes:
        req = encode_bytes_field(1, indoor_unit_message)
        return await self._unary_unary(
            "/core.protos.home_datastore.HomeDatastoreService/UpdateIndoorUnit",
            req,
        )

    async def async_get_authorization_header(self) -> str:
        await self._ensure_fresh_token()
        return self._id_token

    async def async_publish_heartbeat(self, system_id: str) -> None:
        topic = f"system/{system_id}/client_heartbeat"
        req = encode_publish_request([(topic, None)])
        await self._unary_unary("/core.protos.notifier.NotifierService/Publish", req)

    def notifier_stream_callable(self) -> grpc.StreamStreamMultiCallable:
        if self._channel is None:
            raise RuntimeError("gRPC channel not initialized")
        # Bidirectional stream used by the mobile app for near-real-time updates.
        return self._channel.stream_stream(
            "/core.protos.notifier.NotifierService/Subscribe",
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )

    def notifier_unary_stream_callable(self) -> grpc.UnaryStreamMultiCallable:
        if self._channel is None:
            raise RuntimeError("gRPC channel not initialized")
        # Some gRPC stacks treat Subscribe as a unary-stream subscription: send one
        # request with topics, then receive a long-lived stream of events.
        #
        # This calling pattern is also compatible with bidi-stream servers (we
        # simply send one message and half-close).
        return self._channel.unary_stream(
            "/core.protos.notifier.NotifierService/Subscribe",
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )
