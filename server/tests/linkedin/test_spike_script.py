"""The Phase 0 live-test script must default to a dry run and never touch the network then."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "linkedin_spike.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("linkedin_spike", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations through sys.modules, so register before executing.
    sys.modules["linkedin_spike"] = module
    spec.loader.exec_module(module)
    return module


def test_default_is_dry_run_and_never_imports_httpx(capsys: pytest.CaptureFixture[str]) -> None:
    sys.modules.pop("httpx", None)
    module = load()
    assert module.main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("DRY RUN")
    assert "httpx" not in sys.modules
    assert "Bearer ***" in out  # tokens are masked even in dry runs
    assert "Linkedin-Version" in out
    assert "202611" in out


def test_live_without_env_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINKEDIN_PERSON_URN", raising=False)
    assert load().main(["--live"]) == 2


def test_live_without_exact_phrase_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:abc")

    def fake_input(_prompt: str) -> str:
        return "yes"

    monkeypatch.setattr("builtins.input", fake_input)
    module = load()

    def boom(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("run_live must not be called without the confirmation phrase")

    monkeypatch.setattr(module, "run_live", boom)
    assert module.main(["--live"]) == 3


def test_text_payload_is_escaped_and_keeps_the_hashtag() -> None:
    module = load()
    requests = module.plan("tok", "urn:li:person:abc")
    commentary = requests[0].json_body["commentary"]
    assert "\\|" in commentary
    assert "C\\#" in commentary
    assert commentary.endswith("#AISecurity")


def test_minimal_pdf_has_three_pages() -> None:
    pdf = load().minimal_pdf()
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert pdf.count(b"/Type /Page ") == 3
