"""Humanizer Agent — Rewrites AI-generated posts to sound authentically human."""

import json
import logging
import os
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

logger = logging.getLogger(__name__)


def _load_humanizer_prompt() -> str:
    from backend.routers.prompts import load_prompt_content
    return load_prompt_content("humanizer_prompt")


def _get_openai_client() -> tuple[AzureOpenAI, str]:
    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("HUMANIZER_MODEL", os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o"))

    if api_key:
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        return client, model

    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=api_version,
    )
    return client, model


def _clean_json_response(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def humanize_post(
    post_text: str,
    content_type: str = "linkedin_post",
    source_material: str = "",
) -> dict[str, Any]:
    """Rewrite an AI-generated post to sound authentically human.

    Args:
        post_text: The AI-generated post text to humanize.
        content_type: "linkedin_post" or "tweet".
        source_material: Original blog content for context (optional).

    Returns:
        Dict with humanized_text and changes_summary.
        On any error, returns the original text unchanged.
    """
    if not post_text.strip():
        return {"humanized_text": post_text, "changes_summary": "empty input"}

    system_prompt = _load_humanizer_prompt()
    client, model = _get_openai_client()

    user_message = (
        f"Content type: {content_type}\n\n"
        f"--- POST TO HUMANIZE ---\n{post_text}\n"
    )

    if source_material:
        user_message += f"\n--- ORIGINAL BLOG (for context only, do not copy from this) ---\n{source_material[:4000]}\n"

    logger.info(f"Humanizer agent: type={content_type}, post_len={len(post_text)}")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.9,
            max_completion_tokens=2000,
        )

        raw_output = response.choices[0].message.content or ""
        parsed = json.loads(_clean_json_response(raw_output))

        humanized = str(parsed.get("humanized_text", "")).strip()
        summary = str(parsed.get("changes_summary", "")).strip()

        if not humanized:
            logger.warning("Humanizer returned empty text, keeping original")
            return {"humanized_text": post_text, "changes_summary": "humanizer returned empty"}

        logger.info(f"Humanizer complete: changes='{summary}', len {len(post_text)}->{len(humanized)}")
        return {"humanized_text": humanized, "changes_summary": summary}

    except json.JSONDecodeError:
        # If the model returned plain text instead of JSON, use it directly
        raw = response.choices[0].message.content or "" if 'response' in dir() else ""
        if raw.strip() and not raw.strip().startswith("{"):
            logger.info("Humanizer returned plain text instead of JSON, using directly")
            return {"humanized_text": raw.strip(), "changes_summary": "plain text response"}
        logger.warning("Humanizer returned unparseable JSON, keeping original")
        return {"humanized_text": post_text, "changes_summary": "parse error"}

    except Exception as exc:
        logger.warning(f"Humanizer agent failed: {exc}")
        return {"humanized_text": post_text, "changes_summary": f"error: {exc}"}
