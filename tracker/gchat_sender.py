"""
Google Chat REST API — outbound message sender.
Uses the service account to post messages into spaces proactively.
"""
import os
import json
import logging
import httpx
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleRequest

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/chat.bot"]
_CHAT_API = "https://chat.googleapis.com/v1"
_CREDS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "./credentials/service_account.json")

_credentials: service_account.Credentials | None = None


def _get_token() -> str:
    global _credentials
    if _credentials is None:
        _credentials = service_account.Credentials.from_service_account_file(
            _CREDS_PATH, scopes=_SCOPES
        )
    if not _credentials.valid:
        _credentials.refresh(GoogleRequest())
    return _credentials.token


def send_message(space_name: str, text: str) -> dict:
    """
    Post a plain-text message to a Google Chat space.
    space_name: "spaces/XXXXXXXX"
    Returns the created message object (includes thread.name).
    """
    token = _get_token()
    url = f"{_CHAT_API}/{space_name}/messages"
    payload = {"text": text}
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code != 200:
        log.error("Google Chat send failed: %s %s", resp.status_code, resp.text)
        return {}
    data = resp.json()
    log.info("Message sent to %s — thread: %s", space_name, data.get("thread", {}).get("name"))
    return data


def reply_to_thread(space_name: str, thread_name: str, text: str) -> dict:
    """Reply inside an existing thread."""
    token = _get_token()
    url = f"{_CHAT_API}/{space_name}/messages"
    payload = {
        "text": text,
        "thread": {"name": thread_name},
        "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD",
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code != 200:
        log.error("Google Chat reply failed: %s %s", resp.status_code, resp.text)
        return {}
    return resp.json()
