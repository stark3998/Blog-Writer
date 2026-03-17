"""Prompts Router — View, edit, test, and reset system prompts at runtime."""

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.cosmos_client import get_prompt, upsert_prompt, delete_prompt, list_prompts

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

PROMPT_DIR = Path(__file__).parent.parent / "prompts"

# Registry of known prompts: name -> (file path, description)
PROMPT_REGISTRY: dict[str, tuple[Path, str]] = {
    "system_prompt": (
        PROMPT_DIR / "system_prompt.md",
        "Blog generation system prompt — controls how blog posts are written",
    ),
    "editor_prompt": (
        PROMPT_DIR / "editor_prompt.md",
        "AI editor prompt — controls how blog posts are edited",
    ),
    "linkedin_post_prompt": (
        PROMPT_DIR / "linkedin_post_prompt.md",
        "LinkedIn post composer — controls LinkedIn post generation",
    ),
    "validation_agent_prompt": (
        PROMPT_DIR / "validation_agent_prompt.md",
        "Validation agent — validates accuracy and URL placement in generated content",
    ),
}


def load_prompt_content(name: str) -> str:
    """Load the active prompt: Cosmos override if present, otherwise the default .md file."""
    override = get_prompt(name)
    if override and override.get("content"):
        return override["content"]
    file_path = PROMPT_REGISTRY.get(name, (None, ""))[0]
    if file_path and file_path.exists():
        return file_path.read_text(encoding="utf-8")
    raise ValueError(f"Unknown prompt: {name}")


def load_default_prompt_content(name: str) -> str:
    """Load the default prompt content from the .md file on disk."""
    file_path = PROMPT_REGISTRY.get(name, (None, ""))[0]
    if file_path and file_path.exists():
        return file_path.read_text(encoding="utf-8")
    raise ValueError(f"Unknown prompt: {name}")


# ---------- Models ----------


class PromptInfo(BaseModel):
    name: str
    description: str
    is_customized: bool
    updated_at: str | None = None


class PromptDetail(BaseModel):
    name: str
    description: str
    content: str
    default_content: str
    is_customized: bool
    updated_at: str | None = None


class PromptUpdateRequest(BaseModel):
    content: str


class PromptTestRequest(BaseModel):
    prompt_name: str
    test_input: str
    content_override: str | None = None


class PromptTestResponse(BaseModel):
    output: str
    model: str
    prompt_name: str


# ---------- Endpoints ----------


@router.get("", response_model=list[PromptInfo])
async def list_all_prompts():
    """List all available prompts with their customization status."""
    overrides = {item["name"]: item for item in list_prompts()}
    result = []
    for name, (_, description) in PROMPT_REGISTRY.items():
        override = overrides.get(name)
        result.append(
            PromptInfo(
                name=name,
                description=description,
                is_customized=override is not None,
                updated_at=override.get("updatedAt") if override else None,
            )
        )
    return result


@router.get("/{name}", response_model=PromptDetail)
async def get_prompt_detail(name: str):
    """Get full prompt content (active + default) by name."""
    if name not in PROMPT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {name}")

    _, description = PROMPT_REGISTRY[name]
    default_content = load_default_prompt_content(name)
    override = get_prompt(name)

    return PromptDetail(
        name=name,
        description=description,
        content=override["content"] if override else default_content,
        default_content=default_content,
        is_customized=override is not None,
        updated_at=override.get("updatedAt") if override else None,
    )


@router.put("/{name}", response_model=PromptDetail)
async def update_prompt(name: str, request: PromptUpdateRequest):
    """Save a custom prompt override to Cosmos DB."""
    if name not in PROMPT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {name}")

    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Prompt content cannot be empty")

    upsert_prompt(name, content)
    _, description = PROMPT_REGISTRY[name]
    default_content = load_default_prompt_content(name)
    override = get_prompt(name)

    return PromptDetail(
        name=name,
        description=description,
        content=override["content"] if override else default_content,
        default_content=default_content,
        is_customized=True,
        updated_at=override.get("updatedAt") if override else None,
    )


@router.delete("/{name}")
async def reset_prompt(name: str):
    """Reset a prompt to its default by removing the Cosmos override."""
    if name not in PROMPT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {name}")

    delete_prompt(name)
    return {"status": "reset", "name": name}


@router.post("/test", response_model=PromptTestResponse)
async def test_prompt(request: PromptTestRequest):
    """Test a prompt with sample input and return the LLM output.

    If content_override is provided, it is used instead of the saved prompt.
    Otherwise the active prompt (Cosmos override or file default) is used.
    """
    if request.prompt_name not in PROMPT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {request.prompt_name}")

    if request.content_override is not None:
        system_prompt = request.content_override.strip()
    else:
        system_prompt = load_prompt_content(request.prompt_name)

    if not system_prompt:
        raise HTTPException(status_code=400, detail="Prompt content is empty")

    test_input = request.test_input.strip()
    if not test_input:
        raise HTTPException(status_code=400, detail="Test input cannot be empty")

    # Use the same OpenAI client pattern as the services
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI

    endpoint = os.environ.get("PROJECT_ENDPOINT", "")
    api_key = os.environ.get("PROJECT_API_KEY", "")
    api_version = os.environ.get("API_VERSION", "2024-12-01-preview")
    model = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o")

    if api_key:
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
    else:
        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=token_provider,
            api_version=api_version,
        )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": test_input},
            ],
            temperature=0.7,
            max_completion_tokens=1000,
        )
        output = response.choices[0].message.content or ""
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LLM call failed: {str(exc)}")

    return PromptTestResponse(
        output=output,
        model=model,
        prompt_name=request.prompt_name,
    )
