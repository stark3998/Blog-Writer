"""Tests for blogwriter.core.settings."""

from __future__ import annotations

import traceback
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from blogwriter.core.settings import CoreSettings, normalize_oid, normalize_origin

OID = "0b5d7a52-3c1e-4f7a-9a51-6c2d8e4f1a30"
KEY = SecretStr("k" * 32)
ORIGIN = "https://writer.example.com"


def prod(**overrides: object) -> CoreSettings:
    values: dict[str, object] = {
        "env": "prod",
        "owner_oid": OID,
        "cookie_key": KEY,
        "public_origin": ORIGIN,
    }
    values.update(overrides)
    return CoreSettings.model_validate(values)


# ----------------------------------------------------------------------------------------------
# Defaults and environment
# ----------------------------------------------------------------------------------------------


def test_defaults_are_safe_for_tests() -> None:
    settings = CoreSettings()
    assert settings.env == "test"
    assert settings.owner_oid is None
    assert settings.cookie_key is None
    assert settings.build_sha == "dev"
    assert settings.public_origin is None
    assert settings.spa_dir is None
    assert settings.dev_principal_oid is None
    assert settings.cookie_ttl_hours == 12
    assert settings.cookie_ttl_seconds == 12 * 3600
    assert settings.watchdog_threshold_ms == 500
    assert not settings.is_prod


def test_reads_bw_prefixed_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BW_ENV", "local")
    monkeypatch.setenv("BW_OWNER_OID", OID.upper())
    monkeypatch.setenv("BW_COOKIE_KEY", "x" * 40)
    monkeypatch.setenv("BW_BUILD_SHA", "abc1234")
    monkeypatch.setenv("BW_PUBLIC_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("BW_SPA_DIR", str(tmp_path))
    monkeypatch.setenv("BW_DEV_PRINCIPAL_OID", OID)
    monkeypatch.setenv("BW_COOKIE_TTL_HOURS", "6")
    monkeypatch.setenv("BW_WATCHDOG_THRESHOLD_MS", "250")
    settings = CoreSettings()
    assert settings.env == "local"
    assert settings.owner_oid == OID
    assert settings.cookie_key is not None
    assert settings.cookie_key.get_secret_value() == "x" * 40
    assert settings.build_sha == "abc1234"
    assert settings.public_origin == "http://localhost:5173"
    assert settings.spa_dir == tmp_path
    assert settings.effective_dev_principal_oid == OID
    assert settings.cookie_ttl_seconds == 6 * 3600
    assert settings.watchdog_threshold_ms == 250


def test_other_packages_bw_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_COSMOS_ENDPOINT", "https://cosmos.example")
    monkeypatch.setenv("BW_AI_DEFAULT_MODEL", "x")
    assert CoreSettings().env == "test"


def test_empty_environment_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BW_OWNER_OID", "")
    monkeypatch.setenv("BW_COOKIE_KEY", "")
    settings = CoreSettings()
    assert settings.owner_oid is None
    assert settings.cookie_key is None


def test_unknown_constructor_arguments_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CoreSettings.model_validate({"owner": OID})


def test_settings_are_immutable() -> None:
    settings = CoreSettings()
    with pytest.raises(ValidationError):
        settings.env = "prod"  # pyright: ignore[reportAttributeAccessIssue]


def test_cookie_key_never_appears_in_repr() -> None:
    settings = CoreSettings(cookie_key=SecretStr("super-secret-value-0123456789abcdef"))
    assert "super-secret" not in repr(settings)
    assert "super-secret" not in str(settings.model_dump())


# ----------------------------------------------------------------------------------------------
# Field validation
# ----------------------------------------------------------------------------------------------


def test_object_ids_are_normalized_to_lowercase() -> None:
    settings = CoreSettings(owner_oid=f"  {OID.upper()} ", dev_principal_oid=OID.upper())
    assert settings.owner_oid == OID
    assert settings.dev_principal_oid == OID


@pytest.mark.parametrize("field", ["owner_oid", "dev_principal_oid"])
@pytest.mark.parametrize(
    "value",
    ["owner@example.com", "0b5d7a52", "{0b5d7a52-3c1e-4f7a-9a51-6c2d8e4f1a30}", "z" * 36],
)
def test_object_ids_must_be_guids(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="GUID"):
        CoreSettings.model_validate({field: value})


def test_cookie_key_needs_32_bytes() -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        CoreSettings(cookie_key=SecretStr("k" * 31))
    assert CoreSettings(cookie_key=SecretStr("k" * 32)).cookie_key is not None
    # Bytes, not characters: 16 two-byte characters are 32 bytes.
    assert CoreSettings(cookie_key=SecretStr("é" * 16)).cookie_key is not None


def test_a_rejected_cookie_key_is_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A boot failure escapes uvicorn's factory call and Python prints the traceback to stderr,
    # i.e. the console log. (pydantic still keeps the input in .errors()/.json(); nothing
    # serializes settings errors that way.)
    monkeypatch.setenv("BW_COOKIE_KEY", "SECRET-but-too-short")
    monkeypatch.setenv("BW_OWNER_OID", "SECRET-not-a-guid")
    with pytest.raises(ValidationError) as caught:
        CoreSettings()
    printed = "".join(traceback.format_exception(caught.value))
    assert "at least 32 bytes" in printed
    assert "SECRET" not in printed


@pytest.mark.parametrize("value", ["", "a b", "x" * 65, "-leading-dash", "sha;rm"])
def test_build_sha_is_a_short_identifier(value: str) -> None:
    with pytest.raises(ValidationError):
        CoreSettings(build_sha=value)


@pytest.mark.parametrize("hours", [0, 25])
def test_cookie_ttl_is_bounded(hours: int) -> None:
    with pytest.raises(ValidationError):
        CoreSettings(cookie_ttl_hours=hours)


@pytest.mark.parametrize("threshold", [0, 9, 60_001])
def test_watchdog_threshold_is_bounded(threshold: int) -> None:
    with pytest.raises(ValidationError):
        CoreSettings(watchdog_threshold_ms=threshold)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://writer.example.com", "https://writer.example.com"),
        ("HTTPS://Writer.Example.COM/", "https://writer.example.com"),
        ("https://writer.example.com:443", "https://writer.example.com"),
        ("http://localhost:80", "http://localhost"),
        ("http://localhost:5173", "http://localhost:5173"),
        ("https://[::1]:8443", "https://[::1]:8443"),
        ("https://writer.example.com/app", None),
        ("https://writer.example.com?x=1", None),
        ("https://writer.example.com#top", None),
        ("https://user:pw@writer.example.com", None),
        ("ftp://writer.example.com", None),
        ("writer.example.com", None),
        ("https://", None),
        ("https://writer.example.com:99999", None),
        ("null", None),
        # Browsers send ASCII origins (IDNA hosts as xn--); Unicode is never compared.
        ("https://xn--bcher-kva.example", "https://xn--bcher-kva.example"),
        ("https://b\u00fccher.example", None),
        ("https://\u212aite.example", None),  # KELVIN SIGN: lowercases to an ASCII "k"
        ("https://writer.example.com\u3002", None),  # ideographic full stop
    ],
)
def test_normalize_origin(value: str, expected: str | None) -> None:
    assert normalize_origin(value) == expected


