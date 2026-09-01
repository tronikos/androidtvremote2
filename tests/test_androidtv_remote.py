"""Tests for AndroidTVRemote, the public entry point of the library."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from androidtvremote2 import AndroidTVRemote, VoiceStream
from androidtvremote2.androidtv_remote import _parse_name_and_mac
from androidtvremote2.exceptions import ConnectionClosed, InvalidAuth
from androidtvremote2.remote import VOICE_CHUNK_MIN_SIZE
from androidtvremote2.remotemessage_pb2 import RemoteDirection, RemoteKeyCode, RemoteMessage

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .conftest import RemoteHarness


@pytest.fixture
async def remote(tmp_path: Path) -> AndroidTVRemote:
    """Return an AndroidTVRemote pointing at temporary certificate paths."""
    return AndroidTVRemote(
        client_name="pytest",
        certfile=str(tmp_path / "cert.pem"),
        keyfile=str(tmp_path / "key.pem"),
        host="192.0.2.1",
    )


@pytest.fixture
async def connected(remote: AndroidTVRemote, remote_factory: Callable[..., RemoteHarness]) -> RemoteHarness:
    """Attach a RemoteProtocol harness to the facade, as async_connect would."""
    harness = remote_factory()
    remote._remote_message_protocol = harness.protocol
    return harness


# --- app links ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_link_or_app_id", "expected"),
    [
        # App ids have no scheme, so they go through the Play Store.
        ("org.xbmc.kodi", "market://launch?id=org.xbmc.kodi"),
        ("com.google.android.youtube.tv", "market://launch?id=com.google.android.youtube.tv"),
        # Anything with a scheme is a deep link and is passed through untouched.
        ("https://www.youtube.com", "https://www.youtube.com"),
        ("https://app.primevideo.com", "https://app.primevideo.com"),
        ("netflix://", "netflix://"),
        ("market://launch?id=org.xbmc.kodi", "market://launch?id=org.xbmc.kodi"),
    ],
)
async def test_send_launch_app_command_prefixes_only_app_ids(
    remote: AndroidTVRemote, connected: RemoteHarness, app_link_or_app_id: str, expected: str
) -> None:
    """App ids get the market prefix, deep links are sent as they are."""
    remote.send_launch_app_command(app_link_or_app_id)

    (sent,) = connected.sent()
    assert sent.remote_app_link_launch_request.app_link == expected


# --- forwarding to the protocol -----------------------------------------------------


async def test_send_key_command_forwards(remote: AndroidTVRemote, connected: RemoteHarness) -> None:
    """Key commands reach the protocol with the requested direction."""
    remote.send_key_command("POWER", "START_LONG")

    (sent,) = connected.sent()
    assert sent.remote_key_inject.key_code == RemoteKeyCode.KEYCODE_POWER
    assert sent.remote_key_inject.direction == RemoteDirection.START_LONG


async def test_send_text_forwards(remote: AndroidTVRemote, connected: RemoteHarness) -> None:
    """Text reaches the protocol as a batch edit."""
    remote.send_text("hello")

    (sent,) = connected.sent()
    assert sent.remote_ime_batch_edit.edit_info[0].text_field_status.value == "hello"


async def answer_voice_begin(harness: RemoteHarness, session_id: int) -> None:
    """Answer a pending start_voice the way the device would."""
    for _ in range(3):
        await asyncio.sleep(0)
    msg = RemoteMessage()
    msg.remote_voice_begin.session_id = session_id
    harness.receive(msg)


async def test_start_voice_returns_a_stream(remote: AndroidTVRemote, connected: RemoteHarness) -> None:
    """start_voice hands back a VoiceStream bound to the negotiated session."""
    task = asyncio.ensure_future(remote.start_voice(timeout=1.0))
    await answer_voice_begin(connected, session_id=99)
    stream = await task

    assert isinstance(stream, VoiceStream)
    assert stream.session_id == 99


# --- state and callbacks ------------------------------------------------------------


async def test_properties_are_none_before_connecting(remote: AndroidTVRemote) -> None:
    """Every device property reads as None until there is a connection."""
    assert remote.is_on is None
    assert remote.current_app is None
    assert remote.device_info is None
    assert remote.volume_info is None
    assert remote.is_voice_enabled is None


async def test_properties_follow_the_protocol(remote: AndroidTVRemote, connected: RemoteHarness) -> None:
    """The facade exposes whatever the protocol last saw."""
    msg = RemoteMessage()
    msg.remote_start.started = True
    connected.receive(msg)

    msg = RemoteMessage()
    msg.remote_ime_key_inject.app_info.app_package = "com.netflix.ninja"
    connected.receive(msg)

    assert remote.is_on is True
    assert remote.current_app == "com.netflix.ninja"
    assert remote.is_voice_enabled is True


@pytest.mark.parametrize(
    ("add", "remove", "notify", "value"),
    [
        ("add_is_on_updated_callback", "remove_is_on_updated_callback", "_on_is_on_updated", True),
        ("add_current_app_updated_callback", "remove_current_app_updated_callback", "_on_current_app_updated", "app"),
        (
            "add_volume_info_updated_callback",
            "remove_volume_info_updated_callback",
            "_on_volume_info_updated",
            {"level": 1, "max": 2, "muted": False},
        ),
        (
            "add_is_available_updated_callback",
            "remove_is_available_updated_callback",
            "_on_is_available_updated",
            False,
        ),
    ],
)
async def test_callbacks_are_notified_until_removed(
    remote: AndroidTVRemote, add: str, remove: str, notify: str, value: object
) -> None:
    """Every callback list fans out to all listeners and honours removal."""
    seen_first: list[object] = []
    seen_second: list[object] = []
    getattr(remote, add)(seen_first.append)
    getattr(remote, add)(seen_second.append)

    getattr(remote, notify)(value)
    assert seen_first == [value]
    assert seen_second == [value]

    getattr(remote, remove)(seen_first.append)
    getattr(remote, notify)(value)
    assert seen_first == [value]
    assert seen_second == [value, value]


async def test_removing_an_unknown_callback_raises(remote: AndroidTVRemote) -> None:
    """Removing a callback that was never added is an error, as documented."""
    with pytest.raises(ValueError, match="not in list"):
        remote.remove_is_on_updated_callback(lambda _value: None)


# --- disconnected behaviour ---------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("send_key_command", ("POWER",)),
        ("send_text", ("hello",)),
        ("send_launch_app_command", ("org.xbmc.kodi",)),
    ],
)
async def test_commands_after_disconnect_raise(remote: AndroidTVRemote, method: str, args: tuple[object, ...]) -> None:
    """Commands sent without a connection raise ConnectionClosed."""
    with pytest.raises(ConnectionClosed):
        getattr(remote, method)(*args)


async def test_start_voice_after_disconnect_raises(remote: AndroidTVRemote) -> None:
    """Starting voice without a connection raises ConnectionClosed."""
    with pytest.raises(ConnectionClosed):
        await remote.start_voice()


async def test_finish_pairing_after_disconnect_raises(remote: AndroidTVRemote) -> None:
    """Finishing pairing without a pairing connection raises ConnectionClosed."""
    with pytest.raises(ConnectionClosed):
        await remote.async_finish_pairing("ABCDEF")


async def test_disconnect_closes_the_protocol_and_stops_the_timer(remote: AndroidTVRemote, connected: RemoteHarness) -> None:
    """disconnect() closes the transport and cancels the idle disconnect task."""
    task = connected.protocol._idle_disconnect_task
    assert task is not None

    remote.disconnect()
    await asyncio.sleep(0)

    assert connected.transport.is_closing()
    assert task.cancelled()
    assert remote.is_on is None


async def test_disconnect_cancels_the_reconnect_task(remote: AndroidTVRemote, connected: RemoteHarness) -> None:
    """disconnect() stops the background reconnect loop."""
    remote.keep_reconnecting()
    await asyncio.sleep(0)
    reconnect_task = remote._reconnect_task
    assert reconnect_task is not None

    remote.disconnect()
    await asyncio.sleep(0)

    assert reconnect_task.cancelled() or reconnect_task.done()


async def test_disconnect_is_idempotent(remote: AndroidTVRemote, connected: RemoteHarness) -> None:
    """Disconnecting twice doesn't raise."""
    remote.disconnect()
    remote.disconnect()


