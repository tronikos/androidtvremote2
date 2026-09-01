"""Tests for RemoteProtocol, the remote protocol with an Android TV."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from androidtvremote2.exceptions import ConnectionClosed, VoiceSessionInProgress
from androidtvremote2.remote import (
    VOICE_CHUNK_MIN_SIZE,
    VOICE_CHUNK_SIZE,
    Feature,
)
from androidtvremote2.remotemessage_pb2 import RemoteDirection, RemoteKeyCode, RemoteMessage

if TYPE_CHECKING:
    from collections.abc import Callable

    from .conftest import RemoteHarness

ALL_FEATURES = Feature.PING | Feature.KEY | Feature.IME | Feature.VOICE | Feature.POWER | Feature.VOLUME | Feature.APP_LINK


def configure_msg(code1: int = int(ALL_FEATURES)) -> RemoteMessage:
    """Build the remote_configure message the device sends first."""
    msg = RemoteMessage()
    msg.remote_configure.code1 = code1
    msg.remote_configure.device_info.vendor = "NVIDIA"
    msg.remote_configure.device_info.model = "SHIELD Android TV"
    msg.remote_configure.device_info.app_version = "1.2.3"
    return msg


# --- handshake and message handling -------------------------------------------------


async def test_configure_reports_device_info_and_negotiates_features(
    remote_factory: Callable[..., RemoteHarness],
) -> None:
    """remote_configure populates device_info and is answered with our own configure."""
    harness = remote_factory()
    harness.receive(configure_msg())

    assert harness.protocol.device_info == {
        "manufacturer": "NVIDIA",
        "model": "SHIELD Android TV",
        "sw_version": "1.2.3",
    }
    (reply,) = harness.sent()
    assert reply.remote_configure.code1 == int(ALL_FEATURES)
    assert reply.remote_configure.device_info.package_name == "atvremote"
    assert reply.remote_configure.device_info.app_version == "1.0.0"


async def test_features_are_intersected_with_the_device(remote_factory: Callable[..., RemoteHarness]) -> None:
    """Features the device doesn't advertise are dropped from the active set."""
    harness = remote_factory(enable_voice=True)
    assert harness.protocol.is_voice_enabled is True

    harness.receive(configure_msg(int(ALL_FEATURES & ~Feature.VOICE)))

    assert harness.protocol.is_voice_enabled is False
    (reply,) = harness.sent()
    assert not reply.remote_configure.code1 & Feature.VOICE


async def test_ime_can_be_disabled_by_the_client(remote_factory: Callable[..., RemoteHarness]) -> None:
    """enable_ime=False keeps the IME bit out of the requested features."""
    harness = remote_factory(enable_ime=False)
    harness.receive(configure_msg())
    (reply,) = harness.sent()
    assert not reply.remote_configure.code1 & Feature.IME


@pytest.mark.parametrize("missing", [Feature.KEY, Feature.APP_LINK])
async def test_missing_essential_feature_is_logged_as_an_error(
    remote_factory: Callable[..., RemoteHarness], caplog: pytest.LogCaptureFixture, missing: Feature
) -> None:
    """A device that can't accept keys or app links gets a actionable error message."""
    harness = remote_factory()
    with caplog.at_level(logging.ERROR, logger="androidtvremote2"):
        harness.receive(configure_msg(int(ALL_FEATURES & ~missing)))
    assert "Try clearing the storage" in caplog.text


async def test_unknown_feature_bits_do_not_break_negotiation(
    remote_factory: Callable[..., RemoteHarness],
) -> None:
    """Feature bits this library doesn't know about are simply not requested."""
    harness = remote_factory()
    harness.receive(configure_msg(int(ALL_FEATURES) | 1 << 20))
    (reply,) = harness.sent()
    assert reply.remote_configure.code1 == int(ALL_FEATURES)


async def test_set_active_is_answered(remote_factory: Callable[..., RemoteHarness]) -> None:
    """remote_set_active is answered with the active feature set."""
    harness = remote_factory()
    harness.receive(configure_msg())
    harness.clear()

    msg = RemoteMessage()
    msg.remote_set_active.active = 622
    harness.receive(msg)

    (reply,) = harness.sent()
    assert reply.remote_set_active.active == int(ALL_FEATURES)


