"""Validation Agent — LLM-powered validation for generated blog and LinkedIn content."""

import json
import logging
import os
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

logger = logging.getLogger(__name__)


def _load_validation_prompt() -> str:
    from backend.routers.prompts import load_prompt_content
    return load_prompt_content("validation_agent_prompt")


def _get_openai_client() -> tuple[AzureOpenAI, str]:
    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("VALIDATION_MODEL", os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o"))

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


def validate_content(
    content_type: str,
    generated_content: str,
    source_material: str,
    blog_url: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    """Run the validation agent on generated content.

    Args:
        content_type: "blog" or "linkedin_post"
        generated_content: The text to validate
        source_material: The original source the content was based on
        blog_url: The author's own blog post URL (primary link)
        source_url: The original article URL (secondary attribution)

    Returns:
        Validation result dict with is_valid, score, issues, corrected_content, summary.
    """
    system_prompt = _load_validation_prompt()
    client, model = _get_openai_client()

    user_message_parts = [
        f"content_type: {content_type}",
        f"blog_url: {blog_url or '(not provided)'}",
        f"source_url: {source_url or '(not provided)'}",
        "",
        "--- GENERATED CONTENT ---",
        generated_content,
        "",
        "--- SOURCE MATERIAL ---",
        source_material[:8000],  # Truncate to avoid token limits
    ]

    user_message = "\n".join(user_message_parts)

    logger.info(
        f"Validation agent: type={content_type}, content_len={len(generated_content)}, "
        f"source_len={len(source_material)}, blog_url={'yes' if blog_url else 'no'}, "
        f"source_url={'yes' if source_url else 'no'}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            max_completion_tokens=2500,
        )

        raw_output = response.choices[0].message.content or ""
        parsed = json.loads(_clean_json_response(raw_output))

        result = {
            "is_valid": bool(parsed.get("is_valid", True)),
            "score": int(parsed.get("score", 100)),
            "issues": parsed.get("issues", []),
            "corrected_content": parsed.get("corrected_content"),
            "summary": str(parsed.get("summary", "")),
        }

        logger.info(
            f"Validation complete: valid={result['is_valid']}, score={result['score']}, "
            f"issues={len(result['issues'])}, has_corrections={result['corrected_content'] is not None}"
        )
        return result

    except json.JSONDecodeError:
        logger.warning("Validation agent returned non-JSON; treating as pass")
        return {
            "is_valid": True,
            "score": 50,
            "issues": [{"severity": "warning", "category": "technical",
                         "description": "Validation agent returned unparseable output",
                         "suggestion": "Review content manually"}],
            "corrected_content": None,
            "summary": "Validation could not be completed automatically.",
        }
    except Exception as exc:
        logger.error(f"Validation agent failed: {exc}")
        return {
            "is_valid": True,
            "score": 0,
            "issues": [{"severity": "warning", "category": "technical",
                         "description": f"Validation failed: {str(exc)}",
                         "suggestion": "Review content manually"}],
            "corrected_content": None,
            "summary": f"Validation error: {str(exc)}",
        }
