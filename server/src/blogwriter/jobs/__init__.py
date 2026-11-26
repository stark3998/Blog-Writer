"""blogwriter.jobs: durable jobs, the scheduler, the broker and the app-wide SSE stream.

Plan sections: "Reliability model: crash-only, one claim mechanism" and "Weekly production
calendar". See ``docs/v2/phase1/jobs.md``.

Defining a job type::

    from blogwriter.jobs import JobRegistry, Step, StepContext

    async def draft(ctx: StepContext) -> JsonValue:
        response_id = ctx.remote_ids.get("response")
        if response_id is None:
            response_id = await start_background_response(...)
            await ctx.record_remote_id("response", response_id)   # before awaiting it
        text_ref = await poll_until_done(response_id)
        return {"articleId": text_ref}

    registry = JobRegistry()
    registry.register("article.draft", [Step("draft", draft)])

Wiring (``core.app.create_app``)::

    create_app(routers=[jobs.get_router()], lifecycles=[db, jobs.JobsLifecycle(registry)])

Routes and other packages reach the running service through ``app.state.jobs``
(:class:`JobsService`, dependency :func:`get_jobs_service`): ``enqueue``, ``get``, ``cancel``,
``provide_input``, ``retry`` and the ``scheduler``.
"""

from blogwriter.jobs.bootguard import BootGuard, BootReport
from blogwriter.jobs.broker import Broker, BrokerFull, DeltaMessage, JobEventMessage, Subscription
from blogwriter.jobs.claims import Claim, ClaimLost, LeasePolicy, acquire, new_owner
from blogwriter.jobs.clock import SYSTEM_CLOCK, Clock, SystemClock, parse_iso, to_iso
from blogwriter.jobs.lifecycle import (
    JobsLifecycle,
    JobsService,
    default_registry,
    get_jobs_service,
    server_exit_requested,
    shutdown,
    startup,
)
from blogwriter.jobs.models import (
    ACTIVE_STATUSES,
    JOBS_CONTAINER,
    MAX_EVENTS,
    TERMINAL_STATUSES,
    InputRequest,
    Job,
    JobError,
    JobEvent,
    JobStatus,
    StepState,
    StepStatus,
)
from blogwriter.jobs.queue import (
    JobStateError,
    enqueue,
    get_job,
    provide_input,
    request_cancel,
    retry_job,
)
from blogwriter.jobs.runner import (
    JobFailed,
    JobRegistry,
    JobRunner,
    JobType,
    QuarantineHook,
    QuarantineNotice,
    RunnerStats,
    Step,
    StepContext,
    StepFunction,
)
from blogwriter.jobs.scheduler import (
    DailyRule,
    IntervalRule,
    ScheduleItem,
    Scheduler,
    ScheduleStateError,
    TickReport,
    WeeklyRule,
    daily,
    every,
    next_run_after,
    weekly,
)
from blogwriter.jobs.settings import JobsSettings
from blogwriter.jobs.sse import EVENTS_PATH, get_router

__all__ = [
    "ACTIVE_STATUSES",
    "EVENTS_PATH",
    "JOBS_CONTAINER",
    "MAX_EVENTS",
    "SYSTEM_CLOCK",
    "TERMINAL_STATUSES",
    "BootGuard",
    "BootReport",
    "Broker",
    "BrokerFull",
    "Claim",
    "ClaimLost",
    "Clock",
    "DailyRule",
    "DeltaMessage",
    "InputRequest",
    "IntervalRule",
    "Job",
    "JobError",
    "JobEvent",
    "JobEventMessage",
    "JobFailed",
    "JobRegistry",
    "JobRunner",
    "JobStateError",
    "JobStatus",
    "JobType",
    "JobsLifecycle",
    "JobsService",
    "JobsSettings",
    "LeasePolicy",
    "QuarantineHook",
    "QuarantineNotice",
    "RunnerStats",
    "ScheduleItem",
    "ScheduleStateError",
    "Scheduler",
    "Step",
    "StepContext",
    "StepFunction",
    "StepState",
    "StepStatus",
    "Subscription",
    "SystemClock",
    "TickReport",
    "WeeklyRule",
    "acquire",
    "daily",
    "default_registry",
    "enqueue",
    "every",
    "get_job",
    "get_jobs_service",
    "get_router",
    "new_owner",
    "next_run_after",
    "parse_iso",
    "provide_input",
    "request_cancel",
    "retry_job",
    "server_exit_requested",
    "shutdown",
    "startup",
    "to_iso",
    "weekly",
]
