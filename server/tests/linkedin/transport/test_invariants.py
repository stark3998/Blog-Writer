"""The transport's invariants: nothing but the publish route reaches the publisher (an
import-graph test over ``blogwriter.*``), jobs and schedules cannot, scopes are a constant, the
client is wrapped by no retry helper."""

from __future__ import annotations

import ast
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Final

import httpx
import pytest
from fastapi.routing import APIRoute

import blogwriter
from blogwriter.domain.jobtypes import SCHEDULED_JOB_TYPES, generates_content
from blogwriter.domain.settings import LINKEDIN_SCOPES, CadenceSettings, LinkedInConnection
from blogwriter.jobs import DailyRule, JobRegistry
from blogwriter.linkedin.transport import register_jobs, schedule_entries
from blogwriter.linkedin.transport.client import LinkedInClient
from blogwriter.linkedin.transport.oauth import SCOPE_PARAM
from blogwriter.linkedin.transport.routes import get_router
from blogwriter.linkedin.transport.testing import make_linkedin_settings

SRC: Final = Path(blogwriter.__file__).resolve().parent
PUBLISHER: Final = "blogwriter.linkedin.transport.publisher"
ALLOWED: Final = frozenset({PUBLISHER, "blogwriter.linkedin.transport.routes"})
PUBLISHER_NAMES: Final = frozenset({"publish_post", "OwnerClick", "owner_click"})
TRANSPORT: Final = "blogwriter.linkedin.transport"


def module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve(module: str, level: int, current: str, is_package: bool) -> str:
    """The absolute module a (possibly relative) ``from ... import`` names."""
    if level == 0:
        return module
    base = current.split(".")
    if not is_package:
        base = base[:-1]
    base = base[: len(base) - (level - 1)]
    return ".".join([*base, module] if module else base)


