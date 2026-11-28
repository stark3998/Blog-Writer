"""LinkedIn analytics export import (**beta**, behind ``BW_ANALYTICS_IMPORT_BETA``).

The plan sets the export format from a real sample (Phase 0); until that sample exists this
parser follows the **assumed format** below and stays off by default. The snapshot form always
works. An upload the parser does not recognise answers 422 ``unknown_export_format``.

Assumed format
--------------
LinkedIn's creator analytics export, downloaded from a post's or the profile's analytics page:

- **Files**: an ``.xlsx`` workbook (one or more sheets) or a ``.csv`` file (UTF-8 with or without
  a BOM, UTF-16 with a BOM, or Windows-1252; comma, semicolon or tab separated). Legacy ``.xls``
  is refused (415).
- **Table layout** (the "top posts" sheet; a CSV): a header row somewhere in the first 40 rows
  (description lines above it are skipped), then one row per post. Headers are matched
  case-insensitively, ignoring spaces and punctuation:

  ==============  ====================================================================
  column          header names
  ==============  ====================================================================
  post URL / URN  Post URL, URL, Post link, Link, Permalink, URN, Post URN, Activity URN,
                  Share URN, Update URN
  publish date    Post publish date, Publish date, Post date, Date, Created date,
                  Posted on, Published on, Date published
  impressions     Impressions, Post impressions, Total impressions
  reactions       Reactions, Likes, Total reactions
  comments        Comments, Total comments
  reposts         Reposts, Shares, Total reposts
  saves           Saves
  sends           Sends, Sends on LinkedIn
  ==============  ====================================================================

  Tables side by side (separated by an empty header cell, as in the "top posts" sheet: posts by
  engagements next to posts by impressions) are read separately and merged by post URN. A table
  needs the post URL column and at least one metric column; other columns ("Engagements",
  "Members reached", engagement rates) are ignored.
- **Key-value layout** (a single post's export): label cells in one column and values in the
  next ("Post URL" | link, "Post Date" | date, "Impressions" | 1,234, ...), with the same names.
- **Values**: dates as Excel dates, ISO ``2026-11-20``, US ``11/20/2026`` (month first, as the
  US export writes them), ``Nov 20, 2026`` and the like, optionally followed by a time; counts
  as numbers or text with thousands separators. Sheets with daily totals ("Engagement",
  "Discovery", "Followers", "Demographics") have no post URL column and are skipped.

Matching (contract W-B6: "by date, then URN")
---------------------------------------------
Only live posts (``status == posted``) are candidates, and a post's date is the local date
(cadence time zone) it went live. A row whose date has exactly one candidate post matches it,
unless the row's URN names another post (then the row is ambiguous and stays unmatched). When
several posts went live that day, the URN decides among them. A row without a date, or whose
date has no candidate, matches by URN alone. URNs are compared by their numbers
(``urn:li:activity:N``, ``urn:li:share:N``, ``urn:li:ugcPost:N``, ``...-activity-N-...`` in
``/posts/`` links) against the post's ``linkedinUrn`` and ``postUrl``. LinkedIn's activity and
share numbers differ for the same post, which is why the date comes first.

Each matched post gets one snapshot (``source: import``, id = the import id, ``offset`` from the
post's age, numbers merged across the rows that matched it); importing the same bytes again
returns the earlier import (the import id is derived from the SHA-256). Nothing from the file is
stored except the counts, and nothing is logged but counts.

Limits: the upload cap (``BW_ANALYTICS_IMPORT_MAX_BYTES``), 40 MB unpacked, 500 zip entries,
12 sheets, 64 columns, 2,000 rows per sheet. Parsing runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import csv
import io
import math
import re
import unicodedata
import warnings
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Final, Literal
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import openpyxl

from blogwriter.analytics.snapshots import SnapshotRefused, offset_for, with_snapshot
from blogwriter.db import Conflict, Filter, NotFound, OrderBy, Store
from blogwriter.domain.analytics import AnalyticsImport
from blogwriter.domain.base import derived_id, parse_iso, sha256_hex, to_iso
from blogwriter.domain.docstore import (
    Stored,
    create_doc,
    find_docs,
    read_doc,
    read_singleton,
    update_doc,
)
from blogwriter.domain.posts import MetricsSnapshot, Post
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.states import PostStatus

__all__ = [
    "CSV_CONTENT_TYPES",
    "GENERIC_CONTENT_TYPES",
    "MAX_COLUMNS",
    "MAX_DATA_ROWS",
    "MAX_SHEETS",
    "MAX_UNPACKED_BYTES",
    "XLSX_CONTENT_TYPES",
    "Candidate",
    "Column",
    "ExportFormatError",
    "ExportKind",
    "ExportRow",
    "ImportOutcome",
    "MatchResult",
    "clean_filename",
    "export_kind",
    "import_export",
    "import_id_for",
    "linkedin_ids",
    "match_rows",
    "parse_export",
    "rows_from_grid",
]

type ExportKind = Literal["xlsx", "csv"]
type Column = Literal[
    "date", "url", "impressions", "reactions", "comments", "reposts", "saves", "sends"
]
type MetricColumn = Literal["impressions", "reactions", "comments", "reposts", "saves", "sends"]

XLSX_CONTENT_TYPES: Final = frozenset(
    {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
)
CSV_CONTENT_TYPES: Final = frozenset(
    {
        "text/csv",
        "application/csv",
        "text/comma-separated-values",
        "text/plain",
        "application/vnd.ms-excel",  # what browsers on Windows send for .csv files
    }
)
GENERIC_CONTENT_TYPES: Final = frozenset({"", "application/octet-stream"})

MAX_UNPACKED_BYTES: Final = 40 * 1024 * 1024
MAX_ZIP_ENTRIES: Final = 500
MAX_SHEETS: Final = 12
MAX_COLUMNS: Final = 64
HEADER_SCAN_ROWS: Final = 40
MAX_DATA_ROWS: Final = 2000
MAX_SCAN_ROWS: Final = HEADER_SCAN_ROWS + MAX_DATA_ROWS
MAX_CELL_CHARS: Final = 2000
MAX_COUNT: Final = 1_000_000_000
_EMPTY_RUN_STOP: Final = 50
"""A table ends after this many empty rows in a row."""

_ZIP_MAGIC: Final = b"PK\x03\x04"
_OLE_MAGIC: Final = b"\xd0\xcf\x11\xe0"
_METRICS: Final[tuple[MetricColumn, ...]] = (
    "impressions",
    "reactions",
    "comments",
    "reposts",
    "saves",
    "sends",
)
_HEADERS: Final[Mapping[Column, tuple[str, ...]]] = {
    "url": (
        "posturl",
        "url",
        "postlink",
        "link",
        "permalink",
        "urn",
        "posturn",
        "activityurn",
        "shareurn",
        "updateurn",
    ),
    "date": (
        "postpublishdate",
        "publishdate",
        "postdate",
        "date",
        "createddate",
        "postedon",
        "publishedon",
        "datepublished",
    ),
    "impressions": ("impressions", "postimpressions", "totalimpressions"),
    "reactions": ("reactions", "likes", "totalreactions"),
    "comments": ("comments", "totalcomments"),
    "reposts": ("reposts", "shares", "totalreposts"),
    "saves": ("saves",),
    "sends": ("sends", "sendsonlinkedin"),
}
_HEADER_INDEX: Final[Mapping[str, Column]] = {
    name: column for column, names in _HEADERS.items() for name in names
}
_ID_RE: Final = re.compile(
    r"(?:urn:li:(?:activity|share|ugcpost):|activity[-:])([0-9]{6,30})", re.IGNORECASE
)
_DATE_FORMATS: Final = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y/%m/%d",
    "%m-%d-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d %b %Y",
    "%d %B %Y",
)
_EXCEL_EPOCH: Final = date(1899, 12, 30)
_FILENAME_DROP: Final = re.compile(r"[\x00-\x1f\x7f]+")


class ExportFormatError(ValueError):
    """The upload is not an export this parser reads. ``code`` and ``status`` make the problem:
    ``unknown_export_format`` (422), ``too_large`` (413), ``unsupported_media_type`` (415)."""

    def __init__(
        self,
        code: Literal["unknown_export_format", "too_large", "unsupported_media_type"],
        detail: str,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.status = {"unknown_export_format": 422, "too_large": 413}.get(code, 415)


@dataclass(frozen=True, slots=True)
class ExportRow:
    """One post's numbers from the export (the URL itself is never kept: only its numbers)."""

    day: date | None
    ids: frozenset[str] = frozenset()
    impressions: int | None = None
    reactions: int | None = None
    comments: int | None = None
    reposts: int | None = None
    saves: int | None = None
    sends: int | None = None

    @property
    def has_metrics(self) -> bool:
        return any(getattr(self, metric) is not None for metric in _METRICS)

    def merged(self, other: ExportRow) -> ExportRow:
        """Both rows' numbers (the larger value where both have one: counts only grow)."""
        values: dict[str, int | None] = {}
        for metric in _METRICS:
            mine: int | None = getattr(self, metric)
            theirs: int | None = getattr(other, metric)
            values[metric] = (
                theirs if mine is None else mine if theirs is None else max(mine, theirs)
            )
        return ExportRow(
            day=self.day if self.day is not None else other.day,
            ids=self.ids | other.ids,
            impressions=values["impressions"],
            reactions=values["reactions"],
            comments=values["comments"],
            reposts=values["reposts"],
            saves=values["saves"],
            sends=values["sends"],
        )


