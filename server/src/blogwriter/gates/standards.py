"""The standards-ID validator and its catalog (singleton ``standards_catalog``, owner: gates).

Families (plan: "Standards-ID validator: OWASP LLM/agentic, MITRE ATLAS, NIST AI RMF,
SP 800-63-4, CVE"):

=====================  ===================================  =====================================
family                 written as                           checked against
=====================  ===================================  =====================================
``owasp_llm``          ``LLM01``, ``LLM01:2025``            the catalog (LLM01-LLM10)
``owasp_agentic``      ``ASI01``, ``ASI01:2026``            the catalog (ASI01-ASI10)
``mitre_atlas``        ``AML.T0051``, ``AML.T0051.000``,    the catalog (seeds plus the ATLAS
                       ``AML.TA0002``, ``AML.M0015``        data file from ``BW_GATES_ATLAS_URL``)
``nist_ai_rmf``        ``GOVERN 1.1``, ``MAP 3``,           the catalog (4 functions, 19
                       ``GV-1.1-001``                       categories, 72 subcategories)
``nist_sp_800_63_4``   ``SP 800-63B-4``, ``AAL2``           the catalog; revision 3 is
                                                            ``standard-id-superseded``
CVE                    ``CVE-2024-3094``                    the pattern only (4-digit year from
                                                            1999, sequence of 4 or more digits)
=====================  ===================================  =====================================

Findings: ``standard-id-unknown`` (an id the catalog does not have) blocks, overridably, while
the catalog is fresh (:data:`STALE_AFTER`) and the family list is not empty, and only warns
while the catalog is empty or stale ("warn only while the catalog is empty or stale"). The
ATLAS list counts as fresh only after a successful refresh from the ATLAS data file: the shipped
seed list is a snapshot. ``standard-id-malformed`` (a CVE that cannot exist) always blocks,
overridably. ``ref`` is the id as written (spaces replaced by ``-``).

The refresh (job ``gates.standards-refresh``) merges the seed lists shipped with the package
with the stored catalog and, when configured, the ATLAS data file fetched through egress. It
never drops a seeded id, and a failed fetch keeps the previously stored ATLAS ids.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import resources
from typing import Final, Literal, cast

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from blogwriter.domain.base import parse_iso, to_iso, utc_now
from blogwriter.domain.common import Finding
from blogwriter.domain.settings import StandardsCatalog, StandardsFamily

__all__ = [
    "ATLAS_ID_RE",
    "FAMILIES",
    "SEED_VERSION",
    "STALE_AFTER",
    "AtlasData",
    "effective_catalog",
    "merge_catalog",
    "parse_atlas",
    "seed_catalog",
    "seed_families",
    "validate_standard_ids",
]

FAMILIES: Final[tuple[StandardsFamily, ...]] = (
    "owasp_llm",
    "owasp_agentic",
    "mitre_atlas",
    "nist_ai_rmf",
    "nist_sp_800_63_4",
)
SEED_VERSION: Final = "2026-11-26"
"""Version of the seed lists shipped with the package (``sourceVersions.seed``)."""
STALE_AFTER: Final = timedelta(days=14)
"""Weekly refresh plus its 48 h lateness, with room to spare."""
ATLAS_VERSION_KEY: Final = "mitre_atlas"
ATLAS_REFRESHED_KEY: Final = "mitre_atlas_at"
ATLAS_ID_RE: Final = re.compile(r"AML\.(?:TA\d{4}|T\d{4}(?:\.\d{3})?|M\d{4}|CS\d{4})")
MAX_ATLAS_IDS: Final = 10_000

UNKNOWN: Final = "standard-id-unknown"
MALFORMED: Final = "standard-id-malformed"
SUPERSEDED: Final = "standard-id-superseded"

_FAMILY_NAMES: Final[Mapping[StandardsFamily, str]] = {
    "owasp_llm": "OWASP Top 10 for LLM Applications",
    "owasp_agentic": "OWASP Top 10 for Agentic Applications",
    "mitre_atlas": "MITRE ATLAS",
    "nist_ai_rmf": "NIST AI RMF",
    "nist_sp_800_63_4": "NIST SP 800-63-4",
}

# Patterns. Uppercase as the standards write them; prose such as "map 1 to 1" never matches.
_LLM_RE: Final = re.compile(r"\bLLM(\d{2,3})(?::(\d{4}))?\b")
_ASI_RE: Final = re.compile(r"\bASI(\d{2,3})(?::(\d{4}))?\b")
_ATLAS_ANY_RE: Final = re.compile(r"\bAML\.[A-Z]{1,2}[0-9][0-9.]*")
_RMF_RE: Final = re.compile(r"\b(GOVERN|MAP|MEASURE|MANAGE)\s+(\d{1,2})(?:\.(\d{1,2}))?\b")
_RMF_SHORT_RE: Final = re.compile(r"\b(GV|MP|MS|MG)-(\d{1,2})\.(\d{1,2})(?:-\d{3})?\b")
_RMF_FUNCTIONS: Final = {"GV": "GOVERN", "MP": "MAP", "MS": "MEASURE", "MG": "MANAGE"}
_SP80063_RE: Final = re.compile(r"\b(?:SP\s*)?800-63([A-Z])?(?:-(\d{1,2}))?\b")
_LEVEL_RE: Final = re.compile(r"\b(IAL|AAL|FAL)\s?(\d{1,2})\b")
_DASH: Final = r"[-\u2010\u2011\u2013\u2014]"
_CVE_RE: Final = re.compile(rf"\bCVE{_DASH}(\d+){_DASH}(\d+)\b", re.IGNORECASE)


# --------------------------------------------------------------------------------------------
# Seeds and the effective catalog
# --------------------------------------------------------------------------------------------


@functools.cache
def _seed_lists() -> tuple[tuple[StandardsFamily, tuple[str, ...]], ...]:
    root = resources.files("blogwriter.gates").joinpath("data", "standards")
    lists: list[tuple[StandardsFamily, tuple[str, ...]]] = []
    for family in FAMILIES:
        text = root.joinpath(f"{family}.txt").read_text(encoding="utf-8")
        ids = tuple(
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        lists.append((family, ids))
    return tuple(lists)


def seed_families() -> dict[StandardsFamily, list[str]]:
    """The id lists shipped with the package, by family."""
    return {family: list(ids) for family, ids in _seed_lists()}


def seed_catalog(now: str) -> StandardsCatalog:
    """The never-saved catalog the API shows: the seed lists, ``refreshedAt`` unset."""
    default = StandardsCatalog.default(now)
    return default.model_copy(
        update={"families": seed_families(), "source_versions": {"seed": SEED_VERSION}}
    )


def effective_catalog(stored: StandardsCatalog) -> StandardsCatalog:
    """``stored`` with the seed lists merged in (what validation uses; never written)."""
    families = _union(stored.families, seed_families())
    return stored.model_copy(update={"families": families})


def _union(
    first: Mapping[StandardsFamily, Sequence[str]], second: Mapping[StandardsFamily, Sequence[str]]
) -> dict[StandardsFamily, list[str]]:
    merged: dict[StandardsFamily, list[str]] = {}
    for family in FAMILIES:
        seen: dict[str, None] = {}
        for item in (*first.get(family, ()), *second.get(family, ())):
            seen.setdefault(item, None)
        merged[family] = sorted(seen)
    return merged


@dataclass(frozen=True, slots=True)
class AtlasData:
    """Ids read from the MITRE ATLAS data file, and its ``version``."""

    ids: tuple[str, ...]
    version: str | None


def merge_catalog(
    stored: StandardsCatalog, *, atlas: AtlasData | None, now: datetime
) -> StandardsCatalog:
    """The refreshed catalog: seeds plus the stored ids plus fresh ATLAS ids.

    Seeded ids are never dropped. With ``atlas`` the ATLAS family becomes seeds plus the data
    file's ids (ids the file no longer lists go, unless seeded); without it (not configured, or
    the fetch failed) the stored ATLAS ids are kept as they were.
    """
    seeds = seed_families()
    families = _union(stored.families, seeds)
    versions = dict(stored.source_versions)
    versions["seed"] = SEED_VERSION
    stamp = to_iso(now)
    if atlas is not None:
        families["mitre_atlas"] = sorted({*seeds["mitre_atlas"], *atlas.ids})
        versions[ATLAS_VERSION_KEY] = (atlas.version or "unknown")[:100]
        versions[ATLAS_REFRESHED_KEY] = stamp
    return stored.model_copy(
        update={
            "families": families,
            "source_versions": versions,
            "refreshed_at": stamp,
            "updated_at": stamp,
        }
    )


def parse_atlas(data: bytes) -> AtlasData:
    """The ATLAS ids in an ``ATLAS.yaml`` data file (every ``id`` value that is one).

    The YAML is composed, never constructed (no Python objects, no alias expansion: each node
    is visited once). ``ValueError`` when the file is not YAML or holds no ATLAS id.
    """
    try:
        root = yaml.compose(data.decode("utf-8"), Loader=yaml.BaseLoader)
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ValueError("the ATLAS data file is not readable YAML") from exc
    if root is None:
        raise ValueError("the ATLAS data file is empty")
    ids: set[str] = set()
    version: str | None = None
    if isinstance(root, MappingNode):
        for key, value in cast(list[tuple[Node, Node]], root.value):
            if (
                isinstance(key, ScalarNode)
                and key.value == "version"
                and isinstance(value, ScalarNode)
            ):
                version = str(value.value)[:100]
    for key, value in _mapping_items(root):
        if key == "id" and isinstance(value, ScalarNode):
            text = str(value.value).strip()
            if ATLAS_ID_RE.fullmatch(text):
                ids.add(text)
                if len(ids) > MAX_ATLAS_IDS:
                    raise ValueError("the ATLAS data file holds too many ids")
    if not ids:
        raise ValueError("the ATLAS data file holds no ATLAS ids")
    return AtlasData(ids=tuple(sorted(ids)), version=version)


def _mapping_items(root: Node) -> Iterator[tuple[str, Node]]:
    seen: set[int] = set()
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, MappingNode):
            for key, value in cast(list[tuple[Node, Node]], node.value):
                if isinstance(key, ScalarNode):
                    yield str(key.value), value
                stack.append(value)
        elif isinstance(node, SequenceNode):
            stack.extend(cast(list[Node], node.value))


# --------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------

type _UnknownReporter = Callable[[StandardsFamily, str, int, int], None]


def _fresh(stamp: str | None, now: datetime) -> bool:
    if stamp is None:
        return False
    try:
        return now - parse_iso(stamp) <= STALE_AFTER
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class _Strictness:
    block: dict[StandardsFamily, bool]
    max_cve_year: int | None


def _strictness(catalog: StandardsCatalog, now: datetime) -> _Strictness:
    fresh = _fresh(catalog.refreshed_at, now)
    atlas_fresh = _fresh(catalog.source_versions.get(ATLAS_REFRESHED_KEY), now)
    block: dict[StandardsFamily, bool] = {}
    for family in FAMILIES:
        listed = bool(catalog.families.get(family))
        block[family] = fresh and listed and (family != "mitre_atlas" or atlas_fresh)
    year = parse_iso(catalog.refreshed_at).year + 1 if catalog.refreshed_at else None
    return _Strictness(block=block, max_cve_year=year)


def validate_standard_ids(
    text: str, catalog: StandardsCatalog, *, now: datetime | None = None
) -> list[Finding]:
    """Findings for the standards ids in ``text`` (spans into ``text``), sorted by position.

    The seed lists are always merged into ``catalog`` (:func:`effective_catalog`), so a stored
    catalog that misses them, or the never-saved default, still knows every seeded id; whether
    unknown ids block depends on the stored catalog's freshness (module docs).
    """
    moment = now if now is not None else utc_now()
    strictness = _strictness(catalog, moment)
    catalog = effective_catalog(catalog)
    known = {family: frozenset(catalog.families.get(family, ())) for family in FAMILIES}
    findings: list[Finding] = []

    def unknown(family: StandardsFamily, written: str, start: int, end: int) -> None:
        findings.append(
            Finding(
                code=UNKNOWN,
                severity="block" if strictness.block[family] else "warn",
                message=f"Not a known {_FAMILY_NAMES[family]} id. Check it against the source.",
                start=start,
                end=end,
                ref=_ref(written),
                overridable=True,
            )
        )

    for match in _LLM_RE.finditer(text):
        if f"LLM{match.group(1)}" not in known["owasp_llm"]:
            unknown("owasp_llm", match.group(0), match.start(), match.end())
    for match in _ASI_RE.finditer(text):
        if f"ASI{match.group(1)}" not in known["owasp_agentic"]:
            unknown("owasp_agentic", match.group(0), match.start(), match.end())
    for match in _ATLAS_ANY_RE.finditer(text):
        written = match.group(0).rstrip(".")
        end = match.start() + len(written)
        if not ATLAS_ID_RE.fullmatch(written):
            findings.append(_malformed(written, match.start(), end, "a MITRE ATLAS id"))
        elif written not in known["mitre_atlas"]:
            unknown("mitre_atlas", written, match.start(), end)
    for match in _RMF_RE.finditer(text):
        written = match.group(0)
        canonical = " ".join(written.split())
        if canonical not in known["nist_ai_rmf"]:
            unknown("nist_ai_rmf", written, match.start(), match.end())
    for match in _RMF_SHORT_RE.finditer(text):
        function = _RMF_FUNCTIONS[match.group(1)]
        canonical = f"{function} {int(match.group(2))}.{int(match.group(3))}"
        if canonical not in known["nist_ai_rmf"]:
            unknown("nist_ai_rmf", match.group(0), match.start(), match.end())
    findings.extend(_sp_800_63(text, known["nist_sp_800_63_4"], unknown))
    for match in _LEVEL_RE.finditer(text):
        canonical = f"{match.group(1)}{int(match.group(2))}"
        if canonical not in known["nist_sp_800_63_4"]:
            unknown("nist_sp_800_63_4", match.group(0), match.start(), match.end())
    for match in _CVE_RE.finditer(text):
        year, sequence = match.group(1), match.group(2)
        valid_year = len(year) == 4 and int(year) >= 1999
        if valid_year and strictness.max_cve_year is not None:
            valid_year = int(year) <= strictness.max_cve_year
        dash_ok = "-" == match.group(0)[3] == match.group(0)[4 + len(year)]
        if not (valid_year and len(sequence) >= 4 and dash_ok):
            findings.append(_malformed(match.group(0), match.start(), match.end(), "a CVE id"))
    return sorted(findings, key=lambda f: (f.start or 0, f.code))


def _sp_800_63(
    text: str,
    known: Collection[str],
    unknown: _UnknownReporter,
) -> list[Finding]:
    findings: list[Finding] = []
    for match in _SP80063_RE.finditer(text):
        volume = match.group(1) or ""
        revision = match.group(2)
        if revision is None:
            if volume and f"SP 800-63{volume}-4" not in known:
                unknown("nist_sp_800_63_4", match.group(0), match.start(), match.end())
            continue
        if revision != "4" and volume in ("", "A", "B", "C") and int(revision) < 4:
            findings.append(
                Finding(
                    code=SUPERSEDED,
                    severity="warn",
                    message="An earlier revision: NIST SP 800-63-4 supersedes it.",
                    start=match.start(),
                    end=match.end(),
                    ref=_ref(match.group(0)),
                    overridable=True,
                )
            )
        elif f"SP 800-63{volume}-{revision}" not in known:
            unknown("nist_sp_800_63_4", match.group(0), match.start(), match.end())
    return findings


def _malformed(written: str, start: int, end: int, what: str) -> Finding:
    return Finding(
        code=MALFORMED,
        severity="block",
        message=f"This is not a well-formed {what}.",
        start=start,
        end=end,
        ref=_ref(written),
        overridable=True,
    )


def _ref(written: str) -> str | None:
    ref = re.sub(r"\s+", "-", written.strip())
    ref = "".join(char for char in ref if "!" <= char <= "~" and char not in '"\\')
    return ref[:200] or None


type CatalogFreshness = Literal["fresh", "stale", "empty"]


def catalog_freshness(catalog: StandardsCatalog, *, now: datetime) -> CatalogFreshness:
    """How validation treats unknown ids today (shown with the catalog)."""
    if not any(catalog.families.get(family) for family in FAMILIES):
        return "empty"
    return "fresh" if _fresh(catalog.refreshed_at, now) else "stale"
