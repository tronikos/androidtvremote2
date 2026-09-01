"""Tests for the length delimited protobuf framing in ProtobufProtocol."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from androidtvremote2.base import ProtobufProtocol
from androidtvremote2.remotemessage_pb2 import RemoteMessage

from .conftest import FakeTransport, frame, parse_written


class StubFuture:
    """A stand in for asyncio.Future so these tests need no event loop."""

    def __init__(self) -> None:
        """Initialize as not done."""
        self._done = False
        self._result: Any = None

    def done(self) -> bool:
        """Whether set_result was called."""
        return self._done

    def set_result(self, result: Any) -> None:
        """Record the result."""
        self._done = True
        self._result = result

    def result(self) -> Any:
        """Return the recorded result."""
        return self._result


class RecordingProtocol(ProtobufProtocol):
    """A ProtobufProtocol that keeps every message it handles."""

    def __init__(self) -> None:
        """Initialize with a future the tests can inspect."""
        super().__init__(StubFuture())  # type: ignore[arg-type]
        self.handled: list[bytes] = []

    def _handle_message(self, raw_msg: bytes) -> None:
        self.handled.append(raw_msg)


@pytest.fixture
def protocol() -> RecordingProtocol:
    """Return a RecordingProtocol attached to a FakeTransport."""
    proto = RecordingProtocol()
    proto.connection_made(FakeTransport())
    return proto


def _ping(val: int = 1) -> RemoteMessage:
    msg = RemoteMessage()
    msg.remote_ping_request.val1 = val
    return msg


def _big(size: int) -> bytes:
    """Build a raw message body large enough to need a multi byte length varint."""
    msg = RemoteMessage()
    msg.remote_ime_key_inject.app_info.app_package = "a" * size
    return msg.SerializeToString()


def test_single_message(protocol: RecordingProtocol) -> None:
    """A message arriving in one piece is handled."""
    body = _big(200)
    protocol.data_received(frame(body))
    assert protocol.handled == [body]


def test_multiple_messages_in_one_read(protocol: RecordingProtocol) -> None:
    """Several messages batched into one read are all handled, in order."""
    bodies = [_big(200), _big(10), _big(300)]
    protocol.data_received(b"".join(frame(b) for b in bodies))
    assert protocol.handled == bodies


def test_message_split_in_body(protocol: RecordingProtocol) -> None:
    """A message split inside its body is reassembled."""
    body = _big(200)
    data = frame(body)
    protocol.data_received(data[:50])
    assert protocol.handled == []
    protocol.data_received(data[50:])
    assert protocol.handled == [body]


def test_message_split_inside_length_varint(protocol: RecordingProtocol) -> None:
    """A read boundary inside the multi byte length varint is handled.

    Regression test: this used to raise IndexError out of data_received.
    """
    body = _big(200)
    data = frame(body)
    assert data[0] & 0x80, "test needs a multi byte length varint"
    protocol.data_received(data[:1])
    assert protocol.handled == []
    protocol.data_received(data[1:])
    assert protocol.handled == [body]


def test_complete_message_followed_by_partial_varint(protocol: RecordingProtocol) -> None:
    """A whole message plus the first byte of the next length varint is handled."""
    body = _big(200)
    data = frame(body)
    protocol.data_received(data + data[:1])
    assert protocol.handled == [body]
    protocol.data_received(data[1:])
    assert protocol.handled == [body, body]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 17, 128, 199, 201])
def test_byte_by_byte_and_arbitrary_splits(protocol: RecordingProtocol, size: int) -> None:
    """Any split point reassembles the same three messages."""
    bodies = [_big(200), _big(1), _big(20000)]
    data = b"".join(frame(b) for b in bodies)
    for i in range(0, len(data), size):
        protocol.data_received(data[i : i + size])
    assert protocol.handled == bodies


def test_one_byte_at_a_time(protocol: RecordingProtocol) -> None:
    """The most adversarial fragmentation still reassembles correctly."""
    bodies = [_big(300), _big(2)]
    for byte in b"".join(frame(b) for b in bodies):
        protocol.data_received(bytes([byte]))
    assert protocol.handled == bodies


def test_empty_data_is_ignored(protocol: RecordingProtocol) -> None:
    """An empty read doesn't disturb a partially received message."""
    body = _big(200)
    data = frame(body)
    protocol.data_received(data[:50])
    protocol.data_received(b"")
    protocol.data_received(data[50:])
    assert protocol.handled == [body]


def test_corrupt_length_varint_closes_the_connection(protocol: RecordingProtocol, caplog: pytest.LogCaptureFixture) -> None:
    """An unrecoverable length prefix closes the transport instead of spinning."""
    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"):
        # 11 continuation bytes is more than a 64 bit varint can hold.
        protocol.data_received(b"\xff" * 11)
    assert protocol.handled == []
    assert protocol.transport is not None
    assert protocol.transport.is_closing()
    assert "Couldn't decode the message length" in caplog.text


def test_connection_lost_sets_on_con_lost(protocol: RecordingProtocol) -> None:
    """connection_lost resolves the on_con_lost future exactly once."""
    exc = OSError("boom")
    protocol.connection_lost(exc)
    assert protocol.on_con_lost.result() is exc
    # A second call must not raise InvalidStateError.
    protocol.connection_lost(None)
    assert protocol.on_con_lost.result() is exc


def test_send_message_writes_length_prefixed_bytes(protocol: RecordingProtocol) -> None:
    """Sent messages are length delimited so the device can frame them."""
    msg = _ping(7)
    protocol._send_message(msg)
    transport = protocol.transport
    assert isinstance(transport, FakeTransport)
    assert parse_written(transport.written) == [msg]


def test_send_message_is_a_noop_when_closing(protocol: RecordingProtocol) -> None:
    """Nothing is written once the transport is closing."""
    transport = protocol.transport
    assert isinstance(transport, FakeTransport)
    transport.close()
    protocol._send_message(_ping())
    assert transport.written == b""


def test_send_message_without_transport_does_not_raise() -> None:
    """Sending before connection_made is a no-op rather than an AttributeError."""
    proto = RecordingProtocol()
    proto._send_message(_ping())


def test_send_message_can_suppress_debug_logging(protocol: RecordingProtocol, caplog: pytest.LogCaptureFixture) -> None:
    """should_debug_log=False keeps payloads such as voice data out of the log."""
    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"):
        protocol._send_message(_ping(), False)
    assert "Sending" not in caplog.text
    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"):
        protocol._send_message(_ping())
    assert "Sending" in caplog.text


def test_round_trip_through_both_sides(protocol: RecordingProtocol) -> None:
    """Whatever _send_message writes, data_received can read back."""
    messages = [_ping(1), _ping(2), _ping(300000)]
    for msg in messages:
        protocol._send_message(msg)
    transport = protocol.transport
    assert isinstance(transport, FakeTransport)
    protocol.data_received(bytes(transport.written))
    assert [RemoteMessage.FromString(raw) for raw in protocol.handled] == messages
