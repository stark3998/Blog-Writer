"""Serves the built SPA (``BW_SPA_DIR``) with deep-link fallback and cache headers.

* The directory is indexed once when the app is built. Only regular, non-hidden files found
  then are served; request paths are looked up in that index and never joined onto the
  filesystem, so path traversal is impossible by construction. Symlinks are not served.
* ``assets/`` holds Vite's content-hashed output and is cached for a year
  (``private, max-age=31536000, immutable``). Every other file, ``index.html`` included,
  is ``no-cache`` and revalidates with its ETag (``If-None-Match`` gets a 304).
* Any other path outside ``/api`` gets ``index.html`` (client-side routing / deep links).
  A missing ``assets/`` file is a 404, and so is any unknown ``/api`` path.
* Content types come from a fixed table (never the platform MIME database, which differs
  between images); unknown extensions are served as ``application/octet-stream``.

The SPA is behind the same authentication as the API (:mod:`blogwriter.core.auth`).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import FileResponse, Response

from blogwriter.core.errors import problem_response
from blogwriter.core.security_headers import is_api_path

__all__ = [
    "ASSET_CACHE_CONTROL",
    "DOCUMENT_CACHE_CONTROL",
    "HASHED_ASSET_DIR",
    "SpaFiles",
]

#: Directory holding content-hashed build output (Vite's ``build.assetsDir``).
HASHED_ASSET_DIR: Final = "assets"
ASSET_CACHE_CONTROL: Final = "private, max-age=31536000, immutable"
DOCUMENT_CACHE_CONTROL: Final = "no-cache"
INDEX_FILE: Final = "index.html"
MAX_SPA_FILES: Final = 10_000

_MEDIA_TYPES: Final = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".wasm": "application/wasm",
}
_FALLBACK_MEDIA_TYPE: Final = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class _SpaFile:
    path: Path
    stat_result: os.stat_result
    media_type: str
    cache_control: str
    etag: str


def _index_directory(root: Path) -> dict[str, _SpaFile]:
    files: dict[str, _SpaFile] = {}
    for directory, dirnames, filenames in root.walk(follow_symlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full_path = directory / name
            stat_result = full_path.lstat()
            if not stat.S_ISREG(stat_result.st_mode):
                continue  # symlinks, sockets, devices
            relative = full_path.relative_to(root).as_posix()
            immutable = relative.startswith(f"{HASHED_ASSET_DIR}/")
            files[relative] = _SpaFile(
                path=full_path,
                stat_result=stat_result,
                media_type=_MEDIA_TYPES.get(full_path.suffix.lower(), _FALLBACK_MEDIA_TYPE),
                cache_control=ASSET_CACHE_CONTROL if immutable else DOCUMENT_CACHE_CONTROL,
                etag=f'"{stat_result.st_size:x}-{stat_result.st_mtime_ns:x}"',
            )
            if len(files) > MAX_SPA_FILES:
                raise ValueError(f"SPA directory has more than {MAX_SPA_FILES} files: {root}")
    return files


def _etag_matches(if_none_match: str, etag: str) -> bool:
    if if_none_match.strip() == "*":
        return True
    return any(tag.strip().removeprefix("W/") == etag for tag in if_none_match.split(","))


class SpaFiles:
    """The SPA build in ``directory``; :meth:`router` serves it (register it last)."""

    def __init__(self, directory: Path) -> None:
        root = directory.resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(f"SPA directory is not a directory: {root}")
        self._files = _index_directory(root)
        if INDEX_FILE not in self._files:
            raise FileNotFoundError(f"SPA directory has no {INDEX_FILE}: {root}")
        self.root = root

    @property
    def paths(self) -> frozenset[str]:
        """Relative paths of every file that can be served."""
        return frozenset(self._files)

    def router(self) -> APIRouter:
        """A catch-all ``GET``/``HEAD`` route. Include it after every API router."""
        router = APIRouter()
        router.add_api_route(
            "/{path:path}",
            self.serve,
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name="spa",
        )
        return router

    async def serve(self, request: Request, path: str) -> Response:
        if is_api_path(request.url.path):
            return problem_response(404)
        relative = path.removeprefix("/") or INDEX_FILE
        spa_file = self._files.get(relative)
        if spa_file is None:
            if relative.startswith(f"{HASHED_ASSET_DIR}/"):
                return problem_response(404)
            spa_file = self._files[INDEX_FILE]
        headers = {"Cache-Control": spa_file.cache_control, "ETag": spa_file.etag}
        if_none_match = request.headers.get("if-none-match")
        if if_none_match is not None and _etag_matches(if_none_match, spa_file.etag):
            return Response(status_code=304, headers=headers)
        return FileResponse(
            spa_file.path,
            stat_result=spa_file.stat_result,
            media_type=spa_file.media_type,
            headers=headers,
        )