async def test_ping_is_answered_with_the_same_value(remote_factory: Callable[..., RemoteHarness]) -> None:
    """Ping requests are echoed back so the device keeps the connection."""
    harness = remote_factory()
    msg = RemoteMessage()
    msg.remote_ping_request.val1 = 4242
    harness.receive(msg)

    (reply,) = harness.sent()
    assert reply.remote_ping_response.val1 == 4242


async def test_remote_start_updates_is_on_and_resolves_the_future(
    remote_factory: Callable[..., RemoteHarness],
) -> None:
    """remote_start marks the remote as started and reports the power state."""
    harness = remote_factory()
    msg = RemoteMessage()
    msg.remote_start.started = True
    harness.receive(msg)

    assert harness.protocol.is_on is True
    assert harness.is_on_updates == [True]
    assert harness.protocol._on_remote_started.result() is True

    msg = RemoteMessage()
    msg.remote_start.started = False
    harness.receive(msg)
    assert harness.protocol.is_on is False
    assert harness.is_on_updates == [True, False]
    # Nothing is sent in response to remote_start.
    assert harness.sent() == []


async def test_ime_key_inject_updates_current_app(remote_factory: Callable[..., RemoteHarness]) -> None:
    """The foreground app is taken from remote_ime_key_inject."""
    harness = remote_factory()
    msg = RemoteMessage()
    msg.remote_ime_key_inject.app_info.app_package = "com.google.android.youtube.tv"
    harness.receive(msg)

    assert harness.protocol.current_app == "com.google.android.youtube.tv"
    assert harness.current_app_updates == ["com.google.android.youtube.tv"]


async def test_volume_level_updates_volume_info(remote_factory: Callable[..., RemoteHarness]) -> None:
    """remote_set_volume_level is exposed as volume_info and reported to callbacks."""
    harness = remote_factory()
    msg = RemoteMessage()
    msg.remote_set_volume_level.volume_level = 12
    msg.remote_set_volume_level.volume_max = 100
    msg.remote_set_volume_level.volume_muted = True
    harness.receive(msg)

    assert harness.protocol.volume_info == {"level": 12, "max": 100, "muted": True}
    assert harness.volume_info_updates == [{"level": 12, "max": 100, "muted": True}]


async def test_ime_batch_edit_updates_the_counters_used_by_send_text(
    remote_factory: Callable[..., RemoteHarness],
) -> None:
    """The counters echoed back in send_text come from the device."""
    harness = remote_factory()
    msg = RemoteMessage()
    msg.remote_ime_batch_edit.ime_counter = 9
    msg.remote_ime_batch_edit.field_counter = 4
    harness.receive(msg)

    assert (harness.protocol.ime_counter, harness.protocol.ime_field_counter) == (9, 4)

    harness.protocol.send_text("hi")
    (sent,) = harness.sent()
    assert sent.remote_ime_batch_edit.ime_counter == 9
    assert sent.remote_ime_batch_edit.field_counter == 4


async def test_remote_error_is_logged_as_an_error(
    remote_factory: Callable[..., RemoteHarness], caplog: pytest.LogCaptureFixture
) -> None:
    """An error reported by the device is surfaced, not swallowed as 'unhandled'."""
    harness = remote_factory()
    msg = RemoteMessage()
    msg.remote_error.value = True
    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"):
        harness.receive(msg)

    assert "Received an error from the device" in caplog.text
    assert "Unhandled" not in caplog.text
    assert harness.sent() == []


async def test_unhandled_message_is_logged_and_ignored(
    remote_factory: Callable[..., RemoteHarness], caplog: pytest.LogCaptureFixture
) -> None:
    """A message this library has no branch for doesn't produce a reply."""
    harness = remote_factory()
    msg = RemoteMessage()
    msg.remote_ime_show_request.SetInParent()
    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"):
        harness.receive(msg)

    assert "Unhandled" in caplog.text
    assert harness.sent() == []


async def test_undecodable_message_does_not_raise(
    remote_factory: Callable[..., RemoteHarness], caplog: pytest.LogCaptureFixture
) -> None:
    """Garbage that happens to be framed correctly is logged and skipped."""
    harness = remote_factory()
    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"):
        harness.protocol.data_received(b"\x03\xff\xff\xff")

    assert "Couldn't parse as RemoteMessage" in caplog.text


