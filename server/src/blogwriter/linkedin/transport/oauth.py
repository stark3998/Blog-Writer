"""Connecting LinkedIn: OAuth 2.0 authorization code flow as a top-level redirect.

1. ``POST /api/linkedin/oauth/start`` makes a random ``state`` (a secret), stores
   :class:`~blogwriter.domain.ephemeral.OAuthState` under the state's SHA-256 (container
   ``ephemeral``, ``expiresAt`` 10 minutes) and returns the authorize URL; the SPA navigates to
   it top-level (no popup, no iframe).
2. LinkedIn sends the browser back to ``GET /api/linkedin/oauth/callback?code&state``. The
   callback consumes the state exactly once (a CAS delete with the etag it read; ``expiresAt``
   checked in code, never the TTL), exchanges the code, reads the member from ``userinfo`` and
   stores the vault-encrypted token (:func:`.tokens.store_connection`). It always answers
   ``303 Location: /settings/integrations`` and reflects nothing from the request; what went
   wrong lands in ``connection.lastError`` as a code, **but only for a flow the owner started**
   (a state this app issued, consumed now, perhaps expired). A missing, unknown or replayed
   state writes nothing (it is a GET: a cross-site navigation must not change stored state,
   security rule 1.9.1); it is only logged.

Scopes are the constant :data:`~blogwriter.domain.settings.LINKEDIN_SCOPES`; nothing else is ever
requested. The code, the state and the token are never logged (the access log records the route
template only, so query strings never reach a log line).
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal
from urllib.parse import quote, urlencode

from blogwriter.db import NotFound, PreconditionFailed, Store
from blogwriter.domain.docstore import create_doc, delete_doc, read_doc
from blogwriter.domain.ephemeral import OAuthState
from blogwriter.domain.ports import Services
from blogwriter.domain.settings import LINKEDIN_SCOPES
from blogwriter.linkedin.transport.client import AUTHORIZE_URL, LinkedInClient, LinkedInError
from blogwriter.linkedin.transport.tokens import record_connection_error, store_connection

__all__ = [
    "RETURN_PATH",
    "SCOPE_PARAM",
    "CallbackParams",
    "StateCheck",
    "authorize_url",
    "check_state",
    "complete_oauth",
    "consume_state",
    "start_oauth",
]

RETURN_PATH: Final = "/settings/integrations"
"""Where the callback always sends the browser (``OAuthState.return_path`` is this constant)."""
SCOPE_PARAM: Final = " ".join(LINKEDIN_SCOPES)
_STATE_RE: Final = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_CODE_RE: Final = re.compile(r"^[\x21-\x7e]{1,4096}$")
_POSTING_SCOPE: Final = "w_member_social"

_logger: Final = logging.getLogger("blogwriter.linkedin.oauth")


def authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """LinkedIn's authorize URL for the constant scopes."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": SCOPE_PARAM,
        },
        quote_via=quote,
    )
    return f"{AUTHORIZE_URL}?{query}"


async def start_oauth(store: Store, *, client_id: str, redirect_uri: str, now: datetime) -> str:
    """Store a fresh single-use state and return the authorize URL."""
    state = secrets.token_urlsafe(32)
    await create_doc(store, OAuthState.issue(state, now=now))
    return authorize_url(client_id=client_id, redirect_uri=redirect_uri, state=state)


@dataclass(frozen=True, slots=True)
class CallbackParams:
    """What the callback takes from its query string. Malformed values read as missing, so
    nothing odd is ever stored, logged or echoed."""

    code: str | None = field(default=None, repr=False)
    state: str | None = field(default=None, repr=False)
    error: bool = False

    @classmethod
    def parse(
        cls, code: Sequence[str], state: Sequence[str], error: Sequence[str]
    ) -> CallbackParams:
        """Each argument is every value of that query parameter; a repeated one is refused."""
        one_code = code[0] if len(code) == 1 and _CODE_RE.fullmatch(code[0]) else None
        one_state = state[0] if len(state) == 1 and _STATE_RE.fullmatch(state[0]) else None
        return cls(code=one_code, state=one_state, error=bool(error))


type StateCheck = Literal["valid", "expired", "unknown"]


async def check_state(store: Store, state: str, *, now: datetime) -> StateCheck:
    """Consume the stored state (CAS delete with the etag read): ``valid`` (it existed, nobody
    consumed it first, not expired), ``expired`` (ours, consumed now, too old) or ``unknown``
    (never issued, or consumed already: nothing was ours to consume)."""
    doc_id = OAuthState.id_for(state)
    try:
        stored = await read_doc(store, OAuthState, doc_id)
    except NotFound:
        return "unknown"
    try:
        await delete_doc(store, OAuthState, doc_id, if_match=stored.etag)
    except (NotFound, PreconditionFailed):
        return "unknown"
    return "expired" if stored.doc.expired(now) else "valid"


async def consume_state(store: Store, state: str, *, now: datetime) -> bool:
    """Delete the stored state and say whether it was valid (:func:`check_state`)."""
    return await check_state(store, state, now=now) == "valid"


async def _failed(store: Store, now: datetime, code: str) -> str:
    _logger.warning("linkedin_oauth_failed", extra={"error_code": code})
    await record_connection_error(store, now=now, code=code)
    return code


async def complete_oauth(
    services: Services,
    client: LinkedInClient | None,
    params: CallbackParams,
    *,
    redirect_uri: str | None,
    now: datetime,
) -> str:
    """Finish the flow; ``connected`` or the error code recorded on the connection.

    The state is consumed before anything else (an invalid, expired or replayed state stops
    here without touching the code). Codes: ``oauth_state_invalid``, ``oauth_denied``,
    ``oauth_code_missing``, ``linkedin_not_configured``, ``vault_unavailable``,
    ``oauth_exchange_failed``, ``oauth_scope_missing``, ``oauth_userinfo_failed``.
    """
    store = services.store
    checked: StateCheck = (
        "unknown" if params.state is None else await check_state(store, params.state, now=now)
    )
    if checked == "unknown":
        # Not a flow this app started (or a replay): log it, never write (a GET).
        _logger.warning("linkedin_oauth_failed", extra={"error_code": "oauth_state_invalid"})
        return "oauth_state_invalid"
    if checked == "expired":
        return await _failed(store, now, "oauth_state_invalid")
    if params.error:
        return await _failed(store, now, "oauth_denied")
    if params.code is None:
        return await _failed(store, now, "oauth_code_missing")
    if client is None or not client.settings.configured or redirect_uri is None:
        return await _failed(store, now, "linkedin_not_configured")
    vault = services.vault
    if vault is None:
        return await _failed(store, now, "vault_unavailable")
    try:
        grant = await client.exchange_code(params.code, redirect_uri)
    except LinkedInError:
        return await _failed(store, now, "oauth_exchange_failed")
    if grant.scopes and _POSTING_SCOPE not in grant.scopes:
        return await _failed(store, now, "oauth_scope_missing")
    try:
        member_urn = await client.userinfo(grant.access_token)
    except LinkedInError:
        return await _failed(store, now, "oauth_userinfo_failed")
    await store_connection(store, vault, grant=grant, member_urn=member_urn, now=now)
    return "connected"
