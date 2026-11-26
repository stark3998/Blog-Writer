"""Tests for blogwriter.notify.payload: what a push notification may carry."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from blogwriter.notify import NotificationPayload


def payload(**overrides: object) -> NotificationPayload:
    values: dict[str, object] = {
        "title": "Post slot in 15 minutes",
        "url": "/publish/slot-123",
        "tag": "slot:slot-123",
    }
    values.update(overrides)
    return NotificationPayload.model_validate(values)


def test_valid_payload_and_wire_format() -> None:
    notification = payload()
    assert json.loads(notification.to_json()) == {
        "title": "Post slot in 15 minutes",
        "url": "/publish/slot-123",
        "tag": "slot:slot-123",
    }


def test_the_model_has_no_room_for_text_beyond_a_title() -> None:
    assert set(NotificationPayload.model_fields) == {"title", "url", "tag"}


@pytest.mark.parametrize("field", ["body", "description", "text", "draft", "data", "image"])
def test_extra_fields_are_refused(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        payload(**{field: "The full draft of the article..."})


def test_wire_format_has_exactly_three_members() -> None:
    document = json.loads(payload(title="Ünïcødé ✓ 東京").to_json())
    assert list(document) == ["title", "url", "tag"]
    assert document["title"] == "Ünïcødé ✓ 東京"


def test_title_length_limit() -> None:
    assert payload(title="x" * 80).title == "x" * 80
    assert payload(title="é" * 80).title == "é" * 80  # characters, not bytes
    with pytest.raises(ValidationError, match="at most 80"):
        payload(title="x" * 81)


@pytest.mark.parametrize(
    "title",
    ["", "   ", "line one\nline two", "tab\there", "bell\x07", "\u202eevil", "a\u2066b"],
)
def test_bad_titles_are_refused(title: str) -> None:
    with pytest.raises(ValidationError):
        payload(title=title)


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/publish/slot-123",
        "/review/article?id=a1&tab=diff",
        "/engage#reply-3",
        "/a/b/c-d_e.f~g",
        "/path%20encoded",
    ],
)
def test_same_origin_paths_are_accepted(url: str) -> None:
    assert payload(url=url).url == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "publish/slot",
        "https://writer.example/publish",
        "//evil.example/path",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "/path with space",
        "/line\nbreak",
        "/über",
        '/quote"',
        "/<script>",
        "/" + "a" * 512,
    ],
)
def test_other_urls_are_refused(url: str) -> None:
    with pytest.raises(ValidationError):
        payload(url=url)


@pytest.mark.parametrize("tag", ["slot:slot-123", "reply.15m", "a", "A" * 64])
def test_valid_tags(tag: str) -> None:
    assert payload(tag=tag).tag == tag


@pytest.mark.parametrize("tag", ["", "-leading", "has space", "a" * 65, "emoji✓", "a/b"])
def test_bad_tags_are_refused(tag: str) -> None:
    with pytest.raises(ValidationError):
        payload(tag=tag)


@pytest.mark.parametrize("field", ["title", "url", "tag"])
def test_fields_must_be_strings(field: str) -> None:
    with pytest.raises(ValidationError):
        payload(**{field: 123})


def test_payload_is_immutable() -> None:
    notification = payload()
    with pytest.raises(ValidationError):
        notification.title = "changed"  # pyright: ignore[reportAttributeAccessIssue]
