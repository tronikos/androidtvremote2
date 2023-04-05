"""Pairing protocol with an Android TV."""

# Based on:
# https://android.googlesource.com/platform/external/google-tv-pairing-protocol/+/refs/heads/master/java/src/com/google/polo/pairing/
# https://github.com/louis49/androidtv-remote/tree/main/src/pairing
# https://github.com/farshid616/Android-TV-Remote-Controller-Python/blob/main/pairing.py

from __future__ import annotations

import asyncio
import hashlib

import aiofiles
from cryptography import x509
from google.protobuf import text_format
from google.protobuf.message import DecodeError

from .base import ProtobufProtocol
from .const import LOGGER
from .exceptions import ConnectionClosed, InvalidAuth
from .polo_pb2 import Options, OuterMessage


def _create_message():
    """Create an OuterMessage with default values."""
    msg = OuterMessage()
    msg.protocol_version = 2
    msg.status = OuterMessage.Status.STATUS_OK
    return msg


def _get_modulus_and_exponent(cert):
    """Extract modulus and exponent from a certificate."""
    public_numbers = cert.public_key().public_numbers()
    return public_numbers.n, public_numbers.e


class PairingProtocol(ProtobufProtocol):
    """Implement pairing protocol with an Android TV.

    Messages transmitted between client and server are of type OuterMessage, see polo.proto.
    Protocol is described in
    https://github.com/Aymkdn/assistant-freebox-cloud/wiki/Google-TV-(aka-Android-TV)-Remote-Control-(v2)
    """

    def __init__(
        self,
        on_con_lost: asyncio.Future,
        client_name: str,
        certfile: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Initialize.

        :param on_con_lost: callback for when the connection is lost or closed.
        :param client_name: client name. Will be shown on the Android TV during pairing.
        :param certfile: filename that contains the client certificate in PEM format.
                         Needed for computing the secret code during pairing.
        :param loop: event loop. Used for creating futures.
        """
        super().__init__(on_con_lost)
        self._client_name = client_name
        self._certfile = certfile
        self._loop = loop
        self._on_pairing_started: asyncio.Future | None = None
        self._on_pairing_finished: asyncio.Future | None = None

    async def async_start_pairing(self):
        """Start the pairing process.

        :raises ConnectionClosed: if connection was lost.
        """
        self._raise_if_not_connected()
        msg = _create_message()
        msg.pairing_request.client_name = self._client_name
        msg.pairing_request.service_name = "atvremote"
        self._on_pairing_started = self._loop.create_future()
        self._send_message(msg)
        try:
            await self._async_wait_for_future_or_con_lost(self._on_pairing_started)
        finally:
            self._on_pairing_started = None

    async def async_finish_pairing(self, pairing_code: str):
        """Finish the pairing process.

        :param pairing_code: pairing code shown on the Android TV.
        :raises ConnectionClosed: if connection was lost.
        :raises InvalidAuth: if pairing was unsuccessful.
        """
        self._raise_if_not_connected()
        if not pairing_code or len(pairing_code) != 6:
            LOGGER.debug("Length of PIN (%s) should be exactly 6", pairing_code)
            raise InvalidAuth("Length of PIN should be exactly 6")
        try:
            bytes.fromhex(pairing_code)
        except ValueError as exc:
            LOGGER.debug("PIN (%s) should be in hex", pairing_code)
            raise InvalidAuth("PIN should be in hex") from exc

        async with aiofiles.open(self._certfile, "rb") as fp:
            client_cert = x509.load_pem_x509_certificate(await fp.read())
        client_modulus, client_exponent = _get_modulus_and_exponent(client_cert)

        assert self.transport
        server_cert_bytes = self.transport.get_extra_info("ssl_object").getpeercert(
            True
        )
        server_cert = x509.load_der_x509_certificate(server_cert_bytes)
        server_modulus, server_exponent = _get_modulus_and_exponent(server_cert)

        h = hashlib.sha256()
        h.update(bytes.fromhex(f"{client_modulus:X}"))
        h.update(bytes.fromhex(f"0{client_exponent:X}"))
        h.update(bytes.fromhex(f"{server_modulus:X}"))
        h.update(bytes.fromhex(f"0{server_exponent:X}"))
        h.update(bytes.fromhex(pairing_code[2:]))
        hash_result = h.digest()

        if hash_result[0] != int(pairing_code[0:2], 16):
            LOGGER.debug("Unexpected hash for pairing code: %s", pairing_code)
            raise InvalidAuth(f"Unexpected hash for pairing code: {pairing_code}")

        msg = _create_message()
        msg.secret.secret = hash_result
        self._on_pairing_finished = self._loop.create_future()
        self._send_message(msg)
        try:
            await self._async_wait_for_future_or_con_lost(self._on_pairing_finished)
        finally:
            self._on_pairing_finished = None

    async def _async_wait_for_future_or_con_lost(self, future: asyncio.Future):
        """Wait for future to finish or connection to be lost."""
        await asyncio.wait(
            (self.on_con_lost, future), return_when=asyncio.FIRST_COMPLETED
        )
        if future.done():
            if future.exception():
                raise ConnectionClosed(future.exception())
            if future.result():
                return
        self._raise_if_not_connected()

    def _raise_if_not_connected(self):
        """Raise ConnectionClosed if not connected."""
        if self.transport is None or self.transport.is_closing():
            LOGGER.debug("Connection has been lost, cannot pair")
            raise ConnectionClosed("Connection has been lost")

    def _handle_message(self, raw_msg: bytes):
        """Handle a message from the server."""
        msg = OuterMessage()
        try:
            msg.ParseFromString(raw_msg)
        except DecodeError as exc:
            LOGGER.debug("Couldn't parse as OuterMessage. %s", exc)
            self._handle_error(exc)
            return
        LOGGER.debug("Received: %s", text_format.MessageToString(msg, as_one_line=True))

        if msg.status != OuterMessage.Status.STATUS_OK:
            LOGGER.debug(
                "Received status: %s in msg: %s",
                msg.status,
                text_format.MessageToString(msg, as_one_line=True),
            )
            self._handle_error(Exception(f"Received status: {msg.status}"))
            return

        new_msg = _create_message()

        if msg.HasField("pairing_request_ack"):
            new_msg.options.preferred_role = Options.RoleType.ROLE_TYPE_INPUT
            enc = new_msg.options.input_encodings.add()
            enc.type = Options.Encoding.ENCODING_TYPE_HEXADECIMAL
            enc.symbol_length = 6
        elif msg.HasField("options"):
            new_msg.configuration.client_role = Options.RoleType.ROLE_TYPE_INPUT
            new_msg.configuration.encoding.type = (
                Options.Encoding.ENCODING_TYPE_HEXADECIMAL
            )
            new_msg.configuration.encoding.symbol_length = 6
        elif msg.HasField("configuration_ack"):
            if self._on_pairing_started:
                self._on_pairing_started.set_result(True)
            return
        elif msg.HasField("secret_ack"):
            if self._on_pairing_finished:
                self._on_pairing_finished.set_result(True)
            return
        else:
            LOGGER.debug(
                "Unhandled msg: %s", text_format.MessageToString(msg, as_one_line=True)
            )
            self._handle_error(
                Exception(
                    f"Unhandled msg: {text_format.MessageToString(msg, as_one_line=True)}"
                )
            )
            return

        self._send_message(new_msg)

    def _handle_error(self, exception):
        """Handle errors during _handle_message."""
        if self._on_pairing_started and not self._on_pairing_started.done():
            self._on_pairing_started.set_exception(exception)
        if self._on_pairing_finished and not self._on_pairing_finished.done():
            self._on_pairing_finished.set_exception(exception)
        self.transport.close()