# --- sending commands ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("key_code", "expected"),
    [
        (26, 26),
        ("POWER", RemoteKeyCode.KEYCODE_POWER),
        ("KEYCODE_POWER", RemoteKeyCode.KEYCODE_POWER),
        ("power", RemoteKeyCode.KEYCODE_POWER),
        ("keycode_power", RemoteKeyCode.KEYCODE_POWER),
        ("DPAD_UP", RemoteKeyCode.KEYCODE_DPAD_UP),
    ],
)
async def test_send_key_command_accepts_ints_and_names(
    remote_factory: Callable[..., RemoteHarness], key_code: int | str, expected: int
) -> None:
    """Key codes can be ints, bare names or fully qualified names, in any case."""
    harness = remote_factory()
    harness.protocol.send_key_command(key_code)

    (sent,) = harness.sent()
    assert sent.remote_key_inject.key_code == expected
    assert sent.remote_key_inject.direction == RemoteDirection.SHORT


@pytest.mark.parametrize("direction", ["START_LONG", "start_long", RemoteDirection.START_LONG])
async def test_send_key_command_accepts_directions(remote_factory: Callable[..., RemoteHarness], direction: int | str) -> None:
    """Directions can be given as ints or as case insensitive names."""
    harness = remote_factory()
    harness.protocol.send_key_command("POWER", direction)

    (sent,) = harness.sent()
    assert sent.remote_key_inject.direction == RemoteDirection.START_LONG


@pytest.mark.parametrize(("key_code", "direction"), [("NOT_A_KEY", "SHORT"), ("POWER", "SIDEWAYS")])
async def test_send_key_command_rejects_unknown_names(
    remote_factory: Callable[..., RemoteHarness], key_code: str, direction: str
) -> None:
    """Unknown key codes and directions raise ValueError."""
    harness = remote_factory()
    with pytest.raises(ValueError, match="Enum"):
        harness.protocol.send_key_command(key_code, direction)


@pytest.mark.parametrize("prefix", ["text:", "TEXT:", "Text:"])
async def test_send_key_command_dispatches_the_text_prefix(remote_factory: Callable[..., RemoteHarness], prefix: str) -> None:
    """A 'text:' prefixed key code is sent through the input method instead."""
    harness = remote_factory()
    harness.protocol.send_key_command(prefix + "Hello World!")

    (sent,) = harness.sent()
    assert sent.HasField("remote_ime_batch_edit")
    assert sent.remote_ime_batch_edit.edit_info[0].text_field_status.value == "Hello World!"


async def test_send_text_builds_the_batch_edit(remote_factory: Callable[..., RemoteHarness]) -> None:
    """send_text uses len(text) - 1 for start and end, as the device expects."""
    harness = remote_factory()
    harness.protocol.send_text("abcd")

    (sent,) = harness.sent()
    edit = sent.remote_ime_batch_edit.edit_info[0]
    assert edit.insert == 1
    assert edit.text_field_status.value == "abcd"
    assert edit.text_field_status.start == 3
    assert edit.text_field_status.end == 3


async def test_send_text_rejects_empty_text(remote_factory: Callable[..., RemoteHarness]) -> None:
    """Empty text is rejected rather than sent as a malformed edit."""
    harness = remote_factory()
    with pytest.raises(ValueError, match="Text cannot be empty"):
        harness.protocol.send_text("")
    assert harness.sent() == []


async def test_send_launch_app_command(remote_factory: Callable[..., RemoteHarness]) -> None:
    """App links are forwarded verbatim by the protocol layer."""
    harness = remote_factory()
    harness.protocol.send_launch_app_command("https://www.youtube.com")

    (sent,) = harness.sent()
    assert sent.remote_app_link_launch_request.app_link == "https://www.youtube.com"


async def yield_to_loop(times: int = 3) -> None:
    """Let pending callbacks run without advancing the clock meaningfully."""
    for _ in range(times):
        await asyncio.sleep(0)


# --- voice --------------------------------------------------------------------------


async def begin_voice(harness: RemoteHarness, session_id: int = 77, timeout: float = 1.0) -> int:
    """Start a voice session, answering remote_voice_begin as the device would."""
    task = asyncio.ensure_future(harness.protocol.start_voice(timeout))
    await yield_to_loop()
    msg = RemoteMessage()
    msg.remote_voice_begin.session_id = session_id
    harness.receive(msg)
    return await task


