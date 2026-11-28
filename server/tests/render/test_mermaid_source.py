"""Mermaid without a browser: source neutralisation, unsupported types, the locked configuration,
the pinned bundle and the raster budget."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest

from blogwriter.render import BrandColors, MermaidError, OutputLimitError
from blogwriter.render.errors import AssetIntegrityError
from blogwriter.render.mermaid import (
    MAX_RASTER_PIXELS,
    MermaidBundle,
    check_raster_size,
    load_bundle,
    mermaid_config,
    neutralise_source,
    unsupported_reason,
)
from blogwriter.render.settings import MERMAID_TINY_SHA256, MERMAID_VERSION

HOSTILE = """---
config:
  securityLevel: loose
  flowchart:
    htmlLabels: true
---
%%{init: {"securityLevel": "loose", "htmlLabels": true, "themeCSS": "* {background: url(https://evil.example/x.png)}"}}%%
flowchart TD
  A["<script>alert(1)</script>"] --> B["<img src=x onerror=alert(2)>"]
  B --> C[Callback target]
  C --> D["<a href='javascript:alert(3)'>link</a>"]
  click A call alert(4)
  click B callback "tooltip"
  click C href "javascript:alert(5)" "Tooltip" _blank
  click D "https://evil.example/" _blank
  style D fill:url(https://evil.example/fill.png)
"""


def test_neutralise_removes_config_and_interaction_and_reports_it() -> None:
    clean, warnings = neutralise_source(HOSTILE)
    assert "config:" not in clean
    assert "securityLevel" not in clean
    assert "%%{" not in clean
    assert not re.search(r"(?im)^\s*click\b", clean)
    assert "url(" not in clean
    assert len(warnings) == 4
    assert clean.splitlines()[0] == "flowchart TD"


def test_neutralise_keeps_ordinary_diagrams_intact() -> None:
    src = "flowchart LR\n  linkStyle default stroke:#333\n  A[clicker] --> B[link to docs]\n"
    clean, warnings = neutralise_source(src)
    assert warnings == []
    assert clean == src


def test_class_diagram_link_and_callback_lines_are_removed() -> None:
    src = (
        'classDiagram\n  class Agent\n  link Agent "https://evil.example"\n'
        '  callback Agent "fn" "tip"\n'
    )
    clean, warnings = neutralise_source(src)
    assert "evil" not in clean
    assert "callback" not in clean
    assert warnings


@pytest.mark.parametrize(
    ("source", "unsupported"),
    [
        ("flowchart TD\n  A --> B", False),
        ("sequenceDiagram\n  A->>B: hi", False),
        ("mindmap\n  root((Identity))", True),
        ("architecture-beta\n  service api(server)[API]", True),
        ("flowchart TD\n  A[$$x^2$$] --> B", True),
    ],
)
def test_types_only_the_full_build_has_are_refused(source: str, unsupported: bool) -> None:
    assert (unsupported_reason(source) is not None) is unsupported


def test_locked_configuration() -> None:
    config = mermaid_config(BrandColors(), 26)
    assert config["securityLevel"] == "strict"
    assert config["htmlLabels"] is False
    assert config["flowchart"]["htmlLabels"] is False
    assert config["fontSize"] == 26
    assert config["themeVariables"]["fontSize"] == "26px"
    for key in ("securityLevel", "htmlLabels", "themeCSS", "flowchart", "sequence", "secure"):
        assert key in config["secure"]
    assert config["maxTextSize"] == 4000
    assert config["maxEdges"] == 200


def test_bundle_integrity_mismatch_is_refused(tmp_path: Path) -> None:
    fake = tmp_path / "mermaid.tiny.js"
    fake.write_text("globalThis.mermaid = {render(){}}")
    with pytest.raises(AssetIntegrityError, match="sha256 mismatch"):
        load_bundle(fake, MERMAID_TINY_SHA256)
    with pytest.raises(AssetIntegrityError, match="not found"):
        load_bundle(tmp_path / "missing.js", MERMAID_TINY_SHA256)


def test_bundle_csp_hash_is_the_script_text_hash(tmp_path: Path) -> None:
    source = "globalThis.mermaid = {};\n"
    path = tmp_path / "bundle.js"
    path.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(source.encode()).hexdigest()
    loaded = load_bundle(path, digest)
    expected = base64.b64encode(hashlib.sha256(source.encode()).digest()).decode()
    assert loaded.csp_hash == f"sha256-{expected}"
    assert loaded.sha256 == digest


def test_pinned_bundle_matches_settings(bundle: MermaidBundle) -> None:
    """Needs ``npm ci --ignore-scripts`` in server/vendor (skips with that reason otherwise)."""
    assert bundle.sha256 == MERMAID_TINY_SHA256
    assert bundle.csp_hash.startswith("sha256-")
    assert 'globalThis["mermaid"]' in bundle.source
    assert len(bundle.source) < 3_500_000  # the tiny build (~2.85 MB), not the full 5.5 MB one


def test_vendor_lockfile_pins_the_same_release() -> None:
    vendor = Path(__file__).resolve().parents[2] / "vendor"
    assert f'"@mermaid-js/tiny": "{MERMAID_VERSION}"' in (vendor / "package.json").read_text()
    lock = (vendor / "package-lock.json").read_text()
    assert f"@mermaid-js/tiny/-/tiny-{MERMAID_VERSION}.tgz" in lock
    assert "node_modules/mermaid" not in lock  # only the tiny build is shipped


def test_raster_budget_boundaries() -> None:
    check_raster_size(4000, 3000)  # 12 MP: allowed
    with pytest.raises(OutputLimitError, match="too large to rasterise"):
        check_raster_size(4002, 3000)
    with pytest.raises(OutputLimitError, match="per side"):
        check_raster_size(16_386, 20)  # tiny area, but too wide for Chromium's capture
    assert MAX_RASTER_PIXELS == 4000 * 3000


def test_mermaid_error_is_a_render_error() -> None:
    from blogwriter.render import RenderError

    assert issubclass(MermaidError, RenderError)
