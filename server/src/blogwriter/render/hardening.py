"""Compensating control for running Chromium without its own sandbox.

Azure Container Apps does not grant the user namespaces Chromium's sandbox needs, so production
runs with ``BW_RENDER_CHROMIUM_SANDBOX=0``. A compromised renderer then runs as the app's uid.
The other isolation layers stay (minimal environment without ``IDENTITY_*``/``AZURE_*``/``BW_*``,
a throwaway home, no network, ``--jitless``, JavaScript off outside the Mermaid context).

This module adds the remaining control from the plan: ``prctl(PR_SET_DUMPABLE, 0)`` on the app
process, before any driver starts. A non-dumpable process cannot be ptrace-attached by other
processes of the same uid, its ``/proc/<pid>/{mem,environ,maps}`` become unreadable to them (that
is where the managed-identity endpoint secret, access tokens and keys live), and it writes no
core dumps. ``execve`` resets the flag, so the driver and Chromium are unaffected.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import Final

__all__ = ["harden_process", "is_dumpable"]

_PR_GET_DUMPABLE: Final = 3
_PR_SET_DUMPABLE: Final = 4


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


def is_dumpable() -> bool | None:
    """The process's dumpable flag on Linux; ``None`` elsewhere."""
    if not sys.platform.startswith("linux"):
        return None
    return bool(_libc().prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0))


def harden_process() -> bool:
    """Make this process non-dumpable. ``True`` when applied; ``False`` off Linux.

    Raises :class:`OSError` when the call fails on Linux: the caller must then refuse to run
    Chromium without its sandbox.
    """
    if not sys.platform.startswith("linux"):
        return False
    libc = _libc()
    if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, "prctl(PR_SET_DUMPABLE, 0) failed")
    if libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(0, "the process is still dumpable after prctl(PR_SET_DUMPABLE, 0)")
    return True
