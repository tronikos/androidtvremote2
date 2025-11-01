"""Remote protocol with an Android TV."""

# Based on:
# https://github.com/louis49/androidtv-remote/tree/main/src/remote
# https://github.com/farshid616/Android-TV-Remote-Controller-Python/blob/main/sending_keys.py

from __future__ import annotations

import asyncio
from enum import IntFlag
from typing import TYPE_CHECKING, Any

from google.protobuf import text_format
from google.protobuf.message import DecodeError

from .base import ProtobufProtocol
from .const import LOGGER
from .exceptions import ConnectionClosed
from .remotemessage_pb2 import (
    RemoteDirection,
    RemoteEditInfo,
    RemoteImeBatchEdit,
    RemoteImeObject,
    RemoteKeyCode,
    RemoteMessage,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .model import DeviceInfo, VolumeInfo

LOG_PING_REQUESTS = False
ERROR_SUGGESTION_MSG = (
    "Try clearing the storage of the Android TV Remote Service system app. "
    "On the Android TV device, go to Settings > Apps > See all apps > Show system apps. "
    "Then, select Android TV Remote Service > Storage > Clear data/storage."
)
KEYCODE_PREFIX = "KEYCODE_"
TEXT_PREFIX = "text:"

# Timeout in seconds to wait for `remote_voice_begin` after sending KEYCODE_SEARCH.
VOICE_SESSION_TIMEOUT = 2.0
# Voice data chunk size in bytes for the `remote_voice_payload` message.
VOICE_CHUNK_SIZE = 20 * 1024
# Minimum voice data chunk size in bytes. Shield TV did not accept lower chunk sizes.
VOICE_CHUNK_MIN_SIZE = 8 * 1024


class Feature(IntFlag):
    """Supported features."""

    PING = 2**0
    KEY = 2**1
    IME = 2**2
    VOICE = 2**3
    """Enables remote_voice_begin after sending KEYCODE_SEARCH"""
    UNKNOWN_1 = 2**4
    POWER = 2**5
    VOLUME = 2**6
    APP_LINK = 2**9


class RemoteProtocol(ProtobufProtocol):
    """Implement remote protocol with an Android TV.

    Messages transmitted between client and server are of type RemoteMessage, see remotemessage.proto.
    Protocol is described in
    https://github.com/Aymkdn/assistant-freebox-cloud/wiki/Google-TV-(aka-Android-TV)-Remote-Control-(v2)
    """

    def __init__(
        self,
        on_con_lost: asyncio.Future[Exception | None],
        on_remote_started: asyncio.Future[bool],
        on_is_on_updated: Callable[[bool], None],
        on_current_app_updated: Callable[[str], None],
        on_volume_info_updated: Callable[[VolumeInfo], None],
        loop: asyncio.AbstractEventLoop,
        enable_ime: bool,
        enable_voice: bool,
    ) -> None:
        """Initialize.

        :param on_con_lost: callback for when the connection is lost or closed.
        :param on_remote_started: callback for when the Android TV is ready to receive commands.
        :param on_is_on_updated: callback for when is_on is updated.
        :param on_current_app_updated: callback for when current_app is updated.
        :param on_volume_info_updated: callback for when volume_info is updated.
        :param loop: event loop.
        :param enable_ime: Needed for getting current_app.
               Disable for devices that show 'Use keyboard on mobile device screen'.
        :param enable_voice: Enable sending voice commands to the device.
        """
        super().__init__(on_con_lost)
        self._on_remote_started = on_remote_started
        self._on_is_on_updated = on_is_on_updated
        self._on_current_app_updated = on_current_app_updated
        self._on_volume_info_updated = on_volume_info_updated
        self._active_features = (
            Feature.PING
            | Feature.KEY
            | Feature.POWER
            | Feature.VOLUME
            | Feature.APP_LINK
            | (Feature.IME if enable_ime else 0)
            | (Feature.VOICE if enable_voice else 0)
        )
        self.is_on = False
        self.current_app = ""
        self.device_info: DeviceInfo | None = None
        self.volume_info: VolumeInfo | None = None
        self.ime_counter: int = 0
        self.ime_field_counter: int = 0
        self._loop = loop
        self._idle_disconnect_task: asyncio.Task[None] | None = None
        self._reset_idle_disconnect_task()
        self._voice_lock = asyncio.Lock()
        self._on_voice_begin: asyncio.Future[int] | None = None

    @property
    def is_voice_enabled(self) -> bool:
        """Voice commands enabled.

        Determined from requested features at initialization and the supported features on the
        device.
        """
        return self._active_features & Feature.VOICE == Feature.VOICE

    def send_key_command(self, key_code: int | str, direction: int | str = RemoteDirection.SHORT) -> None:
        """Send a key press to Android TV.

        This does not block; it buffers the data and arranges for it to be sent out asynchronously.

        :param key_code: int (e.g. 26) or str (e.g. "KEYCODE_POWER" or just "POWER") from the enum
                         RemoteKeyCode in remotemessage.proto or str prefixed with "text:" to pass
                         to send_text.
        :param direction: "SHORT" (default) or "START_LONG" or "END_LONG".
        :raises ValueError: if key_code in str or direction isn't known.
        """
        self._reset_idle_disconnect_task()
        msg = RemoteMessage()
        if isinstance(key_code, str):
            if key_code.lower().startswith(TEXT_PREFIX):
                return self.send_text(key_code[len(TEXT_PREFIX) :])
            if not key_code.startswith(KEYCODE_PREFIX):
                key_code = KEYCODE_PREFIX + key_code
            key_code = RemoteKeyCode.Value(key_code)
        if isinstance(direction, str):
            direction = RemoteDirection.Value(direction)
        msg.remote_key_inject.key_code = key_code  # type: ignore[assignment]
        msg.remote_key_inject.direction = direction  # type: ignore[assignment]
        self._send_message(msg)
        return None

    def send_text(self, text: str) -> None:
        """Send a text string to Android TV via the input method.

        The text length is used for both `start` and `end` in the RemoteImeObject.
        The `ime_counter` and `ime_field_counter` values are taken from self (batch_edit_info response),
        which is populated when a message with a remote_ime_batch_edit field is received.

        :param text: The text string to be sent.
        """
        if not text:
            raise ValueError("Text cannot be empty")

        self._reset_idle_disconnect_task()
        msg = RemoteMessage()
        param_value = len(text) - 1
        ime_object = RemoteImeObject(start=param_value, end=param_value, value=text)
        edit_info = RemoteEditInfo(insert=1, text_field_status=ime_object)
        batch_edit = RemoteImeBatchEdit(
            ime_counter=self.ime_counter,
            field_counter=self.ime_field_counter,
            edit_info=[edit_info],
        )
        msg.remote_ime_batch_edit.CopyFrom(batch_edit)
        self._send_message(msg)

    def send_launch_app_command(self, app_link: str) -> None:
        """Launch an app on Android TV.

        This does not block; it buffers the data and arranges for it to be sent out asynchronously.
        """
        self._reset_idle_disconnect_task()
        msg = RemoteMessage()
        msg.remote_app_link_launch_request.app_link = app_link
        self._send_message(msg)

    async def start_voice(self, timeout: float = VOICE_SESSION_TIMEOUT) -> int:
        """Initiate a voice session and return the session id when ready.

        Sends ``KEYCODE_SEARCH`` and waits for ``remote_voice_begin``. Also sends the
        initial ``remote_voice_begin`` message back to the device, as required by the
        protocol, so the caller can start streaming audio chunks.

        :param timeout: Optional timeout in seconds for session readiness. Defaults to 2 seconds.
        :raises ConnectionClosed: If the connection is lost.
        :raises asyncio.TimeoutError: If the operation times out or a voice session is already in
                                      progress.
        :return: The voice session id, which must be used in later calls to ``send_voice_chunk``.
        """
        if self.transport is None or self.transport.is_closing():
            raise ConnectionClosed("Connection has been lost")

        if self._voice_lock.locked():
            raise asyncio.TimeoutError("Voice session already in progress")

        await self._voice_lock.acquire()

        self._on_voice_begin = self._loop.create_future()
        try:
            self.send_key_command(RemoteKeyCode.KEYCODE_SEARCH)
            session_id = await self._async_wait_for_future_or_con_lost(self._on_voice_begin, timeout)
            if session_id is None:
                raise ConnectionClosed("No voice session available")

            begin = RemoteMessage()
            begin.remote_voice_begin.session_id = session_id
            self._send_message(begin)
            return int(session_id)
        except:
            self._on_voice_begin = None
            raise
        finally:
            self._voice_lock.release()

    def send_voice_chunk(self, chunk: bytes, session_id: int) -> None:
        """Send a chunk of PCM audio for the active voice session.

        :param chunk: The PCM audio data chunk to be sent.
        :param session_id: The voice session id.
        :raises ConnectionClosed: If the connection is lost.
        """
        if self.transport is None or self.transport.is_closing():
            raise ConnectionClosed("Connection has been lost")

        # Pad chunk to minimum chunk size
        if len(chunk) < VOICE_CHUNK_MIN_SIZE:
            chunk = chunk + b"\x00" * (VOICE_CHUNK_MIN_SIZE - len(chunk))

        # Limit chunk size, otherwise Android TV will close the connection
        for i in range(0, len(chunk), VOICE_CHUNK_SIZE):
            msg = RemoteMessage()
            msg.remote_voice_payload.session_id = session_id
            msg.remote_voice_payload.samples = chunk[i : i + VOICE_CHUNK_SIZE]
            self._send_message(msg, False)  # disable logging of voice data

    def end_voice(self, session_id: int) -> None:
        """End the specified voice session.

        :param session_id: The voice session id.
        """
        end = RemoteMessage()
        end.remote_voice_end.session_id = session_id
        self._send_message(end)

    def _handle_message(self, raw_msg: bytes) -> None:  # noqa: PLR0912,PLR0915
        """Handle a message from the server."""
        self._reset_idle_disconnect_task()
        msg = RemoteMessage()
        try:
            msg.ParseFromString(raw_msg)
        except DecodeError as exc:
            LOGGER.debug("Couldn't parse as RemoteMessage. %s", exc)
            return
        if LOG_PING_REQUESTS or not msg.HasField("remote_ping_request"):
            LOGGER.debug("Received: %s", text_format.MessageToString(msg, as_one_line=True))

        new_msg = RemoteMessage()
        log_send = True

        if msg.HasField("remote_configure"):
            cfg = msg.remote_configure
            self.device_info = {
                "manufacturer": cfg.device_info.vendor,
                "model": cfg.device_info.model,
                "sw_version": cfg.device_info.app_version,
            }
            supported_features = Feature(cfg.code1)
            LOGGER.debug("Device supports: %s", [supported_features])
            if Feature.KEY not in supported_features:
                LOGGER.error("Device doesn't support sending keys. %s", ERROR_SUGGESTION_MSG)
            if Feature.APP_LINK not in supported_features:
                LOGGER.error("Device doesn't support sending app links. %s", ERROR_SUGGESTION_MSG)
            self._active_features &= supported_features
            new_msg.remote_configure.code1 = self._active_features.value
            new_msg.remote_configure.device_info.unknown1 = 1
            new_msg.remote_configure.device_info.unknown2 = "1"
            new_msg.remote_configure.device_info.package_name = "atvremote"
            new_msg.remote_configure.device_info.app_version = "1.0.0"
        elif msg.HasField("remote_set_active"):
            new_msg.remote_set_active.active = self._active_features
        elif msg.HasField("remote_ime_key_inject"):
            self.current_app = msg.remote_ime_key_inject.app_info.app_package
            self._on_current_app_updated(self.current_app)
        elif msg.HasField("remote_ime_batch_edit"):
            self.ime_counter = msg.remote_ime_batch_edit.ime_counter
            self.ime_field_counter = msg.remote_ime_batch_edit.field_counter
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
        elif msg.HasField("remote_voice_begin"):
            if self._on_voice_begin and not self._on_voice_begin.done():
                self._on_voice_begin.set_result(msg.remote_voice_begin.session_id)
            else:
                LOGGER.debug("Ignoring remote_voice_begin: no client request available")
        else:
            LOGGER.debug("Unhandled: %s", text_format.MessageToString(msg, as_one_line=True))

        if new_msg != RemoteMessage():
            self._send_message(new_msg, log_send)

    def _reset_idle_disconnect_task(self) -> None:
        if self._idle_disconnect_task is not None:
            self._idle_disconnect_task.cancel()
        self._idle_disconnect_task = self._loop.create_task(self._async_idle_disconnect())

    async def _async_idle_disconnect(self) -> None:
        # Disconnect if there is no message from the server or client within
        # 16 seconds. Server pings every 5 seconds if there is no command sent.
        # This is similar to the server behavior that closes connections after 3
        # unanswered pings.
        await asyncio.sleep(16)
        LOGGER.debug("Closing idle connection")
        if self.transport and not self.transport.is_closing():
            self.transport.close()
        if not self.on_con_lost.done():
            self.on_con_lost.set_result(Exception("Closed idle connection"))

    async def _async_wait_for_future_or_con_lost(self, future: asyncio.Future[Any], timeout: float) -> Any:
        """Wait for the future to finish, connection to be lost, or timeout occurs.

        :param future: The future to wait for.
        :param timeout: Timeout in seconds.

        :raises ConnectionClosed: If the connection is lost or the future has an exception.
        :raises asyncio.TimeoutError: If timeout is reached before completion.
        """
        tasks = {self.on_con_lost, future}

        done, _pending = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)

        # Check if timeout occurred (no tasks completed)
        if not done:
            if not future.done():
                future.cancel()
            LOGGER.debug("Timeout reached after %s seconds", timeout)
            raise asyncio.TimeoutError(f"Operation timed out after {timeout} seconds")

        # Check if future completed successfully
        if future.done():
            if future.exception():
                raise ConnectionClosed("Waiting for future failed") from future.exception()
            return future.result()

        raise ConnectionClosed("Connection has been lost")
