"""Screenshots: the owner's own screenshot is read by ENGAGEMENT (image input) and never stored."""

from __future__ import annotations

import io
import json

import httpx
from PIL import Image

from blogwriter.ai.types import MAX_IMAGE_BYTES, ImagePart, Sensitivity, Task, TextPart
from blogwriter.core.testing import OWNER_OID, make_settings, principal_headers, write_headers
from blogwriter.db import CONTAINERS
from blogwriter.domain.testing import (
    FakeServices,
    ScriptedModelRunner,
    ScriptedReply,
    make_app,
    make_services,
    png_bytes,
)
from blogwriter.engagement import get_router
from blogwriter.engagement._prompts import load_prompt
from blogwriter.engagement.models import ExtractReply
from blogwriter.engagement.testing import save_policies

READ = principal_headers(OWNER_OID)
WRITE = {**READ, **write_headers(make_settings())}
URL = "/api/engagement/extract"


def jpeg_with_exif() -> bytes:
    image = Image.new("RGB", (40, 30), "#123456")
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    data = buffer.getvalue()
    assert b"SecretCam" in data
    return data


def runner_of(services: FakeServices) -> ScriptedModelRunner:
    assert services.ai is not None
    return services.ai


async def dump(services: FakeServices) -> str:
    bodies = [raw.body for name in CONTAINERS for raw in await services.store.find(name, [])]
    return json.dumps(bodies, sort_keys=True)


async def post_image(
    client: httpx.AsyncClient, data: bytes, content_type: str = "image/jpeg"
) -> httpx.Response:
    return await client.post(URL, content=data, headers={**WRITE, "Content-Type": content_type})


async def test_a_screenshot_is_read_and_nothing_is_stored(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    before = await dump(services)
    runner_of(services).add(
        Task.ENGAGEMENT,
        ExtractReply(
            text="  Did Contoso\u200b scope tokens per tool?\r\n\r\n\r\n\r\nSecond one.  "
        ),
    )
    response = await post_image(client, jpeg_with_exif())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "Did Contoso scope tokens per tool?\n\nSecond one."
    [finding] = body["findings"]
    assert (finding["code"], finding["ref"], finding["start"], finding["end"]) == (
        "blocklisted-term",
        "t-1",
        4,
        11,
    )
    [call] = runner_of(services).calls_for(Task.ENGAGEMENT)
    request = call.request
    assert call.origin == "owner"
    assert request.sensitivity is Sensitivity.ANONYMIZED
    assert request.tools == ()
    assert request.output_schema is ExtractReply
    assert request.system == load_prompt("extract.v1").text
    [message] = request.messages
    text_part, image_part = message.parts
    assert isinstance(text_part, TextPart)
    assert isinstance(image_part, ImagePart)
    assert image_part.media_type == "image/jpeg"
    assert b"SecretCam" not in image_part.data
    assert b"Exif" not in image_part.data
    assert await dump(services) == before


async def test_only_png_jpeg_and_webp_screenshots_are_read(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    for content_type in ("text/plain", "image/gif", "image/svg+xml", ""):
        response = await post_image(client, png_bytes(), content_type)
        assert response.status_code == 415, content_type
    mismatch = await post_image(client, png_bytes(), "image/jpeg")
    assert mismatch.status_code == 415
    garbage = await post_image(client, b"<html><script>x</script></html>", "image/png")
    assert garbage.status_code == 415
    empty = await post_image(client, b"", "image/png")
    assert empty.status_code == 422
    assert runner_of(services).calls == []


async def test_oversized_screenshots_are_refused(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    too_many_bytes = await post_image(client, b"\x89PNG" + b"0" * MAX_IMAGE_BYTES, "image/png")
    assert too_many_bytes.status_code == 413
    huge = Image.new("RGB", (4000, 3001), "#ffffff")
    buffer = io.BytesIO()
    huge.save(buffer, format="PNG")
    too_many_pixels = await post_image(client, buffer.getvalue(), "image/png")
    assert too_many_pixels.status_code == 413
    assert too_many_pixels.json()["code"] == "too_large"
    assert runner_of(services).calls == []


async def test_without_the_ai_use_confirmation_no_screenshot_is_sent(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    await save_policies(services.store, ai_use=False)
    response = await post_image(client, jpeg_with_exif())
    assert response.status_code == 409
    assert response.json()["code"] == "ai_use_not_confirmed"
    assert runner_of(services).calls == []


async def test_without_a_model_the_route_answers_503(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    services.ai = None
    response = await post_image(client, jpeg_with_exif())
    assert response.status_code == 503
    assert response.json()["code"] == "ai_unavailable"


async def test_nothing_readable_is_a_clear_422(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    runner_of(services).add(
        Task.ENGAGEMENT, ExtractReply(text=" \u200b "), ScriptedReply(refused=True)
    )
    for _ in range(2):
        response = await post_image(client, png_bytes(), "image/png")
        assert response.status_code == 422
        assert response.json()["code"] == "nothing_extracted"


def test_the_openapi_document_declares_the_raw_screenshot_body() -> None:
    """The web client is generated from it: the screenshot is a raw image body."""
    app = make_app(make_services(), routers=[get_router()], settings=make_settings())
    body = app.openapi()["paths"][URL]["post"]["requestBody"]
    assert body["required"] is True
    assert body["content"] == {"image/jpeg": {}, "image/png": {}, "image/webp": {}}
    assert str(MAX_IMAGE_BYTES) in body["description"]
