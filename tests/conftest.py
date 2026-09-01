"""Shared fixtures and helpers for the androidtvremote2 tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from google.protobuf.internal.decoder import _DecodeVarint  # type: ignore[attr-defined]
from google.protobuf.internal.encoder import _EncodeVarint  # type: ignore[attr-defined]

from androidtvremote2.certificate_generator import generate_selfsigned_cert
from androidtvremote2.polo_pb2 import OuterMessage
from androidtvremote2.remote import RemoteProtocol
from androidtvremote2.remotemessage_pb2 import RemoteMessage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from google.protobuf.message import Message


def frame(msg: Message | bytes) -> bytes:
    """Length delimit a protobuf message the way the Android TV does."""
    raw = msg if isinstance(msg, bytes) else msg.SerializeToString()
    out: list[bytes] = []
    _EncodeVarint(out.append, len(raw))
    return b"".join(out) + raw


class FakeTransport(asyncio.Transport):
    """An in-memory transport that records everything written to it."""

    def __init__(self, extra: dict[str, Any] | None = None) -> None:
        """Initialize."""
        super().__init__()
        self.written = bytearray()
        self.closed = False
        self._extra_info = extra or {}

    def write(self, data: Any) -> None:
        """Record the written bytes."""
        self.written += data

    def close(self) -> None:
        """Mark the transport as closing."""
        self.closed = True

    def is_closing(self) -> bool:
        """Whether close was called."""
        return self.closed

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """Return transport specific extra info."""
        return self._extra_info.get(name, default)


def parse_written(written: bytes | bytearray, message_type: type[Message] = RemoteMessage) -> list[Message]:
    """Parse a stream of length delimited messages, mirroring ProtobufProtocol.data_received."""
    messages = []
    buffer = memoryview(bytes(written))
    while buffer:
        msg_len, pos = _DecodeVarint(buffer, 0)
        msg = message_type()
        msg.ParseFromString(bytes(buffer[pos : pos + msg_len]))
        messages.append(msg)
        buffer = buffer[pos + msg_len :]
    return messages


class RemoteHarness:
    """A RemoteProtocol wired up to a FakeTransport, with the callbacks recorded."""

    def __init__(self, enable_ime: bool = True, enable_voice: bool = True) -> None:
        """Build a connected RemoteProtocol recording every callback into a list."""
        self.is_on_updates: list[bool] = []
        self.current_app_updates: list[str] = []
        self.volume_info_updates: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        self.protocol = RemoteProtocol(
            on_con_lost=loop.create_future(),
            on_remote_started=loop.create_future(),
            on_is_on_updated=self.is_on_updates.append,
            on_current_app_updated=self.current_app_updates.append,
            on_volume_info_updated=lambda value: self.volume_info_updates.append(dict(value)),
            loop=loop,
            enable_ime=enable_ime,
            enable_voice=enable_voice,
        )
        self.transport = FakeTransport()
        self.protocol.connection_made(self.transport)

    def receive(self, *msgs: RemoteMessage) -> None:
        """Deliver messages to the protocol as the device would."""
        self.protocol.data_received(b"".join(frame(msg) for msg in msgs))

    def sent(self) -> list[RemoteMessage]:
        """Return every message written to the transport so far."""
        return parse_written(self.transport.written)  # type: ignore[return-value]

    def clear(self) -> None:
        """Forget everything written so far."""
        self.transport.written.clear()


@pytest.fixture
def remote_factory() -> Iterator[Callable[..., RemoteHarness]]:
    """Return a factory building connected RemoteProtocol instances."""
    harnesses: list[RemoteHarness] = []

    def _factory(enable_ime: bool = True, enable_voice: bool = True) -> RemoteHarness:
        harness = RemoteHarness(enable_ime, enable_voice)
        harnesses.append(harness)
        return harness

    yield _factory

    for harness in harnesses:
        harness.transport.close()


@pytest.fixture(scope="session")
def client_cert_and_key() -> tuple[bytes, bytes]:
    """Generate a client certificate and key once for the whole session."""
    return generate_selfsigned_cert("client")


@pytest.fixture(scope="session")
def server_cert_and_key() -> tuple[bytes, bytes]:
    """Generate a server certificate and key once for the whole session."""
    return generate_selfsigned_cert("atvremote/darcy/darcy/SHIELD Android TV/AA:BB:CC:DD:EE:FF")


@pytest.fixture
def certfile(client_cert_and_key: tuple[bytes, bytes], tmp_path: Path) -> str:
    """Write the client certificate to a temporary file and return its path."""
    path = tmp_path / "cert.pem"
    path.write_bytes(client_cert_and_key[0])
    return str(path)


def polo_message() -> OuterMessage:
    """Build an OuterMessage with the fields the protocol always sets."""
    msg = OuterMessage()
    msg.protocol_version = 2
    msg.status = OuterMessage.Status.STATUS_OK
    return msg
