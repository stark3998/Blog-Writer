"""Where Playwright keeps Chrome Headless Shell, and whether it is installed.

Mirrors the Playwright driver's own lookup (``registryDirectory`` in playwright-core):
``PLAYWRIGHT_BROWSERS_PATH`` when set (``0`` means the package-local ``.local-browsers``, a relative
path is resolved against the working directory), otherwise the per-user cache
(``~/Library/Caches/ms-playwright`` on macOS, ``$XDG_CACHE_HOME`` or ``~/.cache`` on Linux,
``%LOCALAPPDATA%`` on Windows). The revision comes from the installed driver's ``browsers.json``,
so a Playwright upgrade without ``playwright install`` reads as "not installed".

The driver is started with a throwaway ``HOME``, so the render session always passes the
resolved directory to it explicitly (:func:`playwright_browsers_path`).

Every function here does blocking file-system work: call them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError

__all__ = [
    "HEADLESS_SHELL",
    "ChromiumLocation",
    "headless_shell_revision",
    "locate_headless_shell",
    "playwright_browsers_path",
]

HEADLESS_SHELL: Final = "chromium-headless-shell"
_EXECUTABLE_GLOBS: Final = (
    "chrome-headless-shell-*/chrome-headless-shell",
    "chrome-headless-shell-*/chrome-headless-shell.exe",
    "chrome-*/headless_shell",
)


@dataclass(frozen=True, slots=True)
class ChromiumLocation:
    browsers_path: Path
    """The directory the driver will search (passed to it as ``PLAYWRIGHT_BROWSERS_PATH``)."""
    revision: str | None
    """The headless-shell revision this Playwright build expects, if known."""
    executable: Path | None
    """The installed executable, or ``None`` when it is missing."""

    @property
    def install_dir(self) -> Path | None:
        if self.revision is None:
            return None
        return self.browsers_path / f"chromium_headless_shell-{self.revision}"


def _driver_package() -> Path:
    return Path(str(resources.files("playwright").joinpath("driver", "package")))


def playwright_browsers_path(environ: Mapping[str, str] | None = None) -> Path:
    """The absolute browser registry directory the Playwright driver uses for this process."""
    env = os.environ if environ is None else environ
    value = env.get("PLAYWRIGHT_BROWSERS_PATH")
    if value == "0":
        return _driver_package() / ".local-browsers"
    if value:
        path = Path(value)
        if not path.is_absolute():
            path = Path(env.get("INIT_CWD") or Path.cwd()) / path
        return path.resolve()
    home = Path(env.get("HOME") or Path.home())
    if sys.platform == "darwin":
        cache = home / "Library" / "Caches"
    elif sys.platform == "win32":
        cache = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    else:
        cache = Path(env.get("XDG_CACHE_HOME") or home / ".cache")
    return (cache / "ms-playwright").resolve()


class _BrowserEntry(BaseModel):
    name: str
    revision: str


class _BrowsersJson(BaseModel):
    browsers: list[_BrowserEntry]


def headless_shell_revision() -> str | None:
    """The ``chromium-headless-shell`` revision pinned by the installed Playwright driver."""
    try:
        raw = (_driver_package() / "browsers.json").read_text(encoding="utf-8")
        data = _BrowsersJson.model_validate_json(raw)
    except (OSError, ValidationError):
        return None
    return next((b.revision for b in data.browsers if b.name == HEADLESS_SHELL), None)


def locate_headless_shell(environ: Mapping[str, str] | None = None) -> ChromiumLocation:
    """Find the installed Chrome Headless Shell for this Playwright build."""
    browsers_path = playwright_browsers_path(environ)
    revision = headless_shell_revision()
    executable: Path | None = None
    if revision is not None:
        install_dir = browsers_path / f"chromium_headless_shell-{revision}"
        for pattern in _EXECUTABLE_GLOBS:
            for candidate in sorted(install_dir.glob(pattern)):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    executable = candidate
                    break
            if executable is not None:
                break
    return ChromiumLocation(browsers_path=browsers_path, revision=revision, executable=executable)
