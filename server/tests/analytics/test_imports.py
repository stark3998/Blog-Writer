"""The beta LinkedIn export import: the tolerant parser (golden CSV, an xlsx workbook with
side-by-side tables and a key-value sheet), matching by date first, then URN, and the route."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from openpyxl import Workbook

from blogwriter.analytics.imports import (
    Candidate,
    ExportFormatError,
    ExportRow,
    clean_filename,
    export_kind,
    import_id_for,
    linkedin_ids,
    match_rows,
    parse_export,
    rows_from_grid,
)
from blogwriter.analytics.routes import import_settings
from blogwriter.analytics.settings import AnalyticsSettings
from blogwriter.analytics.testing import posted_post, snapshot
from blogwriter.domain.analytics import AnalyticsImport
from blogwriter.domain.base import sha256_hex
from blogwriter.domain.docstore import find_docs, read_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.testing import FakeServices

GOLDENS = Path(__file__).parent / "goldens"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
URL_1 = "https://www.linkedin.com/feed/update/urn:li:activity:7390000000000000001"
URL_2 = "https://www.linkedin.com/feed/update/urn:li:share:7390000000000000002"
URL_3 = "https://www.linkedin.com/posts/member_agents-activity-7390000000000000003-AbCd"


def golden_csv() -> bytes:
    return (GOLDENS / "linkedin_export.csv").read_bytes()


def row_json(row: ExportRow) -> dict[str, object]:
    return {
        "day": row.day.isoformat() if row.day is not None else None,
        "ids": sorted(row.ids),
        "impressions": row.impressions,
        "reactions": row.reactions,
        "comments": row.comments,
        "reposts": row.reposts,
        "saves": row.saves,
        "sends": row.sends,
    }


def workbook_bytes() -> bytes:
    """Daily-total sheets (skipped), side-by-side top-post tables and a one-post sheet."""
    book = Workbook()
    discovery = book.active
    assert discovery is not None
    discovery.title = "DISCOVERY"
    discovery.append(["Overall Performance", "11/01/2026 - 11/25/2026"])
    discovery.append(["Impressions", 12345])
    discovery.append(["Members reached", 6789])
    engagement = book.create_sheet("ENGAGEMENT")
    engagement.append(["Date", "Impressions", "Engagements"])
    engagement.append([date(2026, 11, 2), 500, 20])
    engagement.append([date(2026, 11, 4), 900, 31])
    top = book.create_sheet("TOP POSTS")
    top.append(["Maximum of 50 posts available to include in this list"])
    top.append([])
    top.append(
        [
            "Post URL",
            "Post publish date",
            "Reactions",
            "Comments",
            None,
            "Post URL",
            "Post publish date",
            "Impressions",
        ]
    )
    top.append([URL_1, date(2026, 11, 2), 40, 10, None, URL_3, date(2026, 11, 4), 3000])
    top.append([URL_3, "11/04/2026", 25, 5, None, URL_1, datetime(2026, 11, 2, 9, 1), 2000])
    single = book.create_sheet("PERFORMANCE")
    for label, value in (
        ("Post URL", URL_2),
        ("Post Date", "11/04/2026 9:01 AM"),
        ("Impressions", "1,000"),
        ("Members reached", 800),
        ("Reactions", 10),
        ("Comments", 4),
        ("Reposts", 0),
        ("Saves", 1),
        ("Sends on LinkedIn", 0),
    ):
        single.append([label, value])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------------------------


def test_the_documented_csv_layout_parses_to_the_golden_rows() -> None:
    rows = parse_export(golden_csv(), kind="csv")
    golden = json.loads((GOLDENS / "linkedin_export_rows.json").read_text(encoding="utf-8"))
    assert [row_json(row) for row in rows] == golden


def test_csv_variants_semicolons_bom_and_windows_encoding() -> None:
    text = "Post URL;Date;Impressions;Likes\n" + f"{URL_2};2026-11-04;1 200;7\n"
    semicolons = parse_export(b"\xef\xbb\xbf" + text.encode("utf-8"), kind="csv")
    assert [(row.impressions, row.reactions) for row in semicolons] == [(1200, 7)]
    utf16 = parse_export(text.replace(";", "\t").encode("utf-16"), kind="csv")
    assert [row.reactions for row in utf16] == [7]
    windows = parse_export(
        ("Post URL,Date,Impressions\n" + f"{URL_2},11/04/2026,1000\n").encode("cp1252")
        + b"\x93,,\n",
        kind="csv",
    )
    assert [row.impressions for row in windows] == [1000]


def test_a_workbook_with_side_by_side_tables_and_a_key_value_sheet() -> None:
    rows = parse_export(workbook_bytes(), kind="xlsx")
    found = {tuple(sorted(row.ids)): row_json(row) for row in rows}
    assert found == {
        ("7390000000000000001",): {
            "day": "2026-11-02",
            "ids": ["7390000000000000001"],
            "impressions": 2000,
            "reactions": 40,
            "comments": 10,
            "reposts": None,
            "saves": None,
            "sends": None,
        },
        ("7390000000000000003",): {
            "day": "2026-11-04",
            "ids": ["7390000000000000003"],
            "impressions": 3000,
            "reactions": 25,
            "comments": 5,
            "reposts": None,
            "saves": None,
            "sends": None,
        },
        ("7390000000000000002",): {
            "day": "2026-11-04",
            "ids": ["7390000000000000002"],
            "impressions": 1000,
            "reactions": 10,
            "comments": 4,
            "reposts": 0,
            "saves": 1,
            "sends": 0,
        },
    }


def test_unknown_layouts_and_files_are_refused() -> None:
    daily = b"Date,Impressions,Engagements\n11/02/2026,500,20\n"
    with pytest.raises(ExportFormatError) as no_url:
        parse_export(daily, kind="csv")
    assert (no_url.value.code, no_url.value.status) == ("unknown_export_format", 422)
    with pytest.raises(ExportFormatError) as not_a_book:
        parse_export(b"PK\x03\x04 not really a zip", kind="xlsx")
    assert not_a_book.value.code == "unknown_export_format"
    with pytest.raises(ExportFormatError) as binary:
        parse_export(b"Post URL,Impressions\n\x00\x00", kind="csv")
    assert binary.value.code == "unknown_export_format"


def test_a_workbook_that_unpacks_too_large_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", b"0" * (41 * 1024 * 1024))
    with pytest.raises(ExportFormatError) as caught:
        parse_export(buffer.getvalue(), kind="xlsx")
    assert (caught.value.code, caught.value.status) == ("too_large", 413)


def test_kind_detection() -> None:
    assert export_kind(b"PK\x03\x04rest", XLSX) == "xlsx"
    assert export_kind(b"PK\x03\x04rest", "application/octet-stream") == "xlsx"
    assert export_kind(b"Post URL,Impressions\n", "text/csv; charset=utf-8") == "csv"
    assert export_kind(b"Post URL,Impressions\n", "") == "csv"
    with pytest.raises(ExportFormatError) as legacy:
        export_kind(b"\xd0\xcf\x11\xe0legacy", "application/vnd.ms-excel")
    assert legacy.value.status == 415
    with pytest.raises(ExportFormatError) as image:
        export_kind(b"\x89PNG", "image/png")
    assert image.value.status == 415
    with pytest.raises(ExportFormatError) as fake_book:
        export_kind(b"Post URL", XLSX)
    assert fake_book.value.status == 422


def test_urn_numbers_and_filenames() -> None:
    assert linkedin_ids(URL_1) == {"7390000000000000001"}
    assert linkedin_ids(URL_3) == {"7390000000000000003"}
    assert linkedin_ids("urn%3Ali%3AugcPost%3A7390000000000000009") == {"7390000000000000009"}
    assert linkedin_ids("https://example.com/7390000000000000001") == frozenset()
    assert linkedin_ids(None) == frozenset()
    assert clean_filename("C:\\Users\\me\\Content_2026.xlsx") == "Content_2026.xlsx"
    assert clean_filename("a/b/\x07report.csv") == "report.csv"
    assert clean_filename("   ") is None


def test_dates_and_counts_are_read_tolerantly() -> None:
    grid: list[list[object]] = [
        ["Post URL", "Date", "Impressions", "Reactions"],
        [URL_1, 46328, 1200.0, "1,234"],  # Excel serial for 2026-11-02
        [URL_2, "Nov 4, 2026", "12.5%", -3],
        [URL_3, "garbage", None, 7],
    ]
    rows = rows_from_grid(grid)
    # The second row has no usable number (a percentage, a negative count): it is skipped.
    assert [(row.day, row.impressions, row.reactions) for row in rows] == [
        (date(2026, 11, 2), 1200, 1234),
        (None, None, 7),
    ]


# --------------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------------


def test_rows_match_by_date_first_then_by_urn() -> None:
    a = Candidate("post-a", date(2026, 11, 2), frozenset({"111"}))
    b = Candidate("post-b", date(2026, 11, 4), frozenset({"222"}))
    c = Candidate("post-c", date(2026, 11, 4), frozenset({"333"}))
    d = Candidate("post-d", date(2026, 11, 10))
    rows = [
        ExportRow(day=date(2026, 11, 2), ids=frozenset({"999"}), impressions=100),  # date: a
        ExportRow(day=date(2026, 11, 4), ids=frozenset({"333"}), impressions=300),  # urn: c
        ExportRow(day=date(2026, 11, 4), impressions=1),  # two posts that day: ambiguous
        ExportRow(day=None, ids=frozenset({"222"}), impressions=200),  # no date: urn b
        ExportRow(day=date(2026, 11, 2), ids=frozenset({"222"}), impressions=5),  # contradicts
        ExportRow(day=date(2026, 11, 15), ids=frozenset({"111"}), reactions=9),  # urn: a
        ExportRow(day=date(2026, 11, 10), impressions=50),  # date: d
        ExportRow(day=date(2026, 11, 20), impressions=7),  # nothing
    ]
    result = match_rows(rows, [a, b, c, d])
    assert {key: (row.impressions, row.reactions) for key, row in result.matched.items()} == {
        "post-a": (100, 9),
        "post-c": (300, None),
        "post-b": (200, None),
        "post-d": (50, None),
    }
    assert result.row_counts == {"post-a": 2, "post-c": 1, "post-b": 1, "post-d": 1}
    assert result.unmatched == 3


# --------------------------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------------------------


async def seed_live_posts(services: FakeServices) -> tuple[str, str, str]:
    a = await posted_post(
        services,
        posted_at="2026-11-02T15:00:00.000000Z",
        linkedin_urn="urn:li:share:7300000000000000001",
    )
    b = await posted_post(
        services,
        posted_at="2026-11-04T14:00:00.000000Z",
        post_url=f"{URL_2}/",
        snapshots=[snapshot(taken_at="2026-11-06T15:00:00.000000Z", offset="h48", impressions=700)],
    )
    c = await posted_post(
        services,
        posted_at="2026-11-04T16:00:00.000000Z",
        linkedin_urn="urn:li:ugcPost:7390000000000000003",
    )
    return a.id, b.id, c.id


async def test_import_route_end_to_end(
    services: FakeServices,
    client: httpx.AsyncClient,
    write_headers: dict[str, str],
    read_headers: dict[str, str],
) -> None:
    services.clock.set(datetime(2026, 11, 9, 16, 0, tzinfo=UTC))
    a, b, c = await seed_live_posts(services)
    body = golden_csv()
    response = await client.post(
        "/api/analytics/imports",
        params={"filename": "C:\\exports\\Content_2026-11.csv"},
        content=body,
        headers={**write_headers, "Content-Type": "text/csv"},
    )
    assert response.status_code == 201, response.text
    item = response.json()["item"]
    import_id = import_id_for(sha256_hex(body))
    assert item["id"] == import_id
    assert (item["rows"], item["matched"], item["unmatched"]) == (4, 3, 1)
    assert item["filename"] == "Content_2026-11.csv"
    assert item["sha256"] == sha256_hex(body)
    snapshots = {
        post_id: (await read_doc(services.store, Post, post_id)).doc.snapshots
        for post_id in (a, b, c)
    }
    imported = {
        post_id: [s for s in items if s.source == "import"] for post_id, items in snapshots.items()
    }
    assert [(s.id, s.offset, s.impressions, s.reactions) for s in imported[a]] == [
        (import_id, "d7", 2000, 40)
    ]
    assert [(s.impressions, s.offset) for s in imported[b]] == [(1000, "other")]
    assert [(s.impressions, s.import_id) for s in imported[c]] == [(3000, import_id)]
    assert len(snapshots[b]) == 2  # the form snapshot stays

    again = await client.post(
        "/api/analytics/imports",
        content=body,
        headers={**write_headers, "Content-Type": "text/csv"},
    )
    assert again.status_code == 200
    assert again.json()["item"]["id"] == import_id
    assert len((await read_doc(services.store, Post, b)).doc.snapshots) == 2
    imports = await find_docs(services.store, AnalyticsImport)
    assert len(imports) == 1
    listed = await client.get("/api/analytics/imports", headers=read_headers)
    assert [entry["item"]["id"] for entry in listed.json()["items"]] == [import_id]


async def test_import_route_refusals(
    app: FastAPI, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    csv_headers = {**write_headers, "Content-Type": "text/csv"}
    image = await client.post(
        "/api/analytics/imports",
        content=b"\x89PNG",
        headers={**write_headers, "Content-Type": "image/png"},
    )
    assert image.status_code == 415
    big = await client.post(
        "/api/analytics/imports", content=b"x" * (64 * 1024 + 1), headers=csv_headers
    )
    assert big.status_code == 413
    unknown = await client.post(
        "/api/analytics/imports",
        content=b"Date,Impressions\n11/02/2026,500\n",
        headers=csv_headers,
    )
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_export_format"
    empty = await client.post("/api/analytics/imports", content=b"", headers=csv_headers)
    assert empty.json()["code"] == "unknown_export_format"
    no_csrf = await client.post(
        "/api/analytics/imports", content=golden_csv(), headers={"Content-Type": "text/csv"}
    )
    assert no_csrf.status_code in (401, 403)
    app.dependency_overrides[import_settings] = AnalyticsSettings
    off = await client.post("/api/analytics/imports", content=golden_csv(), headers=csv_headers)
    assert off.status_code == 503
    assert off.json()["code"] == "import_unavailable"


async def test_a_workbook_upload_matches_posts(
    services: FakeServices, client: httpx.AsyncClient, write_headers: dict[str, str]
) -> None:
    a, b, c = await seed_live_posts(services)
    response = await client.post(
        "/api/analytics/imports",
        content=workbook_bytes(),
        headers={**write_headers, "Content-Type": XLSX},
    )
    assert response.status_code == 201, response.text
    assert (response.json()["item"]["matched"], response.json()["item"]["unmatched"]) == (3, 0)
    post_c = (await read_doc(services.store, Post, c)).doc
    assert [(s.impressions, s.reactions, s.comments) for s in post_c.snapshots] == [(3000, 25, 5)]
    assert a != b


def test_the_openapi_document_declares_the_raw_export_body(app: FastAPI) -> None:
    """The web client is generated from it: the export is a raw .xlsx or .csv body."""
    body = app.openapi()["paths"]["/api/analytics/imports"]["post"]["requestBody"]
    assert body["required"] is True
    content = body["content"]
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in content
    assert {"text/csv", "application/octet-stream"} <= set(content)
    assert "" not in content
    assert all(media == {} for media in content.values())  # any bytes (OpenAPI 3.1)
