"""Tests for blogwriter.core.crypto: the AES-GCM token vault and its key-ring settings."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from blogwriter.core.crypto import (
    KEY_BYTES,
    NONCE_BYTES,
    TAG_BYTES,
    TokenVault,
    VaultError,
    VaultSettings,
    generate_key,
    key_id,
    parse_key_ring,
)

KEY_A = bytes(range(32))
KEY_B = bytes(range(100, 132))
DOC_ID = "linkedin-token-owner"
TOKEN_RE = re.compile(r"v1\.([A-Za-z0-9_-]{1,32})\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)")


def b64(data: bytes, *, urlsafe: bool = False, pad: bool = True) -> str:
    text = (base64.urlsafe_b64encode(data) if urlsafe else base64.b64encode(data)).decode()
    return text if pad else text.rstrip("=")


def b64url(data: bytes) -> str:
    return b64(data, urlsafe=True, pad=False)


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@pytest.fixture
def vault() -> TokenVault:
    return TokenVault({"k1": KEY_A, "k2": KEY_B}, primary="k2")


def settings(**values: str) -> VaultSettings:
    return VaultSettings.model_validate(values)


# ----------------------------------------------------------------------------------------------
# Round trip and format
# ----------------------------------------------------------------------------------------------


def test_round_trip(vault: TokenVault) -> None:
    token = vault.encrypt("AQX-linkedin-access-token", context=DOC_ID)
    assert vault.decrypt(token, context=DOC_ID) == "AQX-linkedin-access-token"


def test_round_trip_unicode_and_empty_plaintext(vault: TokenVault) -> None:
    for plaintext in ("", "naïve ✓ 東京", "x" * 5000):
        assert vault.decrypt(vault.encrypt(plaintext, context=DOC_ID), context=DOC_ID) == plaintext


def test_token_format(vault: TokenVault) -> None:
    token = vault.encrypt("secret", context=DOC_ID)
    match = TOKEN_RE.fullmatch(token)
    assert match is not None
    kid, nonce, sealed = match.groups()
    assert kid == "k2"
    assert len(unb64url(nonce)) == NONCE_BYTES
    assert len(unb64url(sealed)) == len(b"secret") + TAG_BYTES
    assert "=" not in token
    assert "secret" not in token


def test_nonce_is_random_per_encryption(vault: TokenVault) -> None:
    tokens = {vault.encrypt("same", context=DOC_ID) for _ in range(20)}
    assert len(tokens) == 20
    nonces = {token.split(".")[2] for token in tokens}
    assert len(nonces) == 20


def test_new_tokens_use_the_primary_kid(vault: TokenVault) -> None:
    assert key_id(vault.encrypt("x", context=DOC_ID)) == "k2"
    assert vault.primary_kid == "k2"
    assert vault.kids == frozenset({"k1", "k2"})


# ----------------------------------------------------------------------------------------------
# Decrypt failures
# ----------------------------------------------------------------------------------------------


def test_wrong_context_fails(vault: TokenVault) -> None:
    token = vault.encrypt("secret", context=DOC_ID)
    with pytest.raises(VaultError) as excinfo:
        vault.decrypt(token, context="another-document")
    assert excinfo.value.reason == "decrypt_failed"


def test_unknown_kid_fails() -> None:
    old = TokenVault({"retired": KEY_A}, primary="retired")
    token = old.encrypt("secret", context=DOC_ID)
    current = TokenVault({"k2": KEY_B}, primary="k2")
    with pytest.raises(VaultError) as excinfo:
        current.decrypt(token, context=DOC_ID)
    assert excinfo.value.reason == "unknown_kid"


def _flip(text: str, index: int) -> str:
    data = bytearray(unb64url(text))
    data[index] ^= 0x01
    return b64url(bytes(data))


@pytest.mark.parametrize(
    ("part", "byte_index"),
    [
        (2, 0),  # nonce
        (2, NONCE_BYTES - 1),
        (3, 0),  # ciphertext
        (3, -1),  # tag
        (3, -TAG_BYTES),
    ],
)
def test_tampering_fails(vault: TokenVault, part: int, byte_index: int) -> None:
    parts = vault.encrypt("secret-token", context=DOC_ID).split(".")
    parts[part] = _flip(parts[part], byte_index)
    with pytest.raises(VaultError) as excinfo:
        vault.decrypt(".".join(parts), context=DOC_ID)
    assert excinfo.value.reason == "decrypt_failed"


def test_truncated_ciphertext_fails(vault: TokenVault) -> None:
    parts = vault.encrypt("secret-token", context=DOC_ID).split(".")
    parts[3] = b64url(unb64url(parts[3])[:-1])
    with pytest.raises(VaultError) as excinfo:
        vault.decrypt(".".join(parts), context=DOC_ID)
    assert excinfo.value.reason == "decrypt_failed"


def test_relabelled_kid_fails_with_another_key(vault: TokenVault) -> None:
    parts = vault.encrypt("secret", context=DOC_ID).split(".")
    parts[1] = "k1"
    with pytest.raises(VaultError) as excinfo:
        vault.decrypt(".".join(parts), context=DOC_ID)
    assert excinfo.value.reason == "decrypt_failed"


def test_relabelled_kid_fails_even_with_the_same_key() -> None:
    # The header is part of the associated data, so a kid alias cannot be swapped in.
    vault = TokenVault({"a": KEY_A, "b": KEY_A}, primary="a")
    parts = vault.encrypt("secret", context=DOC_ID).split(".")
    parts[1] = "b"
    with pytest.raises(VaultError) as excinfo:
        vault.decrypt(".".join(parts), context=DOC_ID)
    assert excinfo.value.reason == "decrypt_failed"


def test_wrong_key_under_the_same_kid_fails() -> None:
    token = TokenVault({"k1": KEY_A}, primary="k1").encrypt("secret", context=DOC_ID)
    with pytest.raises(VaultError) as excinfo:
        TokenVault({"k1": KEY_B}, primary="k1").decrypt(token, context=DOC_ID)
    assert excinfo.value.reason == "decrypt_failed"


def _valid_parts(vault: TokenVault) -> list[str]:
    return vault.encrypt("secret", context=DOC_ID).split(".")


BAD_FORMATS: dict[str, Callable[[list[str]], str]] = {
    "empty": lambda p: "",
    "three-parts": lambda p: ".".join(p[:3]),
    "five-parts": lambda p: ".".join([*p, "x"]),
    "unknown-version": lambda p: ".".join(["v2", *p[1:]]),
    "version-case": lambda p: ".".join(["V1", *p[1:]]),
    "empty-kid": lambda p: ".".join([p[0], "", p[2], p[3]]),
    "bad-kid-char": lambda p: ".".join([p[0], "k:2", p[2], p[3]]),
    "long-kid": lambda p: ".".join([p[0], "k" * 33, p[2], p[3]]),
    "padded-nonce": lambda p: ".".join([p[0], p[1], p[2] + "==", p[3]]),
    "padded-ct": lambda p: ".".join([p[0], p[1], p[2], p[3] + "="]),
    "bad-alphabet": lambda p: ".".join([p[0], p[1], p[2], p[3] + "!"]),
    "standard-alphabet": lambda p: ".".join([p[0], p[1], p[2][:-1] + "+", p[3]]),
    "short-nonce": lambda p: ".".join([p[0], p[1], b64url(b"\x00" * 11), p[3]]),
    "long-nonce": lambda p: ".".join([p[0], p[1], b64url(b"\x00" * 13), p[3]]),
    "short-ct": lambda p: ".".join([p[0], p[1], p[2], b64url(b"\x00" * 15)]),
    "impossible-length": lambda p: ".".join([p[0], p[1], p[2], "A"]),
    "trailing-space": lambda p: ".".join(p) + " ",
    "too-long": lambda p: ".".join([p[0], p[1], p[2], "x" * 70_000]),
}


@pytest.mark.parametrize("case", sorted(BAD_FORMATS))
def test_bad_format_fails(vault: TokenVault, case: str) -> None:
    token = BAD_FORMATS[case](_valid_parts(vault))
    with pytest.raises(VaultError) as excinfo:
        vault.decrypt(token, context=DOC_ID)
    assert excinfo.value.reason == "bad_format"


def test_non_canonical_base64_is_rejected(vault: TokenVault) -> None:
    parts = _valid_parts(vault)
    # 12-byte nonce -> 16 chars with no spare bits; use the tag part, whose length leaves spare
    # bits whenever len % 3 != 0. Find a ciphertext whose last char carries spare bits.
    sealed = unb64url(parts[3])
    assert len(sealed) % 3 != 0
    last = parts[3][-1]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    sibling = alphabet[alphabet.index(last) ^ 1]
    tampered = parts[3][:-1] + sibling
    assert unb64url(tampered) == sealed  # decodes to the same bytes...
    parts[3] = tampered
    with pytest.raises(VaultError) as excinfo:  # ...but is not the canonical encoding
        vault.decrypt(".".join(parts), context=DOC_ID)
    assert excinfo.value.reason == "bad_format"


def test_error_messages_carry_no_secrets(vault: TokenVault) -> None:
    token = vault.encrypt("SECRET-PLAINTEXT", context=DOC_ID)
    for bad in (token[:-2], token.replace("v1", "v9")):
        with pytest.raises(VaultError) as excinfo:
            vault.decrypt(bad, context=DOC_ID)
        message = str(excinfo.value)
        assert "SECRET" not in message
        assert token.split(".")[3][:10] not in message


def test_empty_or_huge_context_is_a_programming_error(vault: TokenVault) -> None:
    with pytest.raises(ValueError, match="context"):
        vault.encrypt("x", context="")
    token = vault.encrypt("x", context=DOC_ID)
    with pytest.raises(ValueError, match="context"):
        vault.decrypt(token, context="")
    with pytest.raises(ValueError, match="context"):
        vault.encrypt("x", context="d" * 1025)


# ----------------------------------------------------------------------------------------------
# Rotation
# ----------------------------------------------------------------------------------------------


def test_rotation_keeps_old_tokens_readable() -> None:
    before = TokenVault({"k1": KEY_A}, primary="k1")
    old_token = before.encrypt("token", context=DOC_ID)
    after = TokenVault({"k1": KEY_A, "k2": KEY_B}, primary="k2")
    assert after.decrypt(old_token, context=DOC_ID) == "token"
    assert after.needs_rotation(old_token)
    rotated = after.reencrypt(old_token, context=DOC_ID)
    assert key_id(rotated) == "k2"
    assert not after.needs_rotation(rotated)
    assert after.decrypt(rotated, context=DOC_ID) == "token"


def test_reencrypt_checks_the_context(vault: TokenVault) -> None:
    token = TokenVault({"k1": KEY_A}, primary="k1").encrypt("token", context=DOC_ID)
    with pytest.raises(VaultError):
        vault.reencrypt(token, context="other")


def test_key_id_rejects_bad_tokens() -> None:
    with pytest.raises(VaultError) as excinfo:
        key_id("not-a-token")
    assert excinfo.value.reason == "bad_format"


# ----------------------------------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------------------------------


def test_constructor_validates_the_ring() -> None:
    with pytest.raises(ValueError, match="empty"):
        TokenVault({}, primary="k1")
    with pytest.raises(ValueError, match="32 bytes"):
        TokenVault({"k1": b"short"}, primary="k1")
    with pytest.raises(ValueError, match="kid"):
        TokenVault({"k.1": KEY_A}, primary="k.1")
    with pytest.raises(ValueError, match="primary"):
        TokenVault({"k1": KEY_A}, primary="k2")


def test_repr_shows_kids_only(vault: TokenVault) -> None:
    text = repr(vault)
    assert text == "TokenVault(primary='k2', kids=['k1', 'k2'])"
    assert b64(KEY_A) not in text


def test_generate_key_is_random_and_parseable() -> None:
    first, second = generate_key(), generate_key()
    assert first != second
    ring = parse_key_ring(f"a:{first},b:{second}")
    assert len(ring["a"]) == KEY_BYTES
    assert ring["a"] != ring["b"]


# ----------------------------------------------------------------------------------------------
# Key-ring parsing and settings
# ----------------------------------------------------------------------------------------------


def test_parse_key_ring_accepts_both_alphabets_and_optional_padding() -> None:
    key = os.urandom(32)
    while b64(key) == b64(key, urlsafe=True):  # make sure the alphabets differ for this key
        key = os.urandom(32)
    spec = f" a:{b64(key)} , b:{b64(key, urlsafe=True, pad=False)},c:{b64(KEY_B, pad=False)}"
    assert parse_key_ring(spec) == {"a": key, "b": key, "c": KEY_B}


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("", "entry 1"),
        ("k1", "entry 1"),
        (f"k1:{b64(KEY_A)},", "entry 2"),
        (f":{b64(KEY_A)}", "entry 1"),
        (f"k.1:{b64(KEY_A)}", "entry 1"),
        (f"{'k' * 33}:{b64(KEY_A)}", "entry 1"),
        ("k1:not base64!", "not valid base64"),
        (f"k1:{b64(KEY_A[:16])}", "32 bytes"),
        (f"k1:{b64(KEY_A + b'x')}", "32 bytes"),
        (f"k1:{b64(b'A' * 32)}", "placeholder"),
        (f"k1:{b64(KEY_A)},k1:{b64(KEY_B)}", "more than once"),
    ],
)
def test_parse_key_ring_errors(spec: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_key_ring(spec)


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_VAULT_KEYS", f"k1:{b64(KEY_A)},k2:{b64url(KEY_B)}")
    monkeypatch.setenv("BW_VAULT_PRIMARY", "k2")
    loaded = VaultSettings()
    assert loaded.configured
    vault = TokenVault.from_settings(loaded)
    assert vault.primary_kid == "k2"
    assert vault.kids == frozenset({"k1", "k2"})
    legacy = TokenVault({"k1": KEY_A}, primary="k1").encrypt("t", context=DOC_ID)
    assert vault.decrypt(legacy, context=DOC_ID) == "t"


def test_from_settings_reads_the_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_VAULT_KEYS", f"only:{b64(KEY_A)}")
    vault = TokenVault.from_settings()
    assert vault.primary_kid == "only"


def test_single_key_needs_no_primary() -> None:
    vault = TokenVault.from_settings(settings(keys=f"solo:{b64(KEY_A)}"))
    assert vault.primary_kid == "solo"


def test_unconfigured_vault_fails_closed() -> None:
    empty = settings()
    assert not empty.configured
    with pytest.raises(VaultError) as excinfo:
        TokenVault.from_settings(empty)
    assert excinfo.value.reason == "not_configured"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"keys": f"k1:{b64(KEY_A)},k2:{b64(KEY_B)}"}, "BW_VAULT_PRIMARY is required"),
        ({"keys": f"k1:{b64(KEY_A)}", "primary": "k9"}, "must name a kid"),
        ({"keys": f"k1:{b64(KEY_A[:31])}"}, "32 bytes"),
        ({"keys": f"k1:{b64(KEY_A)},k1:{b64(KEY_B)}", "primary": "k1"}, "more than once"),
        ({"keys": "garbage"}, "entry 1"),
    ],
)
def test_misconfigured_ring_fails_at_build(values: dict[str, str], message: str) -> None:
    with pytest.raises(VaultError, match=message) as excinfo:
        TokenVault.from_settings(settings(**values))
    assert excinfo.value.reason == "misconfigured"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"primary": "k1"}, "BW_VAULT_KEYS is not"),
        ({"keys": f"k1:{b64(KEY_A)}", "primary": "bad kid"}, "1-32"),
    ],
)
def test_settings_errors(values: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        settings(**values)


def test_configuration_errors_never_echo_key_material() -> None:
    secret_key = b64(os.urandom(31))  # wrong length, so the ring is rejected
    loaded = settings(keys=f"k1:{secret_key}")  # loads: key material is not checked by pydantic
    with pytest.raises(VaultError) as excinfo:
        TokenVault.from_settings(loaded)
    assert excinfo.value.reason == "misconfigured"
    assert secret_key[:12] not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_settings_validation_errors_never_echo_key_material() -> None:
    secret_key = b64(os.urandom(32))
    with pytest.raises(ValidationError) as excinfo:
        settings(keys=f"k1:{secret_key}", primary="bad kid")
    assert secret_key[:12] not in str(excinfo.value)


def test_settings_repr_hides_keys() -> None:
    loaded = settings(keys=f"k1:{b64(KEY_A)}")
    assert b64(KEY_A) not in repr(loaded)
    assert b64(KEY_A) not in str(loaded.model_dump())


def test_empty_environment_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_VAULT_KEYS", "")
    monkeypatch.setenv("BW_VAULT_PRIMARY", "")
    assert not VaultSettings().configured
