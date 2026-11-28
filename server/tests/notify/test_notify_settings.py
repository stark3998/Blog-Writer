"""Tests for blogwriter.notify.settings: BW_NOTIFY_* and VAPID key parsing."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pydantic import ValidationError

from blogwriter.notify import NotifyConfigError, NotifySettings, load_vapid_private_key
from blogwriter.notify.settings import (
    DEFAULT_PUSH_TIMEOUT_SECONDS,
    DEFAULT_PUSH_TTL_SECONDS,
    validate_vapid_subject,
)

SUBJECT = "mailto:owner@example.com"


def b64url(data: bytes, *, pad: bool = False) -> str:
    text = base64.urlsafe_b64encode(data).decode()
    return text if pad else text.rstrip("=")


def raw_private(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_numbers().private_value.to_bytes(32, "big")


@pytest.fixture
def vapid_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def settings(**values: object) -> NotifySettings:
    return NotifySettings.model_validate(values)


def test_defaults_mean_push_is_off() -> None:
    loaded = NotifySettings()
    assert not loaded.push_configured
    assert loaded.push_ttl_seconds == DEFAULT_PUSH_TTL_SECONDS == 43_200
    assert loaded.push_timeout_seconds == DEFAULT_PUSH_TIMEOUT_SECONDS == 10.0


def test_from_environment(
    monkeypatch: pytest.MonkeyPatch, vapid_key: ec.EllipticCurvePrivateKey
) -> None:
    monkeypatch.setenv("BW_NOTIFY_VAPID_PRIVATE_KEY", b64url(raw_private(vapid_key)))
    monkeypatch.setenv("BW_NOTIFY_VAPID_SUBJECT", SUBJECT)
    monkeypatch.setenv("BW_NOTIFY_PUSH_TTL_SECONDS", "900")
    monkeypatch.setenv("BW_NOTIFY_PUSH_TIMEOUT_SECONDS", "5")
    loaded = NotifySettings()
    assert loaded.push_configured
    assert loaded.vapid_subject == SUBJECT
    assert loaded.push_ttl_seconds == 900
    assert loaded.push_timeout_seconds == 5.0
    assert loaded.vapid_private_key is not None
    parsed = load_vapid_private_key(loaded.vapid_private_key.get_secret_value())
    assert parsed.private_numbers() == vapid_key.private_numbers()


def test_empty_environment_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_NOTIFY_VAPID_PRIVATE_KEY", "")
    monkeypatch.setenv("BW_NOTIFY_VAPID_SUBJECT", "")
    assert not NotifySettings().push_configured


@pytest.mark.parametrize(
    "subject",
    [
        "mailto:owner@example.com",
        "mailto:first.last+push@mail.example.co.uk",
        "https://writer.example.com",
    ],
)
def test_valid_subjects(subject: str) -> None:
    assert validate_vapid_subject(f"  {subject} ") == subject


@pytest.mark.parametrize(
    "subject",
    [
        "owner@example.com",
        "mailto:owner",
        "mailto:@example.com",
        "mailto:owner@localhost",
        "https://writer.example.com/",
        "https://writer.example.com/contact",
        "https://writer.example.com:8443",
        "http://writer.example.com",
        "",
    ],
)
def test_invalid_subjects(subject: str) -> None:
    with pytest.raises(ValueError, match="mailto"):
        validate_vapid_subject(subject)


def test_key_and_subject_go_together(vapid_key: ec.EllipticCurvePrivateKey) -> None:
    with pytest.raises(ValidationError, match="set together"):
        settings(vapid_private_key=b64url(raw_private(vapid_key)))
    with pytest.raises(ValidationError, match="set together"):
        settings(vapid_subject=SUBJECT)


def test_validation_errors_do_not_print_the_key(vapid_key: ec.EllipticCurvePrivateKey) -> None:
    secret = b64url(raw_private(vapid_key))
    with pytest.raises(ValidationError) as excinfo:
        settings(vapid_private_key=secret, vapid_subject="not a subject")
    assert secret not in str(excinfo.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("push_ttl_seconds", -1),
        ("push_ttl_seconds", 28 * 24 * 3600 + 1),
        ("push_timeout_seconds", 0),
        ("push_timeout_seconds", 61),
    ],
)
def test_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        settings(**{field: value})


def test_settings_repr_hides_the_key(vapid_key: ec.EllipticCurvePrivateKey) -> None:
    secret = b64url(raw_private(vapid_key))
    loaded = settings(vapid_private_key=secret, vapid_subject=SUBJECT)
    assert secret not in repr(loaded)


# ----------------------------------------------------------------------------------------------
# load_vapid_private_key
# ----------------------------------------------------------------------------------------------


def encodings(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "raw-b64url": b64url(raw_private(key)),
        "raw-b64url-padded": b64url(raw_private(key), pad=True),
        "raw-standard-b64": base64.b64encode(raw_private(key)).decode(),
        "der-b64url": b64url(der),
        "pem": pem,
        "pem-with-whitespace": f"\n  {pem}  \n",
    }


@pytest.mark.parametrize(
    "form",
    [
        "raw-b64url",
        "raw-b64url-padded",
        "raw-standard-b64",
        "der-b64url",
        "pem",
        "pem-with-whitespace",
    ],
)
def test_vapid_key_forms(form: str, vapid_key: ec.EllipticCurvePrivateKey) -> None:
    parsed = load_vapid_private_key(encodings(vapid_key)[form])
    assert parsed.private_numbers() == vapid_key.private_numbers()


def test_vapid_key_rejects_other_curves_and_algorithms() -> None:
    p384 = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(NotifyConfigError, match="P-256"):
        load_vapid_private_key(p384.decode())
    rsa_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(NotifyConfigError, match="P-256"):
        load_vapid_private_key(rsa_pem.decode())


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not base64 !!",
        b64url(b"\0" * 32),  # private value 0 is not a valid key
        b64url(b"\xff" * 32),  # larger than the curve order
        b64url(b"\x01" * 40),  # neither raw nor DER
        "-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----",
    ],
)
def test_vapid_key_rejects_garbage(value: str) -> None:
    with pytest.raises(NotifyConfigError) as excinfo:
        load_vapid_private_key(value)
    if value:
        assert value not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_encrypted_pem_is_refused(vapid_key: ec.EllipticCurvePrivateKey) -> None:
    pem = vapid_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"passphrase"),
    )
    with pytest.raises(NotifyConfigError):
        load_vapid_private_key(pem.decode())
