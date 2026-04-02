"""Newsletter Service — Prepare and send blog content as email newsletters."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Newsletter preparation
# ---------------------------------------------------------------------------

def prepare_newsletter(
    title: str,
    excerpt: str,
    html_content: str,
    blog_url: str = "",
) -> dict[str, str]:
    """Format blog content into an email newsletter.

    Returns a dict with 'subject', 'html_body', and 'plain_text'.
    """
    subject = title

    cta_section = ""
    if blog_url:
        cta_section = f"""
            <tr>
              <td style="padding: 20px 30px; text-align: center;">
                <a href="{blog_url}"
                   style="display: inline-block; padding: 14px 32px;
                          background: linear-gradient(135deg, #f59e0b, #f97316);
                          color: #ffffff; text-decoration: none;
                          border-radius: 8px; font-weight: 600;
                          font-size: 16px;">
                  Read Full Post
                </a>
              </td>
            </tr>"""

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f4f4f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f4f4f5;">
    <tr>
      <td align="center" style="padding: 40px 20px;">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.07);">
          <!-- Header -->
          <tr>
            <td style="padding: 30px; background: linear-gradient(135deg, #f59e0b, #f97316); text-align: center;">
              <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 700;">{title}</h1>
              <p style="margin: 8px 0 0; color: rgba(255,255,255,0.9); font-size: 14px;">{excerpt}</p>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding: 30px; color: #1f2937; font-size: 16px; line-height: 1.7;">
              {html_content}
            </td>
          </tr>
          <!-- CTA -->{cta_section}
          <!-- Footer -->
          <tr>
            <td style="padding: 20px 30px; background-color: #f9fafb; text-align: center; border-top: 1px solid #e5e7eb;">
              <p style="margin: 0; color: #6b7280; font-size: 12px;">
                You received this email because you subscribed to our newsletter.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    # Plain-text fallback
    plain_text = f"{title}\n\n{excerpt}\n\n---\n\n{_strip_html(html_content)}"
    if blog_url:
        plain_text += f"\n\nRead the full post: {blog_url}"

    return {
        "subject": subject,
        "html_body": html_body,
        "plain_text": plain_text,
    }


def _strip_html(html: str) -> str:
    """Crude HTML-to-text conversion for plain-text fallback."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Mailchimp
# ---------------------------------------------------------------------------

def send_via_mailchimp(
    api_key: str,
    list_id: str,
    subject: str,
    html_body: str,
) -> dict[str, Any]:
    """Create and send a Mailchimp campaign.

    The API key format is ``key-dc`` where ``dc`` is the data center suffix.
    """
    # Extract data center from API key (e.g., "us21")
    if "-" not in api_key:
        raise ValueError("Invalid Mailchimp API key format. Expected 'key-dc'.")
    dc = api_key.split("-")[-1]
    base_url = f"https://{dc}.api.mailchimp.com/3.0"

    headers = {"Content-Type": "application/json"}
    auth = ("anystring", api_key)

    # 1. Create campaign
    campaign_payload = {
        "type": "regular",
        "recipients": {"list_id": list_id},
        "settings": {
            "subject_line": subject,
            "title": subject[:99],
            "from_name": "Blog Writer",
            "reply_to": "noreply@example.com",
        },
    }

    resp = requests.post(
        f"{base_url}/campaigns",
        json=campaign_payload,
        headers=headers,
        auth=auth,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        logger.error(f"Mailchimp create campaign failed: {resp.status_code} {resp.text}")
        raise RuntimeError(f"Mailchimp campaign creation failed: {resp.text}")

    campaign = resp.json()
    campaign_id = campaign["id"]

    # 2. Set campaign content
    content_resp = requests.put(
        f"{base_url}/campaigns/{campaign_id}/content",
        json={"html": html_body},
        headers=headers,
        auth=auth,
        timeout=30,
    )
    if content_resp.status_code not in (200, 201):
        logger.error(f"Mailchimp set content failed: {content_resp.status_code} {content_resp.text}")
        raise RuntimeError(f"Mailchimp set content failed: {content_resp.text}")

    # 3. Send campaign
    send_resp = requests.post(
        f"{base_url}/campaigns/{campaign_id}/actions/send",
        headers=headers,
        auth=auth,
        timeout=30,
    )
    if send_resp.status_code not in (200, 204):
        logger.error(f"Mailchimp send failed: {send_resp.status_code} {send_resp.text}")
        raise RuntimeError(f"Mailchimp send failed: {send_resp.text}")

    logger.info(f"Mailchimp campaign {campaign_id} sent successfully")
    return {"campaign_id": campaign_id}


# ---------------------------------------------------------------------------
# ConvertKit
# ---------------------------------------------------------------------------

def send_via_convertkit(
    api_key: str,
    subject: str,
    html_body: str,
) -> dict[str, Any]:
    """Create a ConvertKit broadcast."""
    base_url = "https://api.convertkit.com/v3"

    resp = requests.post(
        f"{base_url}/broadcasts",
        json={
            "api_secret": api_key,
            "subject": subject,
            "content": html_body,
            "published": True,
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        logger.error(f"ConvertKit broadcast failed: {resp.status_code} {resp.text}")
        raise RuntimeError(f"ConvertKit broadcast creation failed: {resp.text}")

    data = resp.json()
    broadcast_id = data.get("broadcast", {}).get("id", "unknown")
    logger.info(f"ConvertKit broadcast {broadcast_id} created successfully")
    return {"broadcast_id": broadcast_id}


# ---------------------------------------------------------------------------
# Generic SMTP
# ---------------------------------------------------------------------------

def send_via_generic_smtp(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    from_email: str,
    to_list: list[str],
    subject: str,
    html_body: str,
) -> dict[str, Any]:
    """Send a newsletter via a generic SMTP server."""
    if not to_list:
        raise ValueError("Recipient list is empty")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email

    # Plain-text fallback
    plain_text = _strip_html(html_body)
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    sent_count = 0
    errors: list[str] = []

    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.starttls()

        server.login(smtp_user, smtp_pass)

        for recipient in to_list:
            try:
                msg.replace_header("To", recipient) if "To" in msg else msg.__setitem__("To", recipient)
                server.sendmail(from_email, recipient, msg.as_string())
                sent_count += 1
            except smtplib.SMTPException as exc:
                logger.error(f"Failed to send to {recipient}: {exc}")
                errors.append(f"{recipient}: {exc}")

        server.quit()
    except Exception as exc:
        logger.error(f"SMTP connection error: {exc}")
        raise RuntimeError(f"SMTP connection failed: {exc}")

    logger.info(f"SMTP send complete: {sent_count}/{len(to_list)} sent")
    return {"sent": sent_count, "total": len(to_list), "errors": errors}
