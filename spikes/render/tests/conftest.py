from __future__ import annotations

import asyncio
import io
import json
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".ms-playwright"))

from render import DeckSpec, RenderSession  # noqa: E402

PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))


@pytest.fixture(scope="session")
def sample_spec() -> dict[str, object]:
    return json.loads((FIXTURES / "sample_deck.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def sample_deck(sample_spec: dict[str, object]) -> DeckSpec:
    return DeckSpec.model_validate(sample_spec)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def session() -> AsyncIterator[RenderSession]:
    """One Chromium for the tests that do not exercise the per-job lifecycle itself."""
    async with RenderSession() as s:
        yield s


@dataclass
class Canary:
    """A loopback HTTP server that records every request it receives (it should receive none)."""

    port: int = 0
    hits: list[str] = field(default_factory=list)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest_asyncio.fixture(loop_scope="session")
async def canary() -> AsyncIterator[Canary]:
    state = Canary()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            state.hits.append(line.decode("latin-1").strip())
            body = b"\x89PNG\r\n\x1a\n"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: %d\r\n\r\n" % len(body)
            )
            writer.write(body)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()


def pdf_pages_text(pdf: bytes) -> list[str]:
    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages]


def private_use_chars(text: str) -> list[str]:
    return [ch for ch in text if any(lo <= ord(ch) <= hi for lo, hi in PUA_RANGES)]


def png_size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as im:
        assert im.format == "PNG"
        return im.size


@pytest.fixture
def tmp_out(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
