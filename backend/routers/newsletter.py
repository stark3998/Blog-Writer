"""Newsletter Router — Send blog content as email newsletters."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.db.cosmos_client import get_draft
from backend.models.user import UserInfo
from backend.services.newsletter_service import (
    prepare_newsletter,
    send_via_mailchimp,
    send_via_convertkit,
    send_via_generic_smtp,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class MailchimpConfig(BaseModel):
    api_key: str
    list_id: str


class ConvertKitConfig(BaseModel):
    api_key: str


class SmtpConfig(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_pass: str
    from_email: str
    to_list: list[str]


class SendNewsletterRequest(BaseModel):
    draft_id: str
    provider: str  # "mailchimp" | "convertkit" | "smtp"
    config: dict[str, Any]


class PreviewNewsletterRequest(BaseModel):
    draft_id: str


class SendNewsletterResponse(BaseModel):
    status: str
    provider_response: dict[str, Any] = {}


class PreviewNewsletterResponse(BaseModel):
    subject: str
    html_body: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_draft_or_404(draft_id: str) -> dict:
    """Fetch a draft, raising 404 if not found."""
    draft = get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found")
    return draft


def _draft_to_newsletter(draft: dict) -> dict[str, str]:
    """Prepare newsletter content from a draft."""
    title = draft.get("title", "Untitled")
    excerpt = draft.get("excerpt", "")
    content = draft.get("content", "")
    blog_url = draft.get("publishedUrl", "")

    return prepare_newsletter(
        title=title,
        excerpt=excerpt,
        html_content=content,
        blog_url=blog_url,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/send", response_model=SendNewsletterResponse)
def send_newsletter(
    body: SendNewsletterRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Send a draft as an email newsletter via the chosen provider."""
    logger.info(
        f"User {user.user_id} sending newsletter for draft {body.draft_id} via {body.provider}"
    )

    draft = _get_draft_or_404(body.draft_id)
    newsletter = _draft_to_newsletter(draft)

    provider = body.provider.lower()

    try:
        if provider == "mailchimp":
            mc = MailchimpConfig(**body.config)
            provider_response = send_via_mailchimp(
                api_key=mc.api_key,
                list_id=mc.list_id,
                subject=newsletter["subject"],
                html_body=newsletter["html_body"],
            )
        elif provider == "convertkit":
            ck = ConvertKitConfig(**body.config)
            provider_response = send_via_convertkit(
                api_key=ck.api_key,
                subject=newsletter["subject"],
                html_body=newsletter["html_body"],
            )
        elif provider == "smtp":
            smtp = SmtpConfig(**body.config)
            provider_response = send_via_generic_smtp(
                smtp_host=smtp.smtp_host,
                smtp_port=smtp.smtp_port,
                smtp_user=smtp.smtp_user,
                smtp_pass=smtp.smtp_pass,
                from_email=smtp.from_email,
                to_list=smtp.to_list,
                subject=newsletter["subject"],
                html_body=newsletter["html_body"],
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported provider: '{body.provider}'. Use 'mailchimp', 'convertkit', or 'smtp'.",
            )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.error(f"Newsletter send failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Newsletter send failed: {exc}")

    return SendNewsletterResponse(status="sent", provider_response=provider_response)


@router.post("/preview", response_model=PreviewNewsletterResponse)
def preview_newsletter(
    body: PreviewNewsletterRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Preview the newsletter HTML that would be sent for a draft."""
    logger.info(f"User {user.user_id} previewing newsletter for draft {body.draft_id}")

    draft = _get_draft_or_404(body.draft_id)
    newsletter = _draft_to_newsletter(draft)

    return PreviewNewsletterResponse(
        subject=newsletter["subject"],
        html_body=newsletter["html_body"],
    )
