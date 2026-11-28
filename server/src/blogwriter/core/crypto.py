"""Token vault: AES-256-GCM under a key ring, for secrets stored in Cosmos (the LinkedIn token).

Ciphertext format (ASCII, safe to store in a JSON document)::

    v1.<kid>.<nonce_b64url>.<ct_b64url>

* ``kid`` names the key in the ring: 1-32 characters of ``[A-Za-z0-9_-]``.
* ``nonce`` is 12 random bytes. ``ct`` is the AES-GCM ciphertext followed by its 16-byte tag.
  Both are unpadded base64url and must be in canonical form.
* The associated data binds the caller's ``context`` (for example the document id) **and** the
  header: ``b"v1." + kid + b"." + context``. A token copied into another document, relabelled
  with another kid or changed in any other way fails to decrypt.

Keys come from :class:`VaultSettings`:

``BW_VAULT_KEYS``
    Comma-separated ``kid:base64key`` pairs, for example ``2026-11:Zm9v...,2027-05:YmFy...``.
    Each key is 32 random bytes in base64 or base64url, padding optional. Generate one with
    :func:`generate_key`.
``BW_VAULT_PRIMARY``
    The kid new tokens are encrypted with. Optional when the ring holds a single key.

Rotation: add a new kid, make it the primary and redeploy. Tokens under the old kid still
decrypt; :meth:`TokenVault.needs_rotation` flags them and :meth:`TokenVault.reencrypt` moves them
to the primary key (or the owner simply reconnects). Remove the old kid once nothing uses it.

Every failure raises :class:`VaultError` with a stable :attr:`VaultError.reason`. Messages never
contain key material, plaintext or ciphertext. The key ring is parsed by
:meth:`TokenVault.from_settings`, not by pydantic, because pydantic keeps the raw input in
``ValidationError.errors()``; call it at startup so a bad ring stops the boot. Payloads are small
(OAuth tokens), so the calls are synchronous: AES-GCM on a few hundred bytes takes microseconds
and does not stall the event loop.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from collections.abc import Mapping
from typing import Final, Literal, Self

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "KEY_BYTES",
    "NONCE_BYTES",
    "TAG_BYTES",
    "VERSION",
    "TokenVault",
    "VaultError",
    "VaultErrorReason",
    "VaultSettings",
    "generate_key",
    "key_id",
    "parse_key_ring",
]

#: Format version, the first part of every token.
VERSION: Final = "v1"
#: AES-256 key length.
KEY_BYTES: Final = 32
#: AES-GCM nonce length (96 bits, random per encryption).
NONCE_BYTES: Final = 12
#: AES-GCM tag length, appended to the ciphertext.
TAG_BYTES: Final = 16
#: Longest token :meth:`TokenVault.decrypt` will parse.
MAX_TOKEN_CHARS: Final = 65_536
#: Longest associated-data context, in UTF-8 bytes.
MAX_CONTEXT_BYTES: Final = 1024

_KID_RE: Final = re.compile(r"[A-Za-z0-9_-]{1,32}")
_B64URL_RE: Final = re.compile(r"[A-Za-z0-9_-]*")

type VaultErrorReason = Literal[
    "not_configured", "misconfigured", "bad_format", "unknown_kid", "decrypt_failed"
]

_MESSAGES: Final[dict[VaultErrorReason, str]] = {
    "not_configured": "the token vault has no keys (set BW_VAULT_KEYS)",
    "misconfigured": "the token vault key ring is invalid",
    "bad_format": "the token is not a v1 vault token",
    "unknown_kid": "the token was encrypted with a key that is not in the key ring",
    "decrypt_failed": "the token could not be decrypted (tampered, wrong context or wrong key)",
}


class VaultError(Exception):
    """A vault operation failed. :attr:`reason` is stable; the message never holds secrets.

    ``detail`` (configuration errors only) names an entry or a kid, never key material.
    """

    def __init__(self, reason: VaultErrorReason, detail: str | None = None) -> None:
        message = _MESSAGES[reason] if detail is None else f"{_MESSAGES[reason]}: {detail}"
        super().__init__(message)
        self.reason: VaultErrorReason = reason


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode_canonical(text: str) -> bytes | None:
    """Decode unpadded base64url, or ``None`` unless ``text`` is the canonical encoding."""
    if not _B64URL_RE.fullmatch(text) or len(text) % 4 == 1:
        return None
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    # Re-encoding rejects non-zero trailing bits, so every byte string has exactly one token.
    return data if _b64url_encode(data) == text else None


def generate_key() -> str:
    """A new random 32-byte key in unpadded base64url, for a ``BW_VAULT_KEYS`` entry."""
    return _b64url_encode(os.urandom(KEY_BYTES))


def _decode_key(encoded: str, kid: str) -> bytes:
    text = encoded.replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    try:
        key = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"key {kid!r} is not valid base64") from None
    if len(key) != KEY_BYTES:
        raise ValueError(f"key {kid!r} must decode to {KEY_BYTES} bytes")
    if len(set(key)) == 1:
        raise ValueError(f"key {kid!r} is a placeholder (every byte is the same)")
    return key


def parse_key_ring(spec: str) -> dict[str, bytes]:
    """Parse ``kid:base64key[,kid:base64key...]`` into ``{kid: key}``.

    Raises ``ValueError`` naming the offending entry or kid, never the key material.
    """
    ring: dict[str, bytes] = {}
    for index, raw_entry in enumerate(spec.split(","), start=1):
        kid, separator, encoded = raw_entry.strip().partition(":")
        kid = kid.strip()
        if not separator or not _KID_RE.fullmatch(kid):
            raise ValueError(
                f"entry {index} must be 'kid:base64key' with a kid of 1-32 [A-Za-z0-9_-]"
            )
        if kid in ring:
            raise ValueError(f"kid {kid!r} appears more than once")
        ring[kid] = _decode_key(encoded.strip(), kid)
    return ring


def _context_bytes(context: str) -> bytes:
    data = context.encode("utf-8")
    if not data:
        raise ValueError("context must not be empty; pass the id of the document holding the token")
    if len(data) > MAX_CONTEXT_BYTES:
        raise ValueError(f"context must be at most {MAX_CONTEXT_BYTES} bytes")
    return data


def _associated_data(kid: str, context: bytes) -> bytes:
    return f"{VERSION}.{kid}.".encode("ascii") + context


def _split(token: str) -> tuple[str, bytes, bytes]:
    if len(token) > MAX_TOKEN_CHARS:
        raise VaultError("bad_format")
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != VERSION or not _KID_RE.fullmatch(parts[1]):
        raise VaultError("bad_format")
    nonce = _b64url_decode_canonical(parts[2])
    sealed = _b64url_decode_canonical(parts[3])
    if nonce is None or len(nonce) != NONCE_BYTES or sealed is None or len(sealed) < TAG_BYTES:
        raise VaultError("bad_format")
    return parts[1], nonce, sealed


def key_id(token: str) -> str:
    """The kid a token was encrypted with (parses the header only; nothing is decrypted)."""
    return _split(token)[0]


class VaultSettings(BaseSettings):
    """Key ring for :class:`TokenVault` (environment prefix ``BW_VAULT_``).

    Unset by default: nothing is encrypted until keys are configured, and
    :meth:`TokenVault.from_settings` raises ``VaultError('not_configured')``. The key material
    is validated by :meth:`TokenVault.from_settings` (see the module docs).
    """

    model_config = SettingsConfigDict(
        env_prefix="BW_VAULT_",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    keys: SecretStr | None = None
    """``kid:base64key`` pairs, comma-separated."""

    primary: str | None = None
    """Kid used for new tokens. Defaults to the only kid when the ring holds one key."""

    @field_validator("primary")
    @classmethod
    def _check_primary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        kid = value.strip()
        if not _KID_RE.fullmatch(kid):
            raise ValueError("must be a kid of 1-32 [A-Za-z0-9_-]")
        return kid

    @model_validator(mode="after")
    def _check_primary_needs_keys(self) -> Self:
        if self.keys is None and self.primary is not None:
            raise ValueError("BW_VAULT_PRIMARY is set but BW_VAULT_KEYS is not")
        return self

    @property
    def configured(self) -> bool:
        return self.keys is not None


class TokenVault:
    """Encrypts and decrypts short secrets bound to a caller context (see the module docs)."""

    __slots__ = ("_ciphers", "_primary")

    def __init__(self, keys: Mapping[str, bytes], primary: str) -> None:
        if not keys:
            raise ValueError("the key ring is empty")
        ciphers: dict[str, AESGCM] = {}
        for kid, key in keys.items():
            if not _KID_RE.fullmatch(kid):
                raise ValueError(f"kid {kid!r} must be 1-32 [A-Za-z0-9_-]")
            if len(key) != KEY_BYTES:
                raise ValueError(f"key {kid!r} must be {KEY_BYTES} bytes")
            ciphers[kid] = AESGCM(key)
        if primary not in ciphers:
            raise ValueError("the primary kid is not in the key ring")
        self._ciphers = ciphers
        self._primary = primary

    @classmethod
    def from_settings(cls, settings: VaultSettings | None = None) -> TokenVault:
        """Build the vault from ``BW_VAULT_*`` (read from the environment when not given).

        Raises ``VaultError('not_configured')`` when no keys are set and
        ``VaultError('misconfigured')`` when the ring or the primary kid is invalid.
        """
        resolved = settings if settings is not None else VaultSettings()
        if resolved.keys is None:
            raise VaultError("not_configured")
        try:
            ring = parse_key_ring(resolved.keys.get_secret_value())
        except ValueError as exc:
            raise VaultError("misconfigured", f"BW_VAULT_KEYS {exc}") from None
        if resolved.primary is None:
            if len(ring) != 1:
                raise VaultError(
                    "misconfigured", "BW_VAULT_PRIMARY is required when there are several keys"
                )
            primary = next(iter(ring))
        elif resolved.primary in ring:
            primary = resolved.primary
        else:
            raise VaultError("misconfigured", "BW_VAULT_PRIMARY must name a kid in BW_VAULT_KEYS")
        return cls(ring, primary)

    @property
    def primary_kid(self) -> str:
        return self._primary

    @property
    def kids(self) -> frozenset[str]:
        return frozenset(self._ciphers)

    def encrypt(self, plaintext: str, *, context: str) -> str:
        """Encrypt ``plaintext`` under the primary key, bound to ``context``."""
        aad = _associated_data(self._primary, _context_bytes(context))
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._ciphers[self._primary].encrypt(nonce, plaintext.encode("utf-8"), aad)
        return f"{VERSION}.{self._primary}.{_b64url_encode(nonce)}.{_b64url_encode(sealed)}"

    def decrypt(self, token: str, *, context: str) -> str:
        """Decrypt a token produced by :meth:`encrypt` with the same ``context``.

        Raises :class:`VaultError` with reason ``bad_format``, ``unknown_kid`` or
        ``decrypt_failed`` (tampering, a wrong context and a wrong key are indistinguishable).
        """
        aad_context = _context_bytes(context)
        kid, nonce, sealed = _split(token)
        cipher = self._ciphers.get(kid)
        if cipher is None:
            raise VaultError("unknown_kid")
        try:
            plaintext = cipher.decrypt(nonce, sealed, _associated_data(kid, aad_context))
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError):
            raise VaultError("decrypt_failed") from None

    def needs_rotation(self, token: str) -> bool:
        """``True`` if ``token`` was not encrypted with the current primary key."""
        return key_id(token) != self._primary

    def reencrypt(self, token: str, *, context: str) -> str:
        """Decrypt ``token`` and encrypt it again under the primary key (same context)."""
        return self.encrypt(self.decrypt(token, context=context), context=context)

    def __repr__(self) -> str:
        return f"TokenVault(primary={self._primary!r}, kids={sorted(self._ciphers)!r})"