# --- certificates -------------------------------------------------------------------


async def test_generate_cert_if_missing_creates_both_files(remote: AndroidTVRemote, tmp_path: Path) -> None:
    """A missing certificate is generated once and then reused."""
    assert await remote.async_generate_cert_if_missing() is True

    cert_pem = (tmp_path / "cert.pem").read_bytes()
    key_pem = (tmp_path / "key.pem").read_bytes()
    cert = x509.load_pem_x509_certificate(cert_pem)
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "pytest"
    assert serialization.load_pem_private_key(key_pem, password=None) is not None

    assert await remote.async_generate_cert_if_missing() is False
    assert (tmp_path / "cert.pem").read_bytes() == cert_pem


async def test_generating_a_new_cert_invalidates_the_cached_context(remote: AndroidTVRemote, tmp_path: Path) -> None:
    """A regenerated certificate isn't shadowed by an ssl context from the old one."""
    await remote.async_generate_cert_if_missing()
    first = await remote._create_ssl_context()
    assert await remote._create_ssl_context() is first

    (tmp_path / "cert.pem").unlink()
    (tmp_path / "key.pem").unlink()
    assert await remote.async_generate_cert_if_missing() is True

    assert remote._ssl_context is None
    assert await remote._create_ssl_context() is not first


async def test_missing_certificate_raises_invalid_auth(remote: AndroidTVRemote) -> None:
    """Connecting without a certificate is reported as an auth problem."""
    with pytest.raises(InvalidAuth):
        await remote._create_ssl_context()


