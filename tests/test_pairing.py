"""Tests for PairingProtocol, the pairing protocol with an Android TV."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from androidtvremote2.exceptions import ConnectionClosed, InvalidAuth
from androidtvremote2.pairing import PairingProtocol
from androidtvremote2.polo_pb2 import Options, OuterMessage

from .conftest import FakeTransport, frame, parse_written, polo_message


class PairingHarness:
    """A PairingProtocol wired up to a FakeTransport that fakes the peer certificate."""

    def __init__(self, certfile: str, server_cert_pem: bytes) -> None:
        """Initialize and connect the protocol."""
        loop = asyncio.get_running_loop()
        self.protocol = PairingProtocol(
            on_con_lost=loop.create_future(),
            client_name="pytest",
            certfile=certfile,
            loop=loop,
        )
        server_der = x509.load_pem_x509_certificate(server_cert_pem).public_bytes(serialization.Encoding.DER)
        self.transport = FakeTransport({"ssl_object": SimpleNamespace(getpeercert=lambda _binary: server_der)})
        self.protocol.connection_made(self.transport)

    def receive(self, *msgs: OuterMessage) -> None:
        """Deliver messages to the protocol as the device would."""
        self.protocol.data_received(b"".join(frame(msg) for msg in msgs))

    def sent(self) -> list[OuterMessage]:
        """Return every message written to the transport so far."""
        return parse_written(self.transport.written, OuterMessage)  # type: ignore[return-value]

    def clear(self) -> None:
        """Forget everything written so far."""
        self.transport.written.clear()


@pytest.fixture
async def pairing(certfile: str, server_cert_and_key: tuple[bytes, bytes]) -> PairingHarness:
    """Return a connected PairingHarness."""
    return PairingHarness(certfile, server_cert_and_key[0])


def device_msg(field: str, **fields: object) -> OuterMessage:
    """Build an OuterMessage carrying the given sub message."""
    msg = polo_message()
    sub = getattr(msg, field)
    for name, value in fields.items():
        setattr(sub, name, value)
    if not fields:
        sub.SetInParent()
    return msg


def expected_pin(client_cert_pem: bytes, server_cert_pem: bytes, nonce: str) -> str:
    """Compute the 6 hex digit code the Android TV would show for a given nonce."""

    def modulus_and_exponent(pem: bytes) -> tuple[int, int]:
        numbers = x509.load_pem_x509_certificate(pem).public_key().public_numbers()  # type: ignore[union-attr]
        return numbers.n, numbers.e  # type: ignore[union-attr]

    client_n, client_e = modulus_and_exponent(client_cert_pem)
    server_n, server_e = modulus_and_exponent(server_cert_pem)
    h = hashlib.sha256()
    h.update(bytes.fromhex(f"{client_n:X}"))
    h.update(bytes.fromhex(f"0{client_e:X}"))
    h.update(bytes.fromhex(f"{server_n:X}"))
    h.update(bytes.fromhex(f"0{server_e:X}"))
    h.update(bytes.fromhex(nonce))
    return f"{h.digest()[0]:02X}{nonce}"


async def wait_until_sent(pairing: PairingHarness, count: int, timeout: float = 5.0) -> None:
    """Wait until the protocol has written `count` messages.

    Some steps read the certificate from disk in a thread, so yielding to the loop a
    fixed number of times isn't enough.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while len(pairing.sent()) < count:
        assert loop.time() < deadline, f"only {len(pairing.sent())} of {count} messages were sent"
        await asyncio.sleep(0.001)


async def run_start_pairing(pairing: PairingHarness) -> None:
    """Drive the full pairing handshake the way the device would answer it."""
    task = asyncio.ensure_future(pairing.protocol.async_start_pairing())
    await wait_until_sent(pairing, 1)
    pairing.receive(device_msg("pairing_request_ack"))
    await wait_until_sent(pairing, 2)
    pairing.receive(device_msg("options"))
    await wait_until_sent(pairing, 3)
    pairing.receive(device_msg("configuration_ack"))
    await task


# --- the handshake ------------------------------------------------------------------


async def test_start_pairing_completes_the_handshake(pairing: PairingHarness) -> None:
    """Each step of the handshake is answered with the expected message."""
    await run_start_pairing(pairing)

    request, options, configuration = pairing.sent()
    assert request.pairing_request.client_name == "pytest"
    assert request.pairing_request.service_name == "atvremote"
    assert request.protocol_version == 2
    assert request.status == OuterMessage.Status.STATUS_OK

    assert options.options.preferred_role == Options.RoleType.ROLE_TYPE_INPUT
    assert options.options.input_encodings[0].type == Options.Encoding.ENCODING_TYPE_HEXADECIMAL
    assert options.options.input_encodings[0].symbol_length == 6

    assert configuration.configuration.client_role == Options.RoleType.ROLE_TYPE_INPUT
    assert configuration.configuration.encoding.type == Options.Encoding.ENCODING_TYPE_HEXADECIMAL
    assert configuration.configuration.encoding.symbol_length == 6


async def test_finish_pairing_sends_the_secret(
    pairing: PairingHarness, client_cert_and_key: tuple[bytes, bytes], server_cert_and_key: tuple[bytes, bytes]
) -> None:
    """A valid pairing code produces the secret the device expects."""
    await run_start_pairing(pairing)
    pairing.clear()
    pin = expected_pin(client_cert_and_key[0], server_cert_and_key[0], "ABCD")

    task = asyncio.ensure_future(pairing.protocol.async_finish_pairing(pin))
    await wait_until_sent(pairing, 1)
    pairing.receive(device_msg("secret_ack", secret=b"\x00" * 32))
    await task

    (secret,) = pairing.sent()
    assert len(secret.secret.secret) == 32
    assert secret.secret.secret[0] == int(pin[0:2], 16)


