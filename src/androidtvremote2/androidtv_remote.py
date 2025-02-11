"""Pairing and connecting to an Android TV for remotely sending commands to it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
import ssl
from urllib.parse import urlparse

import aiofiles
from cryptography import x509

from .certificate_generator import generate_selfsigned_cert
from .const import LOGGER
from .exceptions import CannotConnect, ConnectionClosed, InvalidAuth
from .pairing import PairingProtocol
from .remote import RemoteProtocol
from .remotemessage_pb2 import RemoteDirection


def _load_cert_chain(certfile: str, keyfile: str) -> ssl.SSLContext:
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.VerifyMode.CERT_NONE
    ssl_context.load_cert_chain(certfile, keyfile)
    return ssl_context


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
        enable_ime: bool = True,
    ) -> None:
        """Initialize.

        :param client_name: client name. Will be shown on the Android TV during pairing.
        :param certfile: filename that contains the client certificate in PEM format.
        :param keyfile: filename that contains the public key in PEM format.
        :param host: IP address of the Android TV.
        :param api_port: port for connecting and sending commands.
        :param pair_port: port for pairing.
        :param loop: event loop. Used for connections and futures.
        :param enable_ime: Needed for getting current_app.
               Disable for devices that show 'Use keyboard on mobile device screen'.
        """
        self._client_name = client_name
        self._certfile = certfile
        self._keyfile = keyfile
        self.host = host
        self._api_port = api_port
        self._pair_port = pair_port
        self._loop = loop or asyncio.get_running_loop()
        self._enable_ime = enable_ime
        self._transport: asyncio.Transport | None = None
        self._remote_message_protocol: RemoteProtocol | None = None
        self._pairing_message_protocol: PairingProtocol | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._ssl_context: ssl.SSLContext | None = None
        self._is_on_updated_callbacks: list[Callable] = []
        self._current_app_updated_callbacks: list[Callable] = []
        self._volume_info_updated_callbacks: list[Callable] = []
        self._is_available_updated_callbacks: list[Callable] = []

        def is_on_updated(is_on: bool) -> None:
            for callback in self._is_on_updated_callbacks:
                callback(is_on)

        def current_app_updated(current_app: str) -> None:
            for callback in self._current_app_updated_callbacks:
                callback(current_app)

        def volume_info_updated(volume_info: dict[str, str | bool]) -> None:
            for callback in self._volume_info_updated_callbacks:
                callback(volume_info)

        def is_available_updated(is_available: bool) -> None:
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
    def volume_info(self) -> dict[str, str | bool | int] | None:
        """Volume info (level, max, muted)."""
        if not self._remote_message_protocol:
            return None
        return self._remote_message_protocol.volume_info

    def add_is_on_updated_callback(self, callback: Callable) -> None:
        """Add a callback for when is_on is updated."""
        self._is_on_updated_callbacks.append(callback)

    def remove_is_on_updated_callback(self, callback: Callable) -> None:
        """Remove a callback previously added via add_is_on_updated_callback.

        :raises ValueError: if callback not previously added.
        """
        self._is_on_updated_callbacks.remove(callback)

    def add_current_app_updated_callback(self, callback: Callable) -> None:
        """Add a callback for when current_app is updated."""
        self._current_app_updated_callbacks.append(callback)

    def remove_current_app_updated_callback(self, callback: Callable) -> None:
        """Remove a callback previously added via add_current_app_updated_callback.

        :raises ValueError: if callback not previously added.
        """
        self._current_app_updated_callbacks.remove(callback)

    def add_volume_info_updated_callback(self, callback: Callable) -> None:
        """Add a callback for when volume_info is updated."""
        self._volume_info_updated_callbacks.append(callback)

    def remove_volume_info_updated_callback(self, callback: Callable) -> None:
        """Remove a callback previously added via add_volume_info_updated_callback.

        :raises ValueError: if callback not previously added.
        """
        self._volume_info_updated_callbacks.remove(callback)

    def add_is_available_updated_callback(self, callback: Callable) -> None:
        """Add a callback for when the Android TV is ready to receive commands or is unavailable."""
        self._is_available_updated_callbacks.append(callback)

    def remove_is_available_updated_callback(self, callback: Callable) -> None:
        """Remove a callback previously added via add_is_available_updated_callback.

        :raises ValueError: if callback not previously added.
        """
        self._is_available_updated_callbacks.remove(callback)

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

    async def _create_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context:
            return self._ssl_context
        try:
            ssl_context = await self._loop.run_in_executor(
                None, _load_cert_chain, self._certfile, self._keyfile
            )
        except FileNotFoundError as exc:
            LOGGER.debug("Missing certificate. Error: %s", exc)
            raise InvalidAuth from exc
        self._ssl_context = ssl_context
        return self._ssl_context

    async def async_connect(self) -> None:
        """Connect to an Android TV.

        :raises CannotConnect: if couldn't connect, e.g. invalid IP address.
        :raises ConnectionClosed: if connection was lost while waiting for the remote to start.
        :raises InvalidAuth: if pairing is needed first.
        """
        ssl_context = await self._create_ssl_context()
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
                    self._enable_ime,
                ),
                self.host,
                self._api_port,
                ssl=ssl_context,
            )
        except OSError as exc:
            LOGGER.debug(
                "Couldn't connect to %s:%s. Error: %s", self.host, self._api_port, exc
            )
            if isinstance(exc, ssl.SSLError):
                raise InvalidAuth("Need to pair") from exc
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
            delay_seconds = 0.1
            LOGGER.debug(
                "Trying to reconnect to %s in %s seconds", self.host, delay_seconds
            )
            while self._remote_message_protocol:
                await asyncio.sleep(delay_seconds)
                try:
                    await self.async_connect()
                    self._on_is_available_updated(True)
                    break
                except (CannotConnect, ConnectionClosed) as exc:
                    delay_seconds = min(2 * delay_seconds, 30)
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
        ssl_context = await self._create_ssl_context()
        try:
            _, writer = await asyncio.open_connection(
                self.host, self._pair_port, ssl=ssl_context
            )
        except OSError as exc:
            LOGGER.debug(
                "Couldn't connect to %s:%s. %s", self.host, self._pair_port, exc
            )
            raise CannotConnect from exc
        server_cert_bytes = writer.transport.get_extra_info("ssl_object").getpeercert(
            True
        )
        writer.close()
        server_cert = x509.load_der_x509_certificate(server_cert_bytes)
        # NVIDIA SHIELD example:
        # CN=atvremote/darcy/darcy/SHIELD Android TV/XX:XX:XX:XX:XX:XX
        # Nexus Player example:
        # dnQualifier=fugu/fugu/Nexus Player/CN=atvremote/XX:XX:XX:XX:XX:XX
        common_name = server_cert.subject.get_attributes_for_oid(x509.OID_COMMON_NAME)
        common_name_str = str(common_name[0].value) if common_name else ""
        dn_qualifier = server_cert.subject.get_attributes_for_oid(x509.OID_DN_QUALIFIER)
        dn_qualifier_str = str(dn_qualifier[0].value) if dn_qualifier else ""
        common_name_parts = common_name_str.split("/")
        dn_qualifier_parts = dn_qualifier_str.split("/")
        name = dn_qualifier_parts[-1] if dn_qualifier_str else common_name_parts[-2]
        mac = common_name_parts[-1]
        return name, mac

    async def async_start_pairing(self) -> None:
        """Start the pairing process.

        :raises CannotConnect: if couldn't connect, e.g. invalid IP address.
        :raises ConnectionClosed: if connection was lost.
        """
        self.disconnect()
        ssl_context = await self._create_ssl_context()
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

    async def async_finish_pairing(self, pairing_code: str) -> None:
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
    ) -> None:
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

    def send_text(self, text: str) -> None:
        """Send text to Android TV.

        :param text: text to be sent.
        :raises ConnectionClosed: if client is disconnected.
        :may not work as expected if virtual keyboard is present on the Android TV screen
        """
        if not self._remote_message_protocol:
            LOGGER.debug("Called send_text after disconnect")
            raise ConnectionClosed("Called send_text after disconnect")
        self._remote_message_protocol.send_text(text)

    def send_launch_app_command(self, app_link_or_app_id: str) -> None:
        """Launch an app on Android TV.

        This does not block; it buffers the data and arranges for it to be sent out asynchronously.

        :raises ConnectionClosed: if client is disconnected.
        """
        if not self._remote_message_protocol:
            LOGGER.debug("Called send_launch_app_command after disconnect")
            raise ConnectionClosed("Called send_launch_app_command after disconnect")
        prefix = "" if urlparse(app_link_or_app_id).scheme else "market://launch?id="
        self._remote_message_protocol.send_launch_app_command(
            f"{prefix}{app_link_or_app_id}"
        )