# --- certificate subject parsing ----------------------------------------------------


def build_cert(
    client_cert_and_key: tuple[bytes, bytes], common_name: str | None, dn_qualifier: str | None = None
) -> x509.Certificate:
    """Build a certificate with the given subject, reusing the session key."""
    key = serialization.load_pem_private_key(client_cert_and_key[1], password=None)
    attributes = []
    if common_name is not None:
        attributes.append(x509.NameAttribute(NameOID.COMMON_NAME, common_name))
    if dn_qualifier is not None:
        attributes.append(x509.NameAttribute(NameOID.DN_QUALIFIER, dn_qualifier))
    name = x509.Name(attributes)
    now = datetime.now(timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())  # type: ignore[arg-type]
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("common_name", "dn_qualifier", "expected"),
    [
        # NVIDIA SHIELD
        (
            "atvremote/darcy/darcy/SHIELD Android TV/AA:BB:CC:DD:EE:FF",
            None,
            ("SHIELD Android TV", "AA:BB:CC:DD:EE:FF"),
        ),
        # Nexus Player
        (
            "atvremote/AA:BB:CC:DD:EE:FF",
            "fugu/fugu/Nexus Player",
            ("Nexus Player", "AA:BB:CC:DD:EE:FF"),
        ),
        # A subject without any "/" separator used to raise IndexError.
        ("atvremote", None, ("atvremote", "atvremote")),
    ],
)
def test_parse_name_and_mac(
    client_cert_and_key: tuple[bytes, bytes],
    common_name: str,
    dn_qualifier: str | None,
    expected: tuple[str, str],
) -> None:
    """The device name and MAC address are read out of the certificate subject."""
    cert = build_cert(client_cert_and_key, common_name, dn_qualifier)

    assert _parse_name_and_mac(cert) == expected


def test_parse_name_and_mac_without_a_common_name(client_cert_and_key: tuple[bytes, bytes]) -> None:
    """A certificate with no common name at all doesn't raise."""
    cert = build_cert(client_cert_and_key, None, "fugu/Nexus Player")

    assert _parse_name_and_mac(cert) == ("Nexus Player", "")


# --- voice through the facade -------------------------------------------------------


async def test_voice_round_trip_through_the_facade(remote: AndroidTVRemote, connected: RemoteHarness) -> None:
    """A full session: start, send audio, end, then start another one."""
    task = asyncio.ensure_future(remote.start_voice(timeout=1.0))
    await answer_voice_begin(connected, session_id=1)
    async with await task as stream:
        stream.send_chunk(b"a" * VOICE_CHUNK_MIN_SIZE)

    payloads = [msg for msg in connected.sent() if msg.HasField("remote_voice_payload")]
    assert len(payloads) == 1
    assert connected.sent()[-1].remote_voice_end.session_id == 1

    task = asyncio.ensure_future(remote.start_voice(timeout=1.0))
    await answer_voice_begin(connected, session_id=2)
    assert (await task).session_id == 2
