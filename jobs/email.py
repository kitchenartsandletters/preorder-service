"""
jobs/email.py

Shared email helper using Mailtrap's sending API.
Used by nyt_notifier and nyt_reporter.

Env vars required:
  MAILTRAP_API_TOKEN  — Mailtrap API token
  EMAIL_SENDER        — From address (e.g. admin@kitchenartsandletters.com)
  EMAIL_RECIPIENTS    — Comma-separated recipient list
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

MAILTRAP_API_TOKEN = os.environ["MAILTRAP_API_TOKEN"]
EMAIL_SENDER       = os.environ["EMAIL_SENDER"]
EMAIL_RECIPIENTS   = os.environ["EMAIL_RECIPIENTS"]

MAILTRAP_SEND_URL  = "https://send.api.mailtrap.io/api/send"


def get_recipients() -> list[str]:
    return [r.strip() for r in EMAIL_RECIPIENTS.split(",") if r.strip()]


def send_email(
    subject: str,
    html_body: str,
    text_body: str,
    attachment_name: Optional[str] = None,
    attachment_data: Optional[str] = None,   # CSV text
    screenshot_b64: Optional[str] = None,    # base64 PNG
) -> None:
    """
    Send an email via Mailtrap's sending API.
    Attachments are optional: CSV text and/or a base64-encoded PNG screenshot.
    """
    recipients = get_recipients()
    if not recipients:
        log.warning("EMAIL_RECIPIENTS is empty — no email sent")
        return

    payload: dict = {
        "from":    {"email": EMAIL_SENDER},
        "to":      [{"email": r} for r in recipients],
        "subject": subject,
        "html":    html_body,
        "text":    text_body,
    }

    attachments = []

    if attachment_name and attachment_data:
        attachments.append({
            "filename":     attachment_name,
            "content":      base64.b64encode(attachment_data.encode()).decode(),
            "content_type": "text/csv",
            "disposition":  "attachment",
        })

    if screenshot_b64:
        attachments.append({
            "filename":     "playwright_failure.png",
            "content":      screenshot_b64,
            "content_type": "image/png",
            "disposition":  "attachment",
        })

    if attachments:
        payload["attachments"] = attachments

    response = httpx.post(
        MAILTRAP_SEND_URL,
        headers={
            "Authorization": f"Bearer {MAILTRAP_API_TOKEN}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=15.0,
    )

    if not response.is_success:
        raise RuntimeError(
            f"Mailtrap send failed: {response.status_code} {response.text}"
        )

    log.info(f"Email '{subject}' sent to {recipients} via Mailtrap")