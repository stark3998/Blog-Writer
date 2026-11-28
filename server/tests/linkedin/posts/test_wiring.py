"""Wiring and structural invariants: the job types registered, no schedule items, the routes,
the prompts carry no URL and the bans, nothing in the package can reach the publisher, and the
structured outputs compile for both providers."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from blogwriter.ai.schema import compile_schema
from blogwriter.domain.jobtypes import (
    POSTS_CHECK,
    POSTS_DERIVE,
    POSTS_EXPIRE,
    POSTS_GENERATE,
    POSTS_RENDER,
    POSTS_STAGE,
    SCHEDULED_JOB_TYPES,
)
from blogwriter.domain.settings import CadenceSettings
from blogwriter.domain.testing import SAMPLE_NOW
from blogwriter.jobs import JobRegistry
from blogwriter.linkedin.posts import get_router, register_jobs, schedule_entries
from blogwriter.linkedin.posts._prompts import PROMPT_VERSIONS, load_prompt
from blogwriter.linkedin.posts.checks import ClaimsOut
from blogwriter.linkedin.posts.derive import CarouselOut, HooksOut, PostDraftOut, TextOut
from blogwriter.linkedin.posts.staging import LinkedInTransport, StagingTransport

PACKAGE = Path(__file__).parents[3] / "src" / "blogwriter" / "linkedin" / "posts"


def test_register_jobs_registers_the_six_types() -> None:
    registry = JobRegistry()
    register_jobs(registry)
    assert set(registry.names()) == {
        POSTS_CHECK,
        POSTS_DERIVE,
        POSTS_EXPIRE,
        POSTS_GENERATE,
        POSTS_RENDER,
        POSTS_STAGE,
    }
    stage = registry.get(POSTS_STAGE)
    assert stage is not None
    assert stage.step_names == ("check", "render", "upload", "token", "ready")
    assert not set(registry.names()) & SCHEDULED_JOB_TYPES


def test_no_schedule_entries() -> None:
    assert schedule_entries(CadenceSettings.default(SAMPLE_NOW)) == []


def test_routes() -> None:
    routes = {
        (method, route.path)
        for route in get_router().routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    assert routes == {
        ("GET", "/api/posts"),
        ("POST", "/api/posts"),
        ("POST", "/api/articles/{article_id}/posts/derive"),
        ("GET", "/api/posts/{post_id}"),
        ("PUT", "/api/posts/{post_id}"),
        ("POST", "/api/posts/{post_id}/lint"),
        ("POST", "/api/posts/{post_id}/check"),
        ("PUT", "/api/posts/{post_id}/carousel"),
        ("GET", "/api/posts/{post_id}/carousel/preview"),
        ("POST", "/api/posts/{post_id}/render"),
        ("POST", "/api/posts/{post_id}/regenerate"),
        ("POST", "/api/posts/{post_id}/approve"),
        ("POST", "/api/posts/{post_id}/unapprove"),
        ("POST", "/api/posts/{post_id}/archive"),
    }


def test_the_default_transport_satisfies_the_port() -> None:
    transport: StagingTransport = LinkedInTransport()
    assert transport is not None


@pytest.mark.parametrize("name", sorted(PROMPT_VERSIONS))
def test_prompts_carry_no_url(name: str) -> None:
    prompt = load_prompt(name)  # type: ignore[arg-type]
    assert prompt.version == f"posts.{name}.v1"
    assert not re.search(r"https?://|www\.", prompt.text)


def test_the_system_prompt_states_the_bans() -> None:
    text = load_prompt("system").text
    for phrase in (
        "zero links",
        "Unpopular opinion",
        "Here's the thing",
        "Let that sink in",
        "X is dead",
        "Most people get X wrong",
        "Nobody talks about",
        "Agree?",
        "leverage",
        "It's not X, it's Y",
        "same digits and the same unit",
        "publisher and the title",
        "uses_owner_wording",
        "140 characters",
    ):
        assert phrase in text, phrase


@pytest.mark.parametrize("model", [PostDraftOut, HooksOut, TextOut, CarouselOut, ClaimsOut])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_structured_outputs_compile(model: type, provider: str) -> None:
    compile_schema(provider, model)  # type: ignore[arg-type]


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def test_nothing_in_posts_can_reach_the_publisher() -> None:
    forbidden = re.compile(r"publish_post|owner_click|OwnerClick|create_post|transport\.publisher")
    for path in sorted(PACKAGE.rglob("*.py")):
        for name in _imported_names(path):
            assert not forbidden.search(name), f"{path.name} references {name}"
