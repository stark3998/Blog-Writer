"""The standards-ID validator (golden valid and invalid ids per family) and its catalog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blogwriter.domain.settings import StandardsCatalog
from blogwriter.gates.standards import (
    FAMILIES,
    STALE_AFTER,
    AtlasData,
    catalog_freshness,
    effective_catalog,
    merge_catalog,
    parse_atlas,
    seed_catalog,
    seed_families,
    validate_standard_ids,
)

T0 = datetime(2026, 11, 26, 12, tzinfo=UTC)
STAMP = "2026-11-26T12:00:00.000000Z"


def fresh_catalog(*, atlas: AtlasData | None = None) -> StandardsCatalog:
    return merge_catalog(StandardsCatalog.default(STAMP), atlas=atlas, now=T0)


def valid_lines(goldens: Path) -> list[str]:
    text = (goldens / "standards" / "valid.txt").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]


def invalid_rows(goldens: Path) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for line in (goldens / "standards" / "invalid.tsv").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            sample, code, ref, severity = line.split("\t")
            rows.append((sample, code, ref, severity))
    return rows


def test_valid_ids_give_no_finding(goldens: Path) -> None:
    catalog = fresh_catalog()
    for line in valid_lines(goldens):
        assert validate_standard_ids(line, catalog, now=T0) == [], line


def test_invalid_ids_golden(goldens: Path) -> None:
    catalog = fresh_catalog()
    rows = invalid_rows(goldens)
    assert len(rows) >= 15
    for sample, code, ref, severity in rows:
        findings = validate_standard_ids(sample, catalog, now=T0)
        assert [(f.code, f.ref, f.severity) for f in findings] == [(code, ref, severity)], sample
        finding = findings[0]
        assert finding.overridable
        assert finding.start is not None
        assert finding.end is not None
        written = sample[finding.start : finding.end]
        printable = "".join(char for char in written.replace(" ", "-") if "!" <= char <= "~")
        assert printable == ref  # the span is the id as written; ref is its printable form


def test_unknown_ids_only_warn_while_the_catalog_is_empty_or_stale(goldens: Path) -> None:
    stale = fresh_catalog().model_copy(update={"refreshed_at": "2026-10-01T00:00:00.000000Z"})
    never_refreshed = StandardsCatalog.default(STAMP)
    empty_but_fresh = StandardsCatalog.default(STAMP).model_copy(update={"refreshed_at": STAMP})
    for catalog in (stale, never_refreshed, empty_but_fresh):
        for sample, code, _, _ in invalid_rows(goldens):
            findings = validate_standard_ids(sample, catalog, now=T0)
            expected = "block" if code == "standard-id-malformed" else "warn"
            assert [f.severity for f in findings] == [expected], sample
    # Seeds count even in a never-saved catalog: valid ids stay valid.
    assert validate_standard_ids("LLM01 and GOVERN 1.1", never_refreshed, now=T0) == []


def test_atlas_ids_block_only_after_a_data_file_refresh() -> None:
    data = AtlasData(ids=("AML.T0099", "AML.T0100.001"), version="5.2.0")
    refreshed = fresh_catalog(atlas=data)
    assert validate_standard_ids("AML.T0099 and AML.T0100.001", refreshed, now=T0) == []
    unknown = validate_standard_ids("AML.T9999", refreshed, now=T0)
    assert [(f.code, f.severity) for f in unknown] == [("standard-id-unknown", "block")]
    later = T0 + STALE_AFTER + timedelta(days=1)
    assert validate_standard_ids("AML.T9999", refreshed, now=later)[0].severity == "warn"


def test_future_cves_are_malformed_once_the_catalog_has_a_date() -> None:
    catalog = fresh_catalog()
    assert validate_standard_ids("CVE-2027-1234", catalog, now=T0) == []
    assert validate_standard_ids("CVE-2028-1234", catalog, now=T0)[0].code == (
        "standard-id-malformed"
    )


def test_seeds_are_never_dropped() -> None:
    seeds = seed_families()
    assert set(seeds) == set(FAMILIES)
    assert len(seeds["owasp_llm"]) == 10
    assert len(seeds["owasp_agentic"]) == 10
    assert len([i for i in seeds["nist_ai_rmf"] if "." in i]) == 72
    stored = StandardsCatalog.default(STAMP).model_copy(
        update={"families": {"mitre_atlas": ["AML.T0999"]}}
    )
    merged = merge_catalog(stored, atlas=None, now=T0)
    for family, ids in seeds.items():
        assert set(ids) <= set(merged.families[family])
    assert "AML.T0999" in merged.families["mitre_atlas"]  # a failed fetch keeps old ids
    refreshed = merge_catalog(merged, atlas=AtlasData(ids=("AML.T0100",), version=None), now=T0)
    assert "AML.T0999" not in refreshed.families["mitre_atlas"]  # gone from the data file
    assert set(seeds["mitre_atlas"]) <= set(refreshed.families["mitre_atlas"])
    assert refreshed.source_versions["mitre_atlas"] == "unknown"
    assert refreshed.refreshed_at == STAMP


def test_effective_and_seed_catalogs() -> None:
    seeded = seed_catalog(STAMP)
    assert seeded.refreshed_at is None
    assert seeded.families["owasp_llm"][0] == "LLM01"
    assert catalog_freshness(seeded, now=T0) == "stale"
    assert catalog_freshness(StandardsCatalog.default(STAMP), now=T0) == "empty"
    assert catalog_freshness(fresh_catalog(), now=T0) == "fresh"
    merged = effective_catalog(StandardsCatalog.default(STAMP))
    assert merged.families["nist_sp_800_63_4"] == sorted(seed_families()["nist_sp_800_63_4"])


ATLAS_YAML = b"""\
id: ATLAS
name: Adversarial Threat Landscape for AI Systems
version: 5.1.0
matrices:
- id: ATLAS
  tactics:
  - id: AML.TA0002
    name: Reconnaissance
  techniques:
  - id: &technique AML.T0099
    name: A technique
  - id: *technique
  - id: AML.T0051.003
    name: A sub-technique
  - id: not-an-atlas-id
case-studies:
- id: AML.CS0001
"""


def test_parse_atlas_reads_ids_and_version_without_expanding_aliases() -> None:
    data = parse_atlas(ATLAS_YAML)
    assert data.version == "5.1.0"
    assert data.ids == ("AML.CS0001", "AML.T0051.003", "AML.T0099", "AML.TA0002")


@pytest.mark.parametrize(
    "payload",
    [b"", b"id: [unclosed", b"\xff\xfe", b"items:\n- id: nothing-here\n"],
)
def test_parse_atlas_refuses_unusable_files(payload: bytes) -> None:
    with pytest.raises(ValueError, match="ATLAS"):
        parse_atlas(payload)