def test_public_origin_must_be_the_ascii_form_browsers_send() -> None:
    # A Unicode origin could never equal a browser's Origin header, so the CSRF fallback would
    # silently never match; refuse it at startup instead.
    with pytest.raises(ValidationError, match="origin"):
        CoreSettings(public_origin="https://b\u00fccher.example")
    punycode = CoreSettings(public_origin="https://xn--bcher-kva.example")
    assert punycode.public_origin == "https://xn--bcher-kva.example"


def test_public_origin_is_validated_and_normalized() -> None:
    assert CoreSettings(public_origin="HTTPS://Writer.Example.com/").public_origin == ORIGIN
    with pytest.raises(ValidationError, match="origin"):
        CoreSettings(public_origin="https://writer.example.com/path")


def test_normalize_oid() -> None:
    assert normalize_oid(OID.upper()) == OID
    assert normalize_oid("not-a-guid") is None


# ----------------------------------------------------------------------------------------------
# Production and environment guards
# ----------------------------------------------------------------------------------------------


def test_valid_prod_settings() -> None:
    settings = prod()
    assert settings.is_prod
    assert settings.owner_oid == OID


@pytest.mark.parametrize(
    ("field", "env_name"),
    [
        ("owner_oid", "BW_OWNER_OID"),
        ("cookie_key", "BW_COOKIE_KEY"),
        ("public_origin", "BW_PUBLIC_ORIGIN"),
    ],
)
def test_prod_fails_closed_without_essentials(field: str, env_name: str) -> None:
    with pytest.raises(ValidationError, match=env_name):
        prod(**{field: None})


def test_prod_requires_https_origin() -> None:
    with pytest.raises(ValidationError, match="https"):
        prod(public_origin="http://writer.example.com")


def test_dev_principal_is_honoured_only_in_local() -> None:
    assert CoreSettings(env="local", dev_principal_oid=OID).effective_dev_principal_oid == OID
    assert CoreSettings(env="test", dev_principal_oid=OID).effective_dev_principal_oid is None
    assert prod(dev_principal_oid=OID).effective_dev_principal_oid is None


@pytest.mark.parametrize("env", ["local", "test"])
def test_non_prod_env_is_refused_inside_container_apps(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("CONTAINER_APP_NAME", "ca-blog-writer-dev")
    with pytest.raises(ValidationError, match="must be 'prod' inside Azure Container Apps"):
        CoreSettings.model_validate({"env": env, "dev_principal_oid": OID})


def test_prod_is_accepted_inside_container_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTAINER_APP_NAME", "ca-blog-writer-dev")
    assert prod().is_prod


def test_default_env_is_refused_inside_container_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    # A deployment that forgets BW_ENV does not start as 'test'.
    monkeypatch.setenv("CONTAINER_APP_NAME", "ca-blog-writer-dev")
    with pytest.raises(ValidationError):
        CoreSettings()
