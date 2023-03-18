"""Protocol for receiving and sending protobuf messages."""

from __future__ import annotations

import asyncio
from typing import cast

from google.protobuf import text_format
from google.protobuf.internal.decoder import _DecodeVarint
from google.protobuf.internal.encoder import _EncodeVarint
from google.protobuf.message import Message

from .const import LOGGER


class ProtobufProtocol(asyncio.Protocol):
    """Protocol for receiving and sending protobuf messages."""

    def __init__(self, on_con_lost: asyncio.Future) -> None:
        """Initialize.

        :param on_con_lost: callback for when the connection is lost or closed.
        """
        self.on_con_lost = on_con_lost
        self.transport: asyncio.Transport | None = None
        self._raw_msg_len = -1
        self._raw_msg = b""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Store transport when a connection is made."""
        LOGGER.debug("Connected to %s", transport.get_extra_info("peername"))
        self.transport = cast(asyncio.Transport, transport)

    def connection_lost(self, exc: Exception | None) -> None:
        """Notify on_con_lost when the connection is lost or closed."""
        LOGGER.debug("Connection lost. Error: %s", exc)
        if not self.on_con_lost.done():
            self.on_con_lost.set_result(exc)

    def data_received(self, data: bytes) -> None:
        """Receive data until a full protobuf is received and pass it to _handle_message."""
        if not data:
            LOGGER.debug("No data received")
            return
        if self._raw_msg_len < 0:
            self._raw_msg_len, pos = _DecodeVarint(data, 0)
            pos_end = pos + self._raw_msg_len
            self._raw_msg += data[pos:pos_end]
            remaining_data = data[pos_end:]
        else:
            pos_end = self._raw_msg_len - len(self._raw_msg)
            self._raw_msg += data[:pos_end]
            remaining_data = data[pos_end:]
        if self._raw_msg_len == len(self._raw_msg):
            raw_msg = self._raw_msg
            self._raw_msg_len = -1
            self._raw_msg = b""
            # LOGGER.debug("Received: %s", raw_msg)
            self._handle_message(raw_msg)
            if remaining_data:
                self.data_received(remaining_data)

    def _handle_message(self, raw_msg: bytes) -> None:
        """Handle a message from the server. Message needs to be parsed to the appropriate protobuf."""

    def _send_message(self, msg: Message, should_debug_log: bool = True) -> None:
        """Send a protobuf message to the server.

        This does not block; it buffers the data and arranges for it to be sent out asynchronously.
        """
        if should_debug_log:
            LOGGER.debug(
                "Sending: %s", text_format.MessageToString(msg, as_one_line=True)
            )
        if not self.transport or self.transport.is_closing():
            LOGGER.debug("Connection is closed!")
            return
        _EncodeVarint(self.transport.write, msg.ByteSize())
        self.transport.write(msg.SerializeToString())
