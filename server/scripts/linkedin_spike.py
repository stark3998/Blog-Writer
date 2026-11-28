"""Phase 0 LIVE test for LinkedIn posting (text post + PDF document post), DRY-RUN BY DEFAULT.

Dry run (default) prints the exact requests the live test would send. It makes no network calls.

    uv run python scripts/linkedin_spike.py

Live run: creates two real posts on the owner's profile, prints their URNs, then deletes them. It
needs the env vars below, plus the exact phrase ``post and delete`` typed at the prompt::

    LINKEDIN_ACCESS_TOKEN=... LINKEDIN_PERSON_URN=urn:li:person:... \\
        uv run --group live python scripts/linkedin_spike.py --live

Rules this script follows (v2 plan, LinkedIn Studio > Go-live mode 1):
- A POST to /rest/posts is never retried. A 5xx can still have created the post, so the script
  stops and reports instead.
- Documents use initializeUpload -> PUT -> poll status until AVAILABLE (bounded) -> post with
  content.media {id, title}.
- Every request sends Linkedin-Version and X-Restli-Protocol-Version 2.0.0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from blogwriter.linkedin.littletext import Hashtag, Text, api_commentary, display_text

API = "https://api.linkedin.com/rest"
LINKEDIN_VERSION = "202611"
CONFIRM_PHRASE = "post and delete"
DOCUMENT_TITLE = "Blog Writer v2 spike (deleted right after)"
POLL_ATTEMPTS = 10
POLL_SECONDS = 3.0


@dataclass
class Request:
    method: str
    url: str
    headers: dict[str, str]
    json_body: dict[str, Any] | None = None
    content: bytes | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=lambda: {})

    def describe(self) -> str:
        shown = {k: ("Bearer ***" if k == "Authorization" else v) for k, v in self.headers.items()}
        lines = [f"{self.method} {self.url}", f"  headers: {json.dumps(shown)}"]
        if self.json_body is not None:
            lines.append("  json: " + json.dumps(self.json_body, ensure_ascii=False, indent=2))
        if self.content is not None:
            lines.append(f"  body: <{len(self.content)} bytes>")
        if self.note:
            lines.append(f"  note: {self.note}")
        return "\n".join(lines)


def headers(token: str, *, method: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    if method:
        h["X-RestLi-Method"] = method
    return h


def spike_text_tokens() -> list[Text | Hashtag]:
    """Every reserved character, 'C#', and one approved hashtag."""
    return [
        Text(
            "Phase 0 escaping test (deleted right after): | { } @ [ ] ( ) < > # \\ * _ ~\n"
            "We write C# and use snake_case_names and (parentheses).\n\n"
        ),
        Hashtag("AISecurity"),
    ]


def minimal_pdf(pages: int = 3) -> bytes:
    """A tiny valid PDF with ``pages`` pages of 1080x1350 pt (no third-party deps)."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    font_id = 3 + pages * 2
    for i in range(pages):
        content_id = 4 + i * 2
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1080 1350] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = f"BT /F1 64 Tf 120 700 Td (Spike slide {i + 1} of {pages}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def post_body(author: str, commentary: str, document_urn: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "author": author,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if document_urn:
        body["content"] = {"media": {"title": DOCUMENT_TITLE, "id": document_urn}}
    return body


