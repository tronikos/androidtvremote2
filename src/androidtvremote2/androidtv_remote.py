"""Pairing and connecting to an Android TV for remotely sending commands to it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
import ssl

import aiofiles
from cryptography import x509

from .certificate_generator import generate_selfsigned_cert
from .const import LOGGER
from .exceptions import CannotConnect, ConnectionClosed, InvalidAuth
from .pairing import PairingProtocol
from .remote import RemoteProtocol
from .remotemessage_pb2 import RemoteDirection


class AndroidTVRemote:
    """Pairing and connecting to an Android TV for remotely sending commands to it."""

    def __init__(
        self,
        client_name: str,
        certfile: str,
        keyfile: str,
        host: str,
        api_port: int = 6466,
        pair_port: int = 6467,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Initialize.

        :param client_name: client name. Will be shown on the Android TV during pairing.
        :param certfile: filename that contains the client certificate in PEM format.
        :param keyfile: filename that contains the public key in PEM format.
        :param host: IP address of the Android TV.
        :param api_port: port for connecting and sending commands.
        :param pair_port: port for pairing.
        :param loop: event loop. Used for connections and futures.
        """
        self._client_name = client_name
        self._certfile = certfile
        self._keyfile = keyfile
        self.host = host
        self._api_port = api_port
        self._pair_port = pair_port
        self._loop = loop or asyncio.get_running_loop()
        self._transport = None
        self._remote_message_protocol: RemoteProtocol | None = None
        self._pairing_message_protocol: PairingProtocol | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._is_on_updated_callbacks: list[Callable] = []
        self._current_app_updated_callbacks: list[Callable] = []
        self._volume_info_updated_callbacks: list[Callable] = []
        self._is_available_updated_callbacks: list[Callable] = []

        def is_on_updated(is_on: bool):
            for callback in self._is_on_updated_callbacks:
                callback(is_on)

        def current_app_updated(current_app: str):
            for callback in self._current_app_updated_callbacks:
                callback(current_app)

        def volume_info_updated(volume_info: dict[str, str | bool]):
            for callback in self._volume_info_updated_callbacks:
                callback(volume_info)

        def is_available_updated(is_available: bool):
            for callback in self._is_available_updated_callbacks:
                callback(is_available)

        self._on_is_on_updated = is_on_updated
        self._on_current_app_updated = current_app_updated
        self._on_volume_info_updated = volume_info_updated
        self._on_is_available_updated = is_available_updated

    @property
    def is_on(self) -> bool | None:
        """Whether the Android TV is on or off."""
        if not self._remote_message_protocol:
            return None
        return self._remote_message_protocol.is_on

    @property
    def current_app(self) -> str | None:
        """Current app in the foreground on the Android TV. E.g. 'com.google.android.youtube.tv'."""
        if not self._remote_message_protocol:
            return None
        return self._remote_message_protocol.current_app

    @property
    def device_info(self) -> dict[str, str] | None:
        """Device info (manufacturer, model, sw_version)."""
        if not self._remote_message_protocol:
            return None
        return self._remote_message_protocol.device_info

    @property
    def volume_info(self) -> dict[str, str | bool] | None:
        """Volume info (level, max, muted)."""
        if not self._remote_message_protocol:
            return None
        return self._remote_message_protocol.volume_info

    def add_is_on_updated_callback(self, callback: Callable) -> None:
        """Add a callback for when is_on is updated."""
        self._is_on_updated_callbacks.append(callback)

    def add_current_app_updated_callback(self, callback: Callable) -> None:
        """Add a callback for when current_app is updated."""
        self._current_app_updated_callbacks.append(callback)

    def add_volume_info_updated_callback(self, callback: Callable) -> None:
        """Add a callback for when volume_info is updated."""
        self._volume_info_updated_callbacks.append(callback)

    def add_is_available_updated_callback(self, callback: Callable) -> None:
        """Add a callback for when the Android TV is ready to receive commands or is unavailable."""
        self._is_available_updated_callbacks.append(callback)

    async def async_generate_cert_if_missing(self) -> bool:
        """Generate client certificate and public key if missing.

        :returns: True if a new certificate was generated.
        """
        if os.path.isfile(self._certfile) and os.path.isfile(self._keyfile):
            return False
        cert_pem, key_pem = generate_selfsigned_cert(self._client_name)
        async with aiofiles.open(self._certfile, "w", encoding="utf-8") as out:
            await out.write(cert_pem.decode("utf-8"))
        async with aiofiles.open(self._keyfile, "w", encoding="utf-8") as out:
            await out.write(key_pem.decode("utf-8"))
        return True

    async def async_connect(self):
        """Connect to an Android TV.

        :raises CannotConnect: if couldn't connect, e.g. invalid IP address.
        :raises ConnectionClosed: if connection was lost while waiting for the remote to start.
        :raises InvalidAuth: if pairing is needed first.
        """
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        try:
            ssl_context.load_cert_chain(self._certfile, self._keyfile)
        except FileNotFoundError as exc:
            LOGGER.debug("Missing certificate. Error: %s", exc)
            raise InvalidAuth from exc
        on_con_lost = self._loop.create_future()
        on_remote_started = self._loop.create_future()
        try:
            (
                self._transport,
                self._remote_message_protocol,
            ) = await self._loop.create_connection(
                lambda: RemoteProtocol(
                    on_con_lost,
                    on_remote_started,
                    self._on_is_on_updated,
                    self._on_current_app_updated,
                    self._on_volume_info_updated,
                    self._loop,
                ),
                self.host,
                self._api_port,
                ssl=ssl_context,
            )
        except OSError as exc:
            LOGGER.debug(
                "Couldn't connect to %s:%s. Error: %s", self.host, self._api_port, exc
            )
            raise CannotConnect(
                f"Couldn't connect to {self.host}:{self._api_port}"
            ) from exc

        await asyncio.wait(
            (on_con_lost, on_remote_started), return_when=asyncio.FIRST_COMPLETED
        )
        if on_con_lost.done():
            con_lost_exc = on_con_lost.result()
            LOGGER.debug(
                "Couldn't connect to %s:%s. Error: %s",
                self.host,
                self._api_port,
                con_lost_exc,
            )
            if isinstance(con_lost_exc, ssl.SSLError):
                raise InvalidAuth("Need to pair again") from con_lost_exc
            raise ConnectionClosed("Connection closed") from con_lost_exc

    async def _async_reconnect(
        self, invalid_auth_callback: Callable | None = None
    ) -> None:
        while self._remote_message_protocol:
            exc = await self._remote_message_protocol.on_con_lost
            self._on_is_available_updated(False)
            LOGGER.debug("Disconnected from %s. Error: %s", self.host, exc)
            delay_seconds = 10
            LOGGER.debug(
                "Trying to reconnect to %s in %s seconds", self.host, delay_seconds
            )
            while self._remote_message_protocol:
                await asyncio.sleep(delay_seconds)
                try:
                    await self.async_connect()
                    self._on_is_available_updated(True)
                    break
                except CannotConnect as exc:
                    delay_seconds = min(2 * delay_seconds, 300)
                    LOGGER.debug(
                        "Couldn't reconnect to %s. Will retry in %s seconds. Error: %s",
                        self.host,
                        delay_seconds,
                        exc,
                    )
                except InvalidAuth as exc:
                    LOGGER.debug(
                        "Couldn't reconnect to %s. Won't retry. Error: %s",
                        self.host,
                        exc,
                    )
                    if invalid_auth_callback:
                        invalid_auth_callback()
                    return

    def keep_reconnecting(self, invalid_auth_callback: Callable | None = None) -> None:
        """Create a task to keep reconnecting whenever connection is lost."""
        self._reconnect_task = self._loop.create_task(
            self._async_reconnect(invalid_auth_callback)
        )

    def disconnect(self) -> None:
        """Disconnect any open connections."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._remote_message_protocol:
            if self._remote_message_protocol.transport:
                self._remote_message_protocol.transport.close()
            self._remote_message_protocol = None
        if self._pairing_message_protocol:
            if self._pairing_message_protocol.transport:
                self._pairing_message_protocol.transport.close()
            self._pairing_message_protocol = None

    async def async_get_name_and_mac(self) -> tuple[str, str]:
        """Connect to the Android TV and get its name and MAC address from its certificate.

        :raises CannotConnect: if couldn't connect, e.g. invalid IP address.
        """
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        try:
            _, writer = await asyncio.open_connection(
                self.host, self._api_port, ssl=ssl_context
            )
        except OSError as exc:
            LOGGER.debug(
                "Couldn't connect to %s:%s. %s", self.host, self._api_port, exc
            )
            raise CannotConnect from exc
        server_cert_bytes = writer.transport.get_extra_info("ssl_object").getpeercert(
            True
        )
        writer.close()
        server_cert = x509.load_der_x509_certificate(server_cert_bytes)
        server_cert_common_name = str(
            server_cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[
                0
            ].value
        )
        # Example: atvremote/darcy/darcy/SHIELD Android TV/XX:XX:XX:XX:XX:XX
        parts = server_cert_common_name.split("/")
        return parts[-2], parts[-1]

    async def async_start_pairing(self):
        """Start the pairing process.

        :raises CannotConnect: if couldn't connect, e.g. invalid IP address.
        :raises ConnectionClosed: if connection was lost.
        """
        self.disconnect()
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        ssl_context.load_cert_chain(self._certfile, self._keyfile)
        on_con_lost = self._loop.create_future()
        try:
            (
                _,
                self._pairing_message_protocol,
            ) = await self._loop.create_connection(
                lambda: PairingProtocol(
                    on_con_lost,
                    self._client_name,
                    self._certfile,
                    self._loop,
                ),
                self.host,
                self._pair_port,
                ssl=ssl_context,
            )
        except OSError as exc:
            LOGGER.debug(
                "Couldn't connect to %s:%s. %s", self.host, self._pair_port, exc
            )
            raise CannotConnect from exc
        await self._pairing_message_protocol.async_start_pairing()

    async def async_finish_pairing(self, pairing_code: str):
        """Finish the pairing process.

        :param pairing_code: pairing code shown on the Android TV.
        :raises ConnectionClosed: if connection was lost, e.g. user pressed cancel on the Android TV.
        :raises InvalidAuth: if pairing was unsuccessful.
        """
        if not self._pairing_message_protocol:
            LOGGER.debug("Called async_finish_pairing after disconnect")
            raise ConnectionClosed("Called async_finish_pairing after disconnect")
        await self._pairing_message_protocol.async_finish_pairing(pairing_code)
        self.disconnect()

    def send_key_command(
        self, key_code: int | str, direction: int | str = RemoteDirection.SHORT
    ):
        """Send a key press to Android TV.

        This does not block; it buffers the data and arranges for it to be sent out asynchronously.

        :param key_code: int (e.g. 26) or str (e.g. "KEYCODE_POWER" or just "POWER") from the enum
                         RemoteKeyCode in remotemessage.proto.
        :param direction: "SHORT" (default) or "START_LONG" or "END_LONG".
        :raises ValueError: if key_code in str or direction isn't known.
        :raises ConnectionClosed: if client is disconnected.
        """
        if not self._remote_message_protocol:
            LOGGER.debug("Called send_key_command after disconnect")
            raise ConnectionClosed("Called send_key_command after disconnect")
        self._remote_message_protocol.send_key_command(key_code, direction)

    def send_launch_app_command(self, app_link: str):
        """Launch an app on Android TV.

        This does not block; it buffers the data and arranges for it to be sent out asynchronously.

        :raises ConnectionClosed: if client is disconnected.
        """
        if not self._remote_message_protocol:
            LOGGER.debug("Called send_launch_app_command after disconnect")
            raise ConnectionClosed("Called send_launch_app_command after disconnect")
        self._remote_message_protocol.send_launch_app_command(app_link)
