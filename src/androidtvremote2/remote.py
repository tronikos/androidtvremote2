"""Remote protocol with an Android TV."""

# Based on:
# https://github.com/louis49/androidtv-remote/tree/main/src/remote
# https://github.com/farshid616/Android-TV-Remote-Controller-Python/blob/main/sending_keys.py

from __future__ import annotations

import asyncio
from collections.abc import Callable

from google.protobuf import text_format
from google.protobuf.message import DecodeError

from .base import ProtobufProtocol
from .const import LOGGER
from .remotemessage_pb2 import RemoteDirection, RemoteKeyCode, RemoteMessage

LOG_PING_REQUESTS = False


class RemoteProtocol(ProtobufProtocol):
    """Implement remote protocol with an Android TV.

    Messages transmitted between client and server are of type RemoteMessage, see remotegmessage.proto.
    Protocol is described in
    https://github.com/Aymkdn/assistant-freebox-cloud/wiki/Google-TV-(aka-Android-TV)-Remote-Control-(v2)
    """

    def __init__(
        self,
        on_con_lost: asyncio.Future,
        on_remote_started: asyncio.Future,
        on_is_on_updated: Callable,
        on_current_app_updated: Callable,
        on_volume_info_updated: Callable,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Initialize.

        :param on_con_lost: callback for when the connection is lost or closed.
        :param on_remote_started: callback for when the Android TV is ready to receive commands.
        :param on_is_on_updated: callback for when is_on is updated.
        :param on_current_app_updated: callback for when current_app is updated.
        :param on_volume_info_updated: callback for when volume_info is updated.
        :param loop: event loop.
        """
        super().__init__(on_con_lost)
        self._on_remote_started = on_remote_started
        self._on_is_on_updated = on_is_on_updated
        self._on_current_app_updated = on_current_app_updated
        self._on_volume_info_updated = on_volume_info_updated
        self._active_mask = 622
        self.is_on = False
        self.current_app = ""
        self.device_info: dict[str, str] = {}
        self.volume_info: dict[str, str | bool] = {}
        self._loop = loop
        self._idle_disconnect_task: asyncio.Task | None = None
        self._reset_idle_disconnect_task()

    def send_key_command(
        self, key_code: int | str, direction: int | str = RemoteDirection.SHORT
    ):
        """Send a key press to Android TV.

        This does not block; it buffers the data and arranges for it to be sent out asynchronously.

        :param key_code: int (e.g. 26) or str (e.g. KEYCODE_POWER or just "POWER") from the enum
                         RemoteKeyCode in remotemessage.proto.
        :param direction: "SHORT" (default) or "START_LONG" or "END_LONG".
        :raises ValueError: if key_code in str or direction isn't known.
        """
        msg = RemoteMessage()
        if isinstance(key_code, str):
            if not key_code.startswith("KEYCODE_"):
                key_code = "KEYCODE_" + key_code
            key_code = RemoteKeyCode.Value(key_code)
        if isinstance(direction, str):
            direction = RemoteDirection.Value(direction)
        msg.remote_key_inject.key_code = key_code
        msg.remote_key_inject.direction = direction
        self._send_message(msg)

    def send_launch_app_command(self, app_link: str):
        """Launch an app on Android TV.

        This does not block; it buffers the data and arranges for it to be sent out asynchronously.
        """
        msg = RemoteMessage()
        msg.remote_app_link_launch_request.app_link = app_link
        self._send_message(msg)

    def _handle_message(self, raw_msg):
        """Handle a message from the server."""
        self._reset_idle_disconnect_task()
        msg = RemoteMessage()
        try:
            msg.ParseFromString(raw_msg)
        except DecodeError as exc:
            LOGGER.debug("Couldn't parse as RemoteMessage. %s", exc)
            return
        if LOG_PING_REQUESTS or not msg.HasField("remote_ping_request"):
            LOGGER.debug(
                "Received: %s", text_format.MessageToString(msg, as_one_line=True)
            )

        new_msg = RemoteMessage()
        log_send = True

        if msg.HasField("remote_configure"):
            cfg = msg.remote_configure
            self.device_info = {
                "manufacturer": cfg.device_info.vendor,
                "model": cfg.device_info.model,
                "sw_version": cfg.device_info.app_version,
            }
            if cfg.code1:
                self._active_mask = cfg.code1
            new_msg.remote_configure.code1 = self._active_mask
            new_msg.remote_configure.device_info.unknown1 = 1
            new_msg.remote_configure.device_info.unknown2 = "1"
            new_msg.remote_configure.device_info.package_name = "atvremote"
            new_msg.remote_configure.device_info.app_version = "1.0.0"
        elif msg.HasField("remote_set_active"):
            new_msg.remote_set_active.active = self._active_mask
        elif msg.HasField("remote_ime_key_inject"):
            self.current_app = msg.remote_ime_key_inject.app_info.app_package
            self._on_current_app_updated(self.current_app)
        elif msg.HasField("remote_set_volume_level"):
            self.volume_info = {
                "level": msg.remote_set_volume_level.volume_level,
                "max": msg.remote_set_volume_level.volume_max,
                "muted": msg.remote_set_volume_level.volume_muted,
            }
            self._on_volume_info_updated(self.volume_info)
        elif msg.HasField("remote_start"):
            if not self._on_remote_started.done():
                self._on_remote_started.set_result(True)
            self.is_on = msg.remote_start.started
            self._on_is_on_updated(self.is_on)
        elif msg.HasField("remote_ping_request"):
            new_msg.remote_ping_response.val1 = msg.remote_ping_request.val1
            log_send = LOG_PING_REQUESTS
        else:
            LOGGER.debug(
                "Unhandled: %s", text_format.MessageToString(msg, as_one_line=True)
            )

        if new_msg != RemoteMessage():
            self._send_message(new_msg, log_send)

    def _reset_idle_disconnect_task(self):
        if self._idle_disconnect_task is not None:
            self._idle_disconnect_task.cancel()
        self._idle_disconnect_task = self._loop.create_task(
            self._async_idle_disconnect()
        )

    async def _async_idle_disconnect(self):
        # Disconnect if there is no message from the server within
        # 16 seconds. Pings are every 5 seconds. This is similar to
        # the server behavior that closes connections after 3
        # unanswered pings.
        await asyncio.sleep(16)
        self.transport.close()