async def test_start_voice_sends_search_then_voice_begin(remote_factory: Callable[..., RemoteHarness]) -> None:
    """start_voice sends KEYCODE_SEARCH and echoes remote_voice_begin back."""
    harness = remote_factory()
    session_id = await begin_voice(harness)

    assert session_id == 77
    sent = harness.sent()
    assert sent[0].remote_key_inject.key_code == RemoteKeyCode.KEYCODE_SEARCH
    assert sent[-1].remote_voice_begin.session_id == 77


async def test_start_voice_times_out_without_a_response(remote_factory: Callable[..., RemoteHarness]) -> None:
    """A device that never begins voice produces a TimeoutError."""
    harness = remote_factory()
    with pytest.raises(asyncio.TimeoutError):
        await harness.protocol.start_voice(timeout=0.01)

    # The failed attempt must not leave a session behind.
    assert harness.protocol._voice_session_id is None
    session_id = await begin_voice(harness)
    assert session_id == 77


async def test_start_voice_requires_a_connection(remote_factory: Callable[..., RemoteHarness]) -> None:
    """Starting a session on a closed connection raises ConnectionClosed."""
    harness = remote_factory()
    harness.transport.close()
    with pytest.raises(ConnectionClosed):
        await harness.protocol.start_voice()


async def test_start_voice_rejects_a_second_session(remote_factory: Callable[..., RemoteHarness]) -> None:
    """Only one voice session can be open at a time, until it is ended.

    Regression test: the guard used to only cover the setup of the session, so a
    second sequential start_voice() silently opened a second session.
    """
    harness = remote_factory()
    await begin_voice(harness)

    with pytest.raises(VoiceSessionInProgress):
        await harness.protocol.start_voice()

    harness.protocol.end_voice(77)
    assert await begin_voice(harness, session_id=78) == 78


async def test_start_voice_rejects_a_concurrent_session(remote_factory: Callable[..., RemoteHarness]) -> None:
    """A second start_voice while the first is still awaiting is rejected too."""
    harness = remote_factory()
    task = asyncio.ensure_future(harness.protocol.start_voice(timeout=1.0))
    await yield_to_loop()

    with pytest.raises(VoiceSessionInProgress):
        await harness.protocol.start_voice()

    msg = RemoteMessage()
    msg.remote_voice_begin.session_id = 5
    harness.receive(msg)
    assert await task == 5


async def test_losing_the_connection_clears_the_voice_session(
    remote_factory: Callable[..., RemoteHarness],
) -> None:
    """A dropped connection doesn't leave a session id blocking future sessions."""
    harness = remote_factory()
    await begin_voice(harness)
    harness.protocol.connection_lost(None)

    assert harness.protocol._voice_session_id is None


async def test_end_voice_sends_the_end_message(remote_factory: Callable[..., RemoteHarness]) -> None:
    """end_voice tells the device the session is over."""
    harness = remote_factory()
    await begin_voice(harness)
    harness.clear()

    harness.protocol.end_voice(77)
    (sent,) = harness.sent()
    assert sent.remote_voice_end.session_id == 77


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, []),
        (100, [VOICE_CHUNK_MIN_SIZE]),
        (VOICE_CHUNK_MIN_SIZE, [VOICE_CHUNK_MIN_SIZE]),
        (VOICE_CHUNK_SIZE, [VOICE_CHUNK_SIZE]),
        (VOICE_CHUNK_SIZE + 1, [VOICE_CHUNK_SIZE, VOICE_CHUNK_MIN_SIZE]),
        (2 * VOICE_CHUNK_SIZE, [VOICE_CHUNK_SIZE, VOICE_CHUNK_SIZE]),
        (80000, [VOICE_CHUNK_SIZE, VOICE_CHUNK_SIZE, VOICE_CHUNK_SIZE, 18560]),
    ],
)
async def test_voice_chunks_are_split_and_padded(
    remote_factory: Callable[..., RemoteHarness], size: int, expected: list[int]
) -> None:
    """Every payload is at most VOICE_CHUNK_SIZE and at least VOICE_CHUNK_MIN_SIZE.

    Regression test: padding used to happen before splitting, so the trailing piece
    of a chunk just over VOICE_CHUNK_SIZE was sent below the minimum size.
    """
    harness = remote_factory()
    harness.protocol.send_voice_chunk(b"a" * size, 77)

    payloads = [msg.remote_voice_payload.samples for msg in harness.sent()]
    assert [len(p) for p in payloads] == expected
    assert all(VOICE_CHUNK_MIN_SIZE <= len(p) <= VOICE_CHUNK_SIZE for p in payloads)
    # The audio itself is preserved, only trailing silence is added.
    assert b"".join(payloads).rstrip(b"\x00") == b"a" * size