def docstrings(tree: ast.Module) -> set[int]:
    """``id()`` of every docstring node (module, class, function): prose, not code."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def references_in(source: str, name: str, *, is_package: bool = False) -> set[str]:
    """How module ``name`` reaches the publisher: an import of it (absolute, relative, from
    the package, or a dynamic import string), or, inside the transport, a publisher name."""
    tree = ast.parse(source)
    prose = docstrings(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(f"import {a.name}" for a in node.names if a.name.startswith(PUBLISHER))
        elif isinstance(node, ast.ImportFrom):
            target = resolve(node.module or "", node.level, name, is_package)
            if target.startswith(PUBLISHER):
                found.add(f"from {target}")
            if target == TRANSPORT:
                wanted = ("publisher", *PUBLISHER_NAMES)
                found.update(
                    f"from {target} import {a.name}" for a in node.names if a.name in wanted
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if PUBLISHER in node.value and id(node) not in prose:
                found.add("dynamic import string")
        elif name.startswith(TRANSPORT):
            if isinstance(node, ast.Name) and node.id in PUBLISHER_NAMES:
                found.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in PUBLISHER_NAMES:
                found.add(node.attr)
    return found


def publisher_references(path: Path) -> set[str]:
    return references_in(
        path.read_text(encoding="utf-8"), module_name(path), is_package=path.name == "__init__.py"
    )


def test_nothing_but_the_publish_route_reaches_the_publisher() -> None:
    modules = sorted(SRC.rglob("*.py"))
    assert len(modules) > 50  # the whole tree, every package
    offenders = {
        module_name(path): refs
        for path in modules
        if module_name(path) not in ALLOWED and (refs := publisher_references(path))
    }
    assert offenders == {}
    assert publisher_references(SRC / "linkedin" / "transport" / "routes.py")  # the one user


@pytest.mark.parametrize(
    ("name", "source"),
    [
        (
            "blogwriter.planner.jobs",
            "from blogwriter.linkedin.transport.publisher import publish_post",
        ),
        ("blogwriter.planner.jobs", "import blogwriter.linkedin.transport.publisher as p"),
        ("blogwriter.planner.jobs", "from blogwriter.linkedin.transport import publisher"),
        ("blogwriter.planner.jobs", "from blogwriter.linkedin.transport import publish_post"),
        (
            "blogwriter.planner.jobs",
            "import importlib\nimportlib.import_module('blogwriter.linkedin.transport.publisher')",
        ),
        ("blogwriter.linkedin.transport.jobs", "from .publisher import OwnerClick"),
        ("blogwriter.linkedin.transport.jobs", "from . import publisher"),
        ("blogwriter.linkedin.transport.jobs", "step = publish_post"),
        ("blogwriter.linkedin.posts.staging", "from ..transport.publisher import publish_post"),
    ],
)
def test_the_import_graph_check_catches_every_way_in(name: str, source: str) -> None:
    assert references_in(source, name)


def test_the_import_graph_check_ignores_prose() -> None:
    source = '"""Never import blogwriter.linkedin.transport.publisher from a job."""\nX = 1\n'
    assert references_in(source, "blogwriter.planner.jobs") == set()


def test_exactly_one_route_calls_the_publisher() -> None:
    callers = [
        f"{sorted(route.methods or ())[0]} {route.path}"
        for route in get_router().routes
        if isinstance(route, APIRoute) and "publish_post" in route.endpoint.__code__.co_names
    ]
    assert callers == ["POST /api/posts/{post_id}/publish"]


def test_jobs_schedules_and_the_package_never_load_the_publisher() -> None:
    probe = (
        "import sys\n"
        "import blogwriter.linkedin.transport as t\n"
        "import blogwriter.linkedin.transport.jobs, blogwriter.linkedin.transport.schedule\n"
        "import blogwriter.linkedin.transport.lifecycle, blogwriter.linkedin.transport.upload\n"
        "import blogwriter.linkedin.transport.kit, blogwriter.linkedin.transport.live\n"
        "import blogwriter.linkedin.transport.tokens, blogwriter.jobs\n"
        "from blogwriter.jobs import JobRegistry\n"
        "registry = JobRegistry(); t.register_jobs(registry)\n"
        f"print({PUBLISHER!r} in sys.modules, {TRANSPORT + '.routes'!r} in sys.modules)\n"
    )
    result = subprocess.run(  # noqa: S603 - our own interpreter and a constant probe
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == ["False", "False"]


def test_registered_steps_live_outside_the_publisher() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    for name in registry.names():
        job_type = registry.get(name)
        assert job_type is not None
        for step in job_type.steps:
            module = step.run.__module__
            assert module not in ALLOWED
            assert not set(step.run.__code__.co_names) & PUBLISHER_NAMES


def test_the_schedule_starts_only_the_introspect_job() -> None:
    default = CadenceSettings.default("2026-11-26T12:00:00.000000Z")
    (entry,) = schedule_entries(default)
    assert entry.id == "sched-linkedin-introspect"
    assert entry.job_type == "linkedin.introspect"
    assert entry.job_type in SCHEDULED_JOB_TYPES
    assert not generates_content(entry.job_type)
    assert isinstance(entry.rule, DailyRule)
    assert entry.rule.at == "08:30"
    assert entry.timezone == "America/New_York"
    assert entry.max_lateness == timedelta(hours=24)
    assert schedule_entries(default) == [entry]  # pure and deterministic
    berlin = default.model_copy(update={"timezone": "Europe/Berlin"})
    assert schedule_entries(berlin)[0].timezone == "Europe/Berlin"


def test_scopes_are_the_constant() -> None:
    assert LINKEDIN_SCOPES == ("openid", "profile", "w_member_social")
    assert SCOPE_PARAM == "openid profile w_member_social"
    with pytest.raises(ValueError, match="constant"):
        LinkedInConnection.model_validate(
            {
                "id": "linkedin-connection",
                "createdAt": "2026-11-26T12:00:00.000000Z",
                "updatedAt": "2026-11-26T12:00:00.000000Z",
                "scopes": ["openid", "profile", "w_member_social", "r_member_social"],
            }
        )


async def test_the_client_is_wrapped_by_no_retry_helper() -> None:
    client = LinkedInClient(make_linkedin_settings())
    try:
        http = vars(client)["_http"]
        assert isinstance(http, httpx.AsyncClient)
        assert http.follow_redirects is False
        assert vars(http)["_trust_env"] is False
        transport = vars(http)["_transport"]
        assert isinstance(transport, httpx.AsyncHTTPTransport)
        pool: object = vars(transport)["_pool"]
        assert vars(pool)["_retries"] == 0
    finally:
        await client.aclose()
    for path in (SRC / "linkedin").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in (
                node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
            )
        }
        assert not imported & {"tenacity", "backoff", "stamina", "retrying", "urllib3"}, path