# --------------------------------------------------------------------------------------------
# Cells
# --------------------------------------------------------------------------------------------


def _blank(cell: object) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip())


def _header_key(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^a-z0-9]", "", folded)


def _column_of(cell: object) -> Column | None:
    if not isinstance(cell, str) or len(cell) > 80:
        return None
    return _HEADER_INDEX.get(_header_key(cell))


def _count(cell: object) -> int | None:
    """A non-negative whole count, or ``None`` (blank, text, a percentage, a negative)."""
    match cell:
        case bool():
            return None
        case int():
            value = cell
        case float() | Decimal():
            number = float(cell)
            if not math.isfinite(number) or number != math.floor(number):
                return None
            value = int(number)
        case str():
            text = cell.strip().replace(",", "").replace("\u00a0", "").replace("\u202f", "")
            text = text.replace(" ", "")
            if not re.fullmatch(r"[0-9]{1,12}(?:\.0+)?", text):
                return None
            value = int(text.split(".")[0])
        case _:
            return None
    return value if 0 <= value <= MAX_COUNT else None


def _plausible(day: date) -> date | None:
    return day if 2003 <= day.year <= 2100 else None


def _day(cell: object) -> date | None:
    """A calendar date from a date cell, an Excel serial number or common date text."""
    match cell:
        case datetime():
            return _plausible(cell.date())
        case date():
            return _plausible(cell)
        case bool():
            return None
        case int() | float():
            if not 20_000 <= cell <= 80_000:
                return None
            return _plausible(_EXCEL_EPOCH + timedelta(days=int(cell)))
        case str():
            return _day_text(cell.strip())
        case _:
            return None


