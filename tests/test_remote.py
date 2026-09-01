"""Tests for RemoteProtocol, the remote protocol with an Android TV."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from androidtvremote2.remote import Feature
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
        ("DPAD_UP", RemoteKeyCode.KEYCODE_DPAD_UP),
    ],
)
async def test_send_key_command_accepts_ints_and_names(
    remote_factory: Callable[..., RemoteHarness], key_code: int | str, expected: int
) -> None:
    """Key codes can be given as ints, bare names or fully qualified names."""
    harness = remote_factory()
    harness.protocol.send_key_command(key_code)

    (sent,) = harness.sent()
    assert sent.remote_key_inject.key_code == expected
    assert sent.remote_key_inject.direction == RemoteDirection.SHORT


@pytest.mark.parametrize("direction", ["START_LONG", RemoteDirection.START_LONG])
async def test_send_key_command_accepts_directions(remote_factory: Callable[..., RemoteHarness], direction: int | str) -> None:
    """Directions can be given as ints or as names."""
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
