"""AI Editor Service — AI-powered blog content editing.

Takes current blog content + user prompt, sends to GPT-4o,
and returns the modified content. Supports streaming.
"""

import logging
import os
import time
from pathlib import Path
from typing import AsyncGenerator

from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

logger = logging.getLogger(__name__)

EDITOR_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "editor_prompt.md"


def _load_editor_prompt() -> str:
    """Load the AI editor system prompt."""
    return EDITOR_PROMPT_PATH.read_text(encoding="utf-8")


def _get_openai_client() -> tuple[AzureOpenAI, str]:
    """Create and return an Azure OpenAI client and model name."""
    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o")

    logger.debug(
        f"Initializing Azure OpenAI client: endpoint={endpoint}, model={model}, api_version={api_version}"
    )

    if api_key:
        logger.debug("Using API key authentication")
        # Use API key authentication if provided (for local dev)
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=None,
            api_key=api_key,
            api_version=api_version,
        )
        return client, model
    else:
        logger.debug("Using DefaultAzureCredential authentication")
        # Use Azure AD authentication (for production)
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


def edit_blog_content(current_content: str, prompt: str) -> str:
    """Edit blog content based on a user prompt using GPT-4o (non-streaming).

    Args:
        current_content: The current MDX blog content.
        prompt: The user's editing instruction.

    Returns:
        The complete updated MDX content.
    """
    logger.info(
        f"Starting non-streaming edit: content_length={len(current_content)}, prompt_length={len(prompt)}"
    )
    start_time = time.time()

    system_prompt = _load_editor_prompt()
    client, model = _get_openai_client()

    user_message = (
        f"## Current Blog Post\n\n"
        f"{current_content}\n\n"
        f"---\n\n"
        f"## Editing Instruction\n\n"
        f"{prompt}\n\n"
        f"---\n\n"
        f"Apply the editing instruction above and return the complete updated MDX blog post. "
        f"Return ONLY the MDX content, nothing else."
    )

    try:
        logger.debug(f"Making API call to {model} for editing")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=1.0,
            max_completion_tokens=4096,
        )

        result = response.choices[0].message.content or current_content
        elapsed = time.time() - start_time

        tokens = response.usage.total_tokens if response.usage else "unknown"
        logger.info(
            f"Edit successful: elapsed={elapsed:.2f}s, "
            f"tokens={tokens}, "
            f"result_length={len(result)}"
        )

        # Strip wrapping code fences if present
        if result.startswith("```mdx"):
            result = result[6:]
        elif result.startswith("```markdown"):
            result = result[11:]
        elif result.startswith("```"):
            result = result[3:]
        if result.endswith("```"):
            result = result[:-3]

        return result.strip()
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Edit failed after {elapsed:.2f}s: {type(e).__name__}: {str(e)}")
        raise


async def edit_blog_content_stream(
    current_content: str, prompt: str
) -> AsyncGenerator[str, None]:
    """Edit blog content with streaming response.

    Yields chunks of the updated content as they arrive.

    Args:
        current_content: The current MDX blog content.
        prompt: The user's editing instruction.

    Yields:
        String chunks of the updated content.
    """
    logger.info(
        f"Starting streaming edit: content_length={len(current_content)}, prompt_length={len(prompt)}"
    )
    start_time = time.time()
    chunk_count = 0

    system_prompt = _load_editor_prompt()
    client, model = _get_openai_client()

    user_message = (
        f"## Current Blog Post\n\n"
        f"{current_content}\n\n"
        f"---\n\n"
        f"## Editing Instruction\n\n"
        f"{prompt}\n\n"
        f"---\n\n"
        f"Apply the editing instruction above and return the complete updated MDX blog post. "
        f"Return ONLY the MDX content, nothing else."
    )

    try:
        logger.debug(f"Making streaming API call to {model} for editing")
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=1.0,
            max_completion_tokens=4096,
            stream=True,
        )

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                chunk_count += 1
                logger.debug(f"Received chunk {chunk_count}: {len(content)} chars")
                yield content

        elapsed = time.time() - start_time
        logger.info(
            f"Streaming edit complete: elapsed={elapsed:.2f}s, chunks={chunk_count}"
        )
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"Streaming edit failed after {elapsed:.2f}s: {type(e).__name__}: {str(e)}"
        )
        raise