def _day_text(text: str) -> date | None:
    if not text or len(text) > 40:
        return None
    candidates = dict.fromkeys(
        (text, text.split("T", 1)[0], text.split(" ", 1)[0], " ".join(text.split(" ")[:3]))
    )
    for candidate in candidates:
        for pattern in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(candidate.strip(), pattern)
            except ValueError:
                continue
            return _plausible(parsed.date())
    return None


def linkedin_ids(text: str | None) -> frozenset[str]:
    """The LinkedIn activity, share and ugcPost numbers in a URN or post URL."""
    if not text:
        return frozenset()
    decoded = unquote(text[:MAX_CELL_CHARS])
    return frozenset(match.group(1) for match in _ID_RE.finditer(decoded))


def _ids(cell: object) -> frozenset[str]:
    if isinstance(cell, str):
        return linkedin_ids(cell)
    return frozenset()


# --------------------------------------------------------------------------------------------
# Layouts
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Block:
    """One table of a header row: the column index of each recognised header."""

    columns: Mapping[Column, int] = field(default_factory=dict[Column, int])

    @property
    def usable(self) -> bool:
        return "url" in self.columns and any(metric in self.columns for metric in _METRICS)


def _blocks(header: Sequence[object]) -> list[_Block]:
    """Tables side by side: runs of non-empty header cells, each mapped on its own."""
    blocks: list[_Block] = []
    current: dict[Column, int] = {}
    for index, cell in enumerate(header):
        if _blank(cell):
            if current:
                blocks.append(_Block(current))
                current = {}
            continue
        column = _column_of(cell)
        if column is not None and column not in current:
            current[column] = index
    if current:
        blocks.append(_Block(current))
    return [block for block in blocks if block.usable]


