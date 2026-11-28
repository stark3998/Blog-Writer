"""The ``linkedin.introspect`` job (daily, schedule item ``sched-linkedin-introspect``).

Steps:

``introspect``
    Ask LinkedIn whether the member token still works
    (:func:`~blogwriter.linkedin.transport.tokens.introspect_connection`): updates
    ``introspectionActive``, ``expiresAt`` and ``lastIntrospectedAt``; a revoked, expired or
    unreadable token moves the connection to ``needs_reconnect``, which alerts out of band and
    pushes "reconnect". The status before the check is checkpointed, so a resumed step still
    alerts once.
``alerts``
    Token-day alerts (days 53, 57, 59; once each per token) and the API version sunset warning
    (60 days before), recorded in ``alertsSent``. The reconnect banner (from day 50) is computed
    on read (``bannerDue``).
``sweep``
    Posts left ``publishing`` by a process that died mid-attempt become ``needs_attention``
    (:func:`~blogwriter.linkedin.transport.lifecycle.sweep_stale_intents`), in case no restart
    happened since. Then the follow-ups of posts that went live (``planner.post-live``) or were
    scheduled natively (``planner.post-scheduled``) are enqueued again from the posts
    (:func:`~blogwriter.linkedin.transport.hooks.ensure_followups`): the hooks that start them
    are best effort, and a reminder is never lost to a crash at the wrong moment.

No step calls a model, and none can publish: this module never imports the publisher.
"""

from __future__ import annotations

from typing import Final, cast

from pydantic import JsonValue

from blogwriter.domain.jobflow import step_services
from blogwriter.domain.jobtypes import ENTITY_CHANGED, LINKEDIN_INTROSPECT, entity_changed
from blogwriter.domain.states import ConnectionStatus
from blogwriter.jobs import JobRegistry, Step, StepContext
from blogwriter.linkedin.transport.client import current_client
from blogwriter.linkedin.transport.hooks import ensure_followups
from blogwriter.linkedin.transport.lifecycle import sweep_stale_intents
from blogwriter.linkedin.transport.tokens import (
    ALERT_REF,
    CONNECTION_ID,
    INTEGRATIONS_PATH,
    effective_status,
    introspect_connection,
    notify_owner,
    read_connection,
    token_alerts,
)
from blogwriter.notify import emit_alert

__all__ = ["INTROSPECT_STEPS", "alerts_step", "introspect_step", "register_jobs", "sweep_step"]

_QUIET: Final = frozenset({"not_connected", "vault_unavailable", "linkedin_not_configured"})
"""Outcomes that wrote nothing (no ``entity.changed`` event)."""


async def introspect_step(ctx: StepContext) -> JsonValue:
    services = step_services()
    output = ctx.output
    before: str | None = None
    if isinstance(output, dict):
        value = cast(dict[str, JsonValue], output).get("before")
        before = value if isinstance(value, str) else None
    if before is None:
        current = await read_connection(services.store, services.clock.now())
        before = current.status.value
        await ctx.checkpoint({"before": before})
    now = services.clock.now()
    result = await introspect_connection(services, current_client(), now=now)
    after = effective_status(result.connection, now)
    if before == ConnectionStatus.CONNECTED.value and after is ConnectionStatus.NEEDS_RECONNECT:
        emit_alert("needs_attention", ALERT_REF)
        await notify_owner(
            services.notifier,
            title="LinkedIn needs reconnecting: one-click publishing is off",
            url=INTEGRATIONS_PATH,
            tag="linkedin-token",
        )
    if result.outcome not in _QUIET:
        await ctx.emit(ENTITY_CHANGED, entity_changed("linkedin_connection", CONNECTION_ID))
    return {"outcome": result.outcome, "status": after.value}


async def alerts_step(ctx: StepContext) -> JsonValue:
    services = step_services()
    keys = await token_alerts(services, now=services.clock.now())
    if keys:
        await ctx.emit(ENTITY_CHANGED, entity_changed("linkedin_connection", CONNECTION_ID))
    return {"alerts": list[JsonValue](keys)}


async def sweep_step(ctx: StepContext) -> JsonValue:
    services = step_services()
    swept = await sweep_stale_intents(
        services.store, now=services.clock.now(), notifier=services.notifier
    )
    for post_id in swept:
        await ctx.emit(ENTITY_CHANGED, entity_changed("post", post_id))
    followups = await ensure_followups(services, now=services.clock.now())
    return {"swept": len(swept), "followups": followups}


INTROSPECT_STEPS: Final = (
    Step("introspect", introspect_step),
    Step("alerts", alerts_step),
    Step("sweep", sweep_step),
)


def register_jobs(registry: JobRegistry) -> None:
    """Register ``linkedin.introspect`` (the only job type the transport owns)."""
    registry.register(LINKEDIN_INTROSPECT, list(INTROSPECT_STEPS))
