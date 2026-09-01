"""Tests for VoiceStream, the high level voice session wrapper."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from androidtvremote2 import VoiceStream
from androidtvremote2.exceptions import ConnectionClosed
from androidtvremote2.remote import VOICE_CHUNK_MIN_SIZE

if TYPE_CHECKING:
    from collections.abc import Callable

    from .conftest import RemoteHarness


@pytest.fixture
async def stream(remote_factory: Callable[..., RemoteHarness]) -> tuple[VoiceStream, RemoteHarness]:
    """Return a VoiceStream on session 42 and the harness behind it."""
    harness = remote_factory()
    return VoiceStream(harness.protocol, 42), harness


async def test_send_chunk_forwards_to_the_protocol(stream: tuple[VoiceStream, RemoteHarness]) -> None:
    """Audio is forwarded to the protocol tagged with the session id."""
    voice, harness = stream
    assert voice.send_chunk(b"a" * VOICE_CHUNK_MIN_SIZE) is True

    (sent,) = harness.sent()
    assert sent.remote_voice_payload.session_id == 42
    assert sent.remote_voice_payload.samples == b"a" * VOICE_CHUNK_MIN_SIZE


async def test_end_sends_voice_end_once(stream: tuple[VoiceStream, RemoteHarness]) -> None:
    """Ending twice only tells the device once."""
    voice, harness = stream
    voice.end()
    voice.end()

    (sent,) = harness.sent()
    assert sent.remote_voice_end.session_id == 42


async def test_send_chunk_after_end_is_refused(
    stream: tuple[VoiceStream, RemoteHarness], caplog: pytest.LogCaptureFixture
) -> None:
    """Audio sent after end() returns False instead of reaching the device."""
    voice, harness = stream
    voice.end()
    harness.clear()

    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"):
        assert voice.send_chunk(b"a" * VOICE_CHUNK_MIN_SIZE) is False

    assert harness.sent() == []
    assert "VoiceStream already closed" in caplog.text


async def test_send_chunk_raises_when_the_connection_is_gone(stream: tuple[VoiceStream, RemoteHarness]) -> None:
    """A lost connection surfaces as ConnectionClosed, not as silent data loss."""
    voice, harness = stream
    harness.transport.close()

    with pytest.raises(ConnectionClosed):
        voice.send_chunk(b"a" * VOICE_CHUNK_MIN_SIZE)


async def test_context_manager_ends_the_session(stream: tuple[VoiceStream, RemoteHarness]) -> None:
    """Leaving the async context manager ends the session."""
    voice, harness = stream
    async with voice as session:
        assert session is voice
        session.send_chunk(b"a" * VOICE_CHUNK_MIN_SIZE)

    assert harness.sent()[-1].remote_voice_end.session_id == 42


async def test_context_manager_ends_the_session_on_error(stream: tuple[VoiceStream, RemoteHarness]) -> None:
    """The session is ended even when the block raises."""
    voice, harness = stream
    with pytest.raises(RuntimeError, match="boom"):
        async with voice:
            raise RuntimeError("boom")

    assert harness.sent()[-1].remote_voice_end.session_id == 42