async def test_duplicate_acks_do_not_raise(pairing: PairingHarness) -> None:
    """A repeated acknowledgement doesn't blow up inside data_received.

    Regression test: set_result was called without checking whether the future was
    already done, raising InvalidStateError out of the protocol callback.
    """
    task = asyncio.ensure_future(pairing.protocol.async_start_pairing())
    await wait_until_sent(pairing, 1)
    pairing.receive(device_msg("pairing_request_ack"))
    await wait_until_sent(pairing, 2)
    pairing.receive(device_msg("options"))
    await wait_until_sent(pairing, 3)
    pairing.receive(device_msg("configuration_ack"), device_msg("configuration_ack"))
    await task
    # A late duplicate after the future was cleared is fine too.
    pairing.receive(device_msg("configuration_ack"))


# --- pairing code validation --------------------------------------------------------


@pytest.mark.parametrize("pairing_code", ["", "12345", "1234567"])
async def test_pairing_code_must_be_six_characters(pairing: PairingHarness, pairing_code: str) -> None:
    """A code of the wrong length is rejected before anything is sent."""
    with pytest.raises(InvalidAuth, match="exactly 6"):
        await pairing.protocol.async_finish_pairing(pairing_code)
    assert pairing.sent() == []


async def test_pairing_code_must_be_hex(pairing: PairingHarness) -> None:
    """A non hexadecimal code is rejected before anything is sent."""
    with pytest.raises(InvalidAuth, match="hex"):
        await pairing.protocol.async_finish_pairing("ABCDEZ")
    assert pairing.sent() == []


async def test_wrong_pairing_code_is_rejected_locally(
    pairing: PairingHarness, client_cert_and_key: tuple[bytes, bytes], server_cert_and_key: tuple[bytes, bytes]
) -> None:
    """A mistyped code fails the local hash check instead of being sent."""
    pin = expected_pin(client_cert_and_key[0], server_cert_and_key[0], "ABCD")
    wrong = f"{(int(pin[0:2], 16) + 1) % 256:02X}ABCD"

    with pytest.raises(InvalidAuth, match="Unexpected hash"):
        await pairing.protocol.async_finish_pairing(wrong)
    assert pairing.sent() == []


# --- failure handling ---------------------------------------------------------------


async def test_pairing_requires_a_connection(pairing: PairingHarness) -> None:
    """Pairing on a closed transport raises ConnectionClosed."""
    pairing.transport.close()
    with pytest.raises(ConnectionClosed):
        await pairing.protocol.async_start_pairing()
    with pytest.raises(ConnectionClosed):
        await pairing.protocol.async_finish_pairing("ABCDEF")


async def test_start_pairing_times_out(pairing: PairingHarness, caplog: pytest.LogCaptureFixture) -> None:
    """A device that stops responding doesn't hang the caller forever.

    Regression test: the wait had no timeout, so pairing hung indefinitely when the
    device accepted the connection and then went silent.
    """
    with caplog.at_level(logging.DEBUG, logger="androidtvremote2"), pytest.raises(ConnectionClosed, match=r"0\.05 seconds"):
        await pairing.protocol._async_wait_for_future_or_con_lost(asyncio.get_running_loop().create_future(), timeout=0.05)

    assert "Timeout after 0.05 seconds" in caplog.text
    assert pairing.transport.is_closing()


async def test_connection_lost_while_pairing(pairing: PairingHarness) -> None:
    """A dropped connection ends the wait with ConnectionClosed."""
    task = asyncio.ensure_future(pairing.protocol.async_start_pairing())
    await wait_until_sent(pairing, 1)

    pairing.transport.close()
    pairing.protocol.connection_lost(None)

    with pytest.raises(ConnectionClosed):
        await task


async def test_error_status_aborts_pairing(pairing: PairingHarness) -> None:
    """A non OK status from the device fails the pending operation."""
    task = asyncio.ensure_future(pairing.protocol.async_start_pairing())
    await wait_until_sent(pairing, 1)

    msg = OuterMessage()
    msg.protocol_version = 2
    msg.status = OuterMessage.Status.STATUS_BAD_CONFIGURATION
    pairing.receive(msg)

    with pytest.raises(ConnectionClosed):
        await task
    assert pairing.transport.is_closing()


async def test_unhandled_message_aborts_pairing(pairing: PairingHarness) -> None:
    """A message that isn't part of the handshake fails the pending operation."""
    task = asyncio.ensure_future(pairing.protocol.async_start_pairing())
    await wait_until_sent(pairing, 1)

    # A message that isn't part of the handshake this library drives.
    pairing.receive(device_msg("pairing_request", service_name="atvremote"))

    with pytest.raises(ConnectionClosed):
        await task
    assert pairing.transport.is_closing()


async def test_undecodable_message_aborts_pairing(pairing: PairingHarness) -> None:
    """Garbage on the wire fails the pending operation instead of being ignored."""
    task = asyncio.ensure_future(pairing.protocol.async_start_pairing())
    await wait_until_sent(pairing, 1)

    pairing.protocol.data_received(b"\x03\xff\xff\xff")

    with pytest.raises(ConnectionClosed):
        await task