def _cell(row: Sequence[object], index: int) -> object:
    return row[index] if index < len(row) else None


def _row_of(row: Sequence[object], block: _Block) -> ExportRow | None:
    columns = block.columns
    ids = _ids(_cell(row, columns["url"]))
    day = _day(_cell(row, columns["date"])) if "date" in columns else None
    counts = {
        metric: _count(_cell(row, columns[metric])) if metric in columns else None
        for metric in _METRICS
    }
    found = ExportRow(day=day, ids=ids, **counts)
    if not found.has_metrics or (not ids and day is None):
        return None
    return found


def _table_rows(grid: Sequence[Sequence[object]]) -> list[ExportRow]:
    for start, header in enumerate(grid[:HEADER_SCAN_ROWS]):
        blocks = _blocks(header)
        if not blocks:
            continue
        rows: list[ExportRow] = []
        empty = 0
        for row in grid[start + 1 : start + 1 + MAX_DATA_ROWS]:
            if all(_blank(cell) for cell in row):
                empty += 1
                if empty >= _EMPTY_RUN_STOP:
                    break
                continue
            empty = 0
            for block in blocks:
                parsed = _row_of(row, block)
                if parsed is not None:
                    rows.append(parsed)
        return rows
    return []


def _key_value_row(grid: Sequence[Sequence[object]]) -> ExportRow | None:
    values: dict[Column, object] = {}
    for row in grid[:HEADER_SCAN_ROWS]:
        cells = [cell for cell in row if not _blank(cell)]
        if len(cells) < 2:
            continue
        column = _column_of(cells[0])
        if column is not None and column not in values:
            values[column] = cells[1]
    if "url" not in values:
        return None
    found = ExportRow(
        day=_day(values.get("date")),
        ids=_ids(values["url"]),
        impressions=_count(values.get("impressions")),
        reactions=_count(values.get("reactions")),
        comments=_count(values.get("comments")),
        reposts=_count(values.get("reposts")),
        saves=_count(values.get("saves")),
        sends=_count(values.get("sends")),
    )
    if not found.has_metrics or (not found.ids and found.day is None):
        return None
    return found


def rows_from_grid(grid: Sequence[Sequence[object]]) -> list[ExportRow]:
    """The post rows of one sheet: a table with a header row, else a key-value layout."""
    rows = _table_rows(grid)
    if rows:
        return rows
    single = _key_value_row(grid)
    return [single] if single is not None else []


def _merge_rows(rows: Iterable[ExportRow]) -> list[ExportRow]:
    """Rows of the same post (same URN numbers) merged; rows without URNs kept as they are."""
    by_ids: dict[frozenset[str], ExportRow] = {}
    loose: list[ExportRow] = []
    for row in rows:
        if not row.ids:
            loose.append(row)
            continue
        prior = by_ids.get(row.ids)
        by_ids[row.ids] = row if prior is None else prior.merged(row)
    return [*by_ids.values(), *loose]


