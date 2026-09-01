"""Tests for the self signed certificate generation."""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from androidtvremote2.certificate_generator import generate_selfsigned_cert


def test_certificate_matches_the_hostname(client_cert_and_key: tuple[bytes, bytes]) -> None:
    """The common name and the subject alternative name both carry the client name."""
    cert = x509.load_pem_x509_certificate(client_cert_and_key[0])

    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "client"
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert san.value.get_values_for_type(x509.DNSName) == ["client"]
    assert cert.issuer == cert.subject


def test_key_is_a_usable_rsa_2048_key(client_cert_and_key: tuple[bytes, bytes]) -> None:
    """The key is an unencrypted RSA 2048 key matching the certificate."""
    cert_pem, key_pem = client_cert_and_key
    key = serialization.load_pem_private_key(key_pem, password=None)

    assert isinstance(key, rsa.RSAPrivateKey)
    assert key.key_size == 2048
    cert = x509.load_pem_x509_certificate(cert_pem)
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()  # type: ignore[union-attr]


def test_certificate_is_valid_for_about_ten_years(client_cert_and_key: tuple[bytes, bytes]) -> None:
    """The certificate is valid now and for roughly the next ten years."""
    cert = x509.load_pem_x509_certificate(client_cert_and_key[0])
    now = datetime.now(timezone.utc)

    assert cert.not_valid_before_utc <= now <= cert.not_valid_after_utc
    assert cert.not_valid_after_utc - now > timedelta(days=10 * 365 - 1)


def test_modulus_hex_has_an_even_number_of_digits(client_cert_and_key: tuple[bytes, bytes]) -> None:
    """The pairing hash formats the modulus as hex, which needs a whole number of bytes."""
    cert = x509.load_pem_x509_certificate(client_cert_and_key[0])
    modulus = cert.public_key().public_numbers().n  # type: ignore[union-attr]

    assert len(f"{modulus:X}") == 512


def test_generation_does_not_emit_deprecation_warnings() -> None:
    """Regression test: datetime.utcnow() is deprecated from Python 3.12 on."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        generate_selfsigned_cert("client")

    assert [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)] == []


def test_each_call_generates_a_new_key() -> None:
    """Two clients don't end up sharing a key."""
    first, _ = generate_selfsigned_cert("client")
    second, _ = generate_selfsigned_cert("client")

    assert first != second