def plan(token: str, author: str) -> list[Request]:
    tokens = spike_text_tokens()
    commentary = api_commentary(tokens)
    pdf = minimal_pdf()
    return [
        Request(
            "POST",
            f"{API}/posts",
            headers(token),
            post_body(author, commentary),
            note=f"text post; display text is {display_text(tokens)!r}",
        ),
        Request(
            "POST",
            f"{API}/documents?action=initializeUpload",
            headers(token),
            {"initializeUploadRequest": {"owner": author}},
            note="returns value.uploadUrl and value.document (urn:li:document:...)",
        ),
        Request(
            "PUT",
            "<value.uploadUrl>",
            {"Authorization": f"Bearer {token}"},
            content=pdf,
            note="upload the PDF bytes",
        ),
        Request(
            "GET",
            f"{API}/documents/<url-encoded document urn>",
            headers(token),
            note=f"poll until status == AVAILABLE (max {POLL_ATTEMPTS} x {POLL_SECONDS}s)",
        ),
        Request(
            "POST",
            f"{API}/posts",
            headers(token),
            post_body(
                author,
                api_commentary([Text("Document post spike, deleted right after.")]),
                "<document urn>",
            ),
            note="document post; never retried",
        ),
        Request(
            "DELETE",
            f"{API}/posts/<url-encoded x-restli-id>",
            headers(token, method="DELETE"),
            note="for each created post; idempotent, 204 expected",
        ),
    ]


def run_live(token: str, author: str) -> int:  # pragma: no cover - needs a real token
    import httpx  # imported lazily: dry runs never touch the network stack

    created: list[str] = []
    with httpx.Client(timeout=30.0) as client:  # no retries configured, on purpose
        try:
            text = spike_text_tokens()
            r = client.post(
                f"{API}/posts", headers=headers(token), json=post_body(author, api_commentary(text))
            )
            print("text post:", r.status_code, r.headers.get("x-restli-id"))
            if r.status_code != 201:
                print("STOP: text post not created (never retried):", r.text[:500])
                return 1
            created.append(r.headers["x-restli-id"])

            r = client.post(
                f"{API}/documents?action=initializeUpload",
                headers=headers(token),
                json={"initializeUploadRequest": {"owner": author}},
            )
            r.raise_for_status()
            value = r.json()["value"]
            upload = client.put(
                value["uploadUrl"],
                content=minimal_pdf(),
                headers={"Authorization": f"Bearer {token}"},
            )
            print("document upload:", upload.status_code)
            document = value["document"]
            status = "UNKNOWN"
            for _ in range(POLL_ATTEMPTS):
                g = client.get(
                    f"{API}/documents/{quote(document, safe='')}", headers=headers(token)
                )
                status = (
                    g.json().get("status", f"HTTP {g.status_code}")
                    if g.is_success
                    else (f"HTTP {g.status_code}")
                )
                print("document status:", status)
                if status == "AVAILABLE" or not g.is_success:
                    break
                time.sleep(POLL_SECONDS)
            if status not in ("AVAILABLE",):
                print("note: status polling did not report AVAILABLE; posting after a fixed wait")
                time.sleep(POLL_SECONDS * 2)
            r = client.post(
                f"{API}/posts",
                headers=headers(token),
                json=post_body(
                    author,
                    api_commentary([Text("Document post spike, deleted right after.")]),
                    document,
                ),
            )
            print("document post:", r.status_code, r.headers.get("x-restli-id"))
            if r.status_code == 201:
                created.append(r.headers["x-restli-id"])
            else:
                print("STOP: document post not created (never retried):", r.text[:500])
        finally:
            for urn in created:
                d = client.delete(
                    f"{API}/posts/{quote(urn, safe='')}", headers=headers(token, method="DELETE")
                )
                print("delete", urn, "->", d.status_code)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="really post, then delete")
    args = parser.parse_args(argv)

    if not args.live:
        print("DRY RUN (no network). Requests the live test would send:\n")
        for request in plan("<token>", "urn:li:person:<id>"):
            print(request.describe(), end="\n\n")
        return 0

    token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
    author = os.environ.get("LINKEDIN_PERSON_URN", "")
    if not token or not author.startswith("urn:li:person:"):
        print("Set LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_URN (urn:li:person:...).")
        return 2
    typed = input(f"This creates 2 PUBLIC posts and deletes them. Type '{CONFIRM_PHRASE}': ")
    if typed.strip() != CONFIRM_PHRASE:
        print("Not confirmed; nothing sent.")
        return 3
    return run_live(token, author)


if __name__ == "__main__":
    sys.exit(main())