# --------------------------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------------------------


def export_kind(data: bytes, content_type: str) -> ExportKind:
    """``xlsx`` or ``csv`` from the bytes (the declared type only has to be plausible)."""
    declared = content_type.split(";", 1)[0].strip().lower()
    allowed = XLSX_CONTENT_TYPES | CSV_CONTENT_TYPES | GENERIC_CONTENT_TYPES
    if declared not in allowed:
        raise ExportFormatError(
            "unsupported_media_type", "Upload the export as an .xlsx or .csv file."
        )
    if data.startswith(_ZIP_MAGIC):
        return "xlsx"
    if data.startswith(_OLE_MAGIC):
        raise ExportFormatError(
            "unsupported_media_type", "Old .xls workbooks are not read. Save it as .xlsx."
        )
    if declared in XLSX_CONTENT_TYPES:
        raise ExportFormatError("unknown_export_format", "The file is not an .xlsx workbook.")
    return "csv"


def _check_zip(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
        raise ExportFormatError(
            "unknown_export_format", "The workbook could not be opened."
        ) from None
    if len(entries) > MAX_ZIP_ENTRIES or sum(entry.file_size for entry in entries) > (
        MAX_UNPACKED_BYTES
    ):
        raise ExportFormatError("too_large", "The workbook unpacks to more than 40 MB.")


def _xlsx_grids(data: bytes) -> list[list[tuple[object, ...]]]:
    _check_zip(data)
    grids: list[list[tuple[object, ...]]] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl warns about styles and extensions it skips
        try:
            workbook = openpyxl.load_workbook(
                io.BytesIO(data), read_only=True, data_only=True, keep_links=False
            )
        except Exception:  # any reader error means "not a workbook we can read"
            raise ExportFormatError(
                "unknown_export_format", "The workbook could not be opened."
            ) from None
        try:
            for sheet in workbook.worksheets[:MAX_SHEETS]:
                rows = sheet.iter_rows(max_row=MAX_SCAN_ROWS, max_col=MAX_COLUMNS, values_only=True)
                grids.append([tuple(_clip(cell) for cell in row) for row in rows])
        except ExportFormatError:
            raise
        except Exception:
            raise ExportFormatError(
                "unknown_export_format", "The workbook could not be read."
            ) from None
        finally:
            workbook.close()
    return grids


def _clip(cell: object) -> object:
    if isinstance(cell, str) and len(cell) > MAX_CELL_CHARS:
        return cell[:MAX_CELL_CHARS]
    return cell


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _delimiter(sample: str) -> str:
    lines = [line for line in sample.splitlines() if line.strip()][:20]
    counts = {
        delimiter: sum(line.count(delimiter) for line in lines) for delimiter in (",", ";", "\t")
    }
    best = max(counts, key=lambda delimiter: counts[delimiter])
    return best if counts[best] else ","


def _csv_grid(data: bytes) -> list[list[object]]:
    text = _decode(data)
    if "\x00" in text:
        raise ExportFormatError("unknown_export_format", "The file is not a text export.")
    reader = csv.reader(io.StringIO(text), delimiter=_delimiter(text[:8192]))
    grid: list[list[object]] = []
    try:
        for row in reader:
            grid.append([_clip(cell) for cell in row[:MAX_COLUMNS]])
            if len(grid) >= MAX_SCAN_ROWS:
                break
    except csv.Error:
        raise ExportFormatError(
            "unknown_export_format", "The CSV file could not be read."
        ) from None
    return grid


def parse_export(data: bytes, *, kind: ExportKind) -> list[ExportRow]:
    """Every post row of the export, merged per post (CPU-bound: run it in a thread).

    Raises :class:`ExportFormatError` when nothing in the file looks like a per-post export.
    """
    grids: Sequence[Sequence[Sequence[object]]] = (
        _xlsx_grids(data) if kind == "xlsx" else [_csv_grid(data)]
    )
    rows = [row for grid in grids for row in rows_from_grid(grid)]
    if not rows:
        raise ExportFormatError(
            "unknown_export_format",
            "No post table found (it needs a post URL column and at least one metric).",
        )
    return _merge_rows(rows)


def clean_filename(name: str | None) -> str | None:
    """A display name for the upload: the last path segment, no control characters, at most
    120 characters (``None`` when nothing is left)."""
    if name is None:
        return None
    base = re.split(r"[\\/]", name)[-1]
    text = " ".join(_FILENAME_DROP.sub(" ", base).split())[:120].strip()
    return text or None


# --------------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """A live post as the matcher sees it: its local go-live date and URN numbers."""

    post_id: str
    day: date
    ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class MatchResult:
    matched: Mapping[str, ExportRow]
    """Post id -> the numbers of the rows that matched it (merged)."""
    row_counts: Mapping[str, int]
    """Post id -> how many export rows matched it."""
    unmatched: int
    """Rows that matched no post (or were ambiguous)."""


def _match_one(
    row: ExportRow, by_day: Mapping[date, Sequence[Candidate]], everyone: Sequence[Candidate]
) -> str | None:
    by_urn = [candidate for candidate in everyone if row.ids and candidate.ids & row.ids]
    if row.day is not None:
        same_day = by_day.get(row.day, ())
        if len(same_day) == 1:
            only = same_day[0]
            if by_urn and all(candidate.post_id != only.post_id for candidate in by_urn):
                return None  # the URN names another post: ambiguous, never guessed
            return only.post_id
        if len(same_day) > 1:
            urn_ids = {candidate.post_id for candidate in by_urn}
            narrowed = [candidate for candidate in same_day if candidate.post_id in urn_ids]
            return narrowed[0].post_id if len(narrowed) == 1 else None
    return by_urn[0].post_id if len(by_urn) == 1 else None


def match_rows(rows: Sequence[ExportRow], candidates: Sequence[Candidate]) -> MatchResult:
    """Match export rows to live posts: by date first, then by URN (module docs)."""
    by_day: dict[date, list[Candidate]] = {}
    for candidate in candidates:
        by_day.setdefault(candidate.day, []).append(candidate)
    matched: dict[str, ExportRow] = {}
    counts: dict[str, int] = {}
    unmatched = 0
    for row in rows:
        post_id = _match_one(row, by_day, candidates)
        if post_id is None:
            unmatched += 1
            continue
        prior = matched.get(post_id)
        matched[post_id] = row if prior is None else prior.merged(row)
        counts[post_id] = counts.get(post_id, 0) + 1
    return MatchResult(matched=matched, row_counts=counts, unmatched=unmatched)


# --------------------------------------------------------------------------------------------
# Importing
# --------------------------------------------------------------------------------------------

MAX_CANDIDATES: Final = 1000
URN_ONLY_WINDOW: Final = timedelta(days=400)
"""Rows without dates are matched against posts of this many days (URN only)."""


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    stored: Stored[AnalyticsImport]
    created: bool
    """False when the same bytes were imported before (the earlier import is returned)."""


def import_id_for(sha256: str) -> str:
    """The import of one exact file (re-uploading the same bytes finds it)."""
    return derived_id("imp", sha256)


def _local_day(stamp: str, zone: ZoneInfo) -> date:
    return parse_iso(stamp).astimezone(zone).date()


async def _candidates(
    store: Store, rows: Sequence[ExportRow], *, zone: ZoneInfo, now: datetime
) -> list[Candidate]:
    days = [row.day for row in rows if row.day is not None]
    if days and len(days) == len(rows):
        start = datetime.combine(min(days) - timedelta(days=2), time.min, tzinfo=UTC)
        end = datetime.combine(max(days) + timedelta(days=3), time.min, tzinfo=UTC)
    else:
        start, end = now - URN_ONLY_WINDOW, now + timedelta(days=1)
    found = await find_docs(
        store,
        Post,
        [
            Filter("status", "==", PostStatus.POSTED.value),
            Filter("postedAt", ">=", to_iso(start)),
            Filter("postedAt", "<", to_iso(end)),
        ],
        order_by=OrderBy("postedAt", descending=True),
        limit=MAX_CANDIDATES,
    )
    out: list[Candidate] = []
    for stored in found:
        post = stored.doc
        if post.posted_at is None:
            continue
        out.append(
            Candidate(
                post_id=post.id,
                day=_local_day(post.posted_at, zone),
                ids=linkedin_ids(post.linkedin_urn) | linkedin_ids(post.post_url),
            )
        )
    return out


def _same_numbers(left: MetricsSnapshot, right: MetricsSnapshot) -> bool:
    return all(getattr(left, name) == getattr(right, name) for name in _METRICS)


async def _apply(
    store: Store, post_id: str, row: ExportRow, *, import_id: str, now: datetime
) -> bool:
    """Put the import snapshot on one post; whether the post holds it afterwards."""
    stamp = to_iso(now)
    holds = False

    def mutate(post: Post) -> Post | None:
        nonlocal holds
        holds = False
        if post.status is not PostStatus.POSTED:
            return None
        snapshot = MetricsSnapshot(
            id=import_id,
            taken_at=stamp,
            offset=offset_for(post.posted_at, stamp),
            impressions=row.impressions,
            reactions=row.reactions,
            comments=row.comments,
            reposts=row.reposts,
            saves=row.saves,
            sends=row.sends,
            source="import",
            import_id=import_id,
        )
        earlier = next((item for item in post.snapshots if item.id == import_id), None)
        if earlier is not None and _same_numbers(earlier, snapshot):
            holds = True  # a retried import already put it there
            return None
        try:
            changed = with_snapshot(post, snapshot, now=stamp, evict_imports=True)
        except SnapshotRefused:
            return None
        holds = True
        return changed

    try:
        await update_doc(store, Post, post_id, mutate)
    except NotFound:
        return False
    return holds


async def import_export(
    store: Store,
    data: bytes,
    *,
    content_type: str,
    filename: str | None,
    now: datetime,
) -> ImportOutcome:
    """Parse an export, match its rows to live posts and add the snapshots (module docs).

    Raises :class:`ExportFormatError`. The import document is written last, so a retry after a
    crash re-applies the same snapshots (same id) and then records the import.
    """
    if not data:
        raise ExportFormatError("unknown_export_format", "The file is empty.")
    kind = export_kind(data, content_type)
    digest = sha256_hex(data)
    import_id = import_id_for(digest)
    try:
        return ImportOutcome(await read_doc(store, AnalyticsImport, import_id), created=False)
    except NotFound:
        pass
    rows = await asyncio.to_thread(parse_export, data, kind=kind)
    stamp = to_iso(now)
    cadence, _ = await read_singleton(store, CadenceSettings, now=stamp)
    zone = ZoneInfo(cadence.timezone)
    result = match_rows(rows, await _candidates(store, rows, zone=zone, now=now))
    applied = 0
    for post_id in sorted(result.matched):
        if await _apply(store, post_id, result.matched[post_id], import_id=import_id, now=now):
            applied += result.row_counts[post_id]
    record = AnalyticsImport(
        id=import_id,
        created_at=stamp,
        updated_at=stamp,
        filename=clean_filename(filename),
        sha256=digest,
        rows=len(rows),
        matched=applied,
        unmatched=len(rows) - applied,
        imported_at=stamp,
    )
    try:
        return ImportOutcome(await create_doc(store, record), created=True)
    except Conflict:
        return ImportOutcome(await read_doc(store, AnalyticsImport, import_id), created=False)