async def test_voice_chunk_payloads_carry_the_session_id(remote_factory: Callable[..., RemoteHarness]) -> None:
    """Each payload is tagged with the session it belongs to."""
    harness = remote_factory()
    harness.protocol.send_voice_chunk(b"a" * (2 * VOICE_CHUNK_SIZE), 12)

    assert [msg.remote_voice_payload.session_id for msg in harness.sent()] == [12, 12]


async def test_send_voice_chunk_requires_a_connection(remote_factory: Callable[..., RemoteHarness]) -> None:
    """Audio sent after the connection dropped raises instead of vanishing."""
    harness = remote_factory()
    harness.transport.close()
    with pytest.raises(ConnectionClosed):
        harness.protocol.send_voice_chunk(b"a" * VOICE_CHUNK_MIN_SIZE, 77)


async def test_unexpected_voice_begin_is_ignored(
    remote_factory: Callable[..., RemoteHarness], caplog: pytest.LogCaptureFixture
) -> None:
    """A remote_voice_begin nobody asked for doesn't raise."""
    harness = remote_factory()
    msg = RemoteMessage()
    msg.remote_voice_begin.session_id = 3
    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"):
        harness.receive(msg)

    assert "no client request available" in caplog.text


# --- idle disconnect ----------------------------------------------------------------


async def test_idle_disconnect_task_is_replaced_on_activity(
    remote_factory: Callable[..., RemoteHarness],
) -> None:
    """Each message resets the idle timer, cancelling the previous task."""
    harness = remote_factory()
    first = harness.protocol._idle_disconnect_task
    assert first is not None

    harness.protocol.send_key_command("POWER")
    second = harness.protocol._idle_disconnect_task
    await yield_to_loop()

    assert second is not None
    assert second is not first
    assert first.cancelled()
    assert not second.done()


async def test_connection_lost_cancels_the_idle_disconnect_task(
    remote_factory: Callable[..., RemoteHarness],
) -> None:
    """A lost connection doesn't leave a task pending for another 16 seconds.

    Regression test: the task used to outlive the connection, keeping the protocol
    alive and logging 'Task was destroyed but it is pending' at shutdown.
    """
    harness = remote_factory()
    task = harness.protocol._idle_disconnect_task
    assert task is not None

    harness.protocol.connection_lost(None)
    await yield_to_loop()

    assert harness.protocol._idle_disconnect_task is None
    assert task.cancelled()


async def test_close_cancels_the_idle_disconnect_task(remote_factory: Callable[..., RemoteHarness]) -> None:
    """close() stops the timer even if connection_lost hasn't run yet."""
    harness = remote_factory()
    task = harness.protocol._idle_disconnect_task
    assert task is not None

    harness.protocol.close()
    await yield_to_loop()

    assert harness.protocol._idle_disconnect_task is None
    assert task.cancelled()
    assert harness.transport.is_closing()


async def test_no_new_idle_task_once_the_transport_is_closing(
    remote_factory: Callable[..., RemoteHarness],
) -> None:
    """Sending after close doesn't resurrect the idle disconnect task."""
    harness = remote_factory()
    harness.protocol.close()

    harness.protocol.send_key_command("POWER")

    assert harness.protocol._idle_disconnect_task is None


async def test_idle_disconnect_closes_the_connection(
    remote_factory: Callable[..., RemoteHarness], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the idle timeout the connection is closed and reported as lost."""
    harness = remote_factory()
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    # remote.py calls asyncio.sleep directly, so patching it here reaches that call.
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await harness.protocol._async_idle_disconnect()

    assert slept == [16]
    assert harness.transport.is_closing()
    assert isinstance(harness.protocol.on_con_lost.result(), Exception)
