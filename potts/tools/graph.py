"""
Microsoft Graph client — Outlook email and calendar via MSAL device flow.

First use: Potts prints a URL and code for the user to authenticate in a browser.
The token is cached at ~/.potts/ms_token_cache.json and silently refreshed on
subsequent calls (MSAL handles refresh tokens automatically).
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx
import msal
from loguru import logger

CACHE_PATH = Path.home() / ".potts" / "ms_token_cache.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SCOPES = [
    "Mail.Read",
    "Mail.Send",
    "Calendars.Read",
    "Calendars.ReadWrite",
    "offline_access",
]


# ── Auth ───────────────────────────────────────────────────────────────────────

def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if CACHE_PATH.exists():
        try:
            cache.deserialize(CACHE_PATH.read_text())
        except Exception:
            # Corrupted cache — delete it and start fresh (user will re-authenticate)
            logger.warning("Token cache corrupted — deleting and re-authenticating.")
            CACHE_PATH.unlink(missing_ok=True)
    return cache


def _save_cache(cache: msal.SerializableTokenCache):
    if cache.has_state_changed:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(cache.serialize())


def _build_app(tenant_id: str, client_id: str):
    cache = _load_cache()
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )
    return app, cache


def _acquire_token_sync(tenant_id: str, client_id: str) -> str:
    """
    Try to get a token silently (from cache). If that fails, trigger device flow.
    This is intentionally synchronous and called via asyncio.to_thread.
    """
    app, cache = _build_app(tenant_id, client_id)

    # Silent path — use cached/refreshed token
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    # Interactive path — device flow
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(
            f"Device flow failed: {flow.get('error_description', flow)}"
        )

    print(f"\n\033[1;33m{'=' * 60}\033[0m")
    print("\033[1;33mPotts needs access to your Microsoft account.\033[0m")
    print(f"\033[1;37mGo to:\033[0m  {flow['verification_uri']}")
    print(f"\033[1;37mCode:\033[0m   {flow['user_code']}")
    print(f"\033[1;33m{'=' * 60}\033[0m\n")

    result = app.acquire_token_by_device_flow(flow)  # blocks until user authenticates

    if "access_token" not in result:
        raise RuntimeError(
            f"Authentication failed: {result.get('error_description', result)}"
        )

    _save_cache(cache)
    logger.info("Microsoft Graph: authenticated successfully.")
    return result["access_token"]


async def get_access_token(tenant_id: str, client_id: str) -> str:
    """Return a valid access token, triggering device flow if needed."""
    if not tenant_id or not client_id:
        raise RuntimeError(
            "MS_TENANT_ID and MS_CLIENT_ID are required for Outlook integration. "
            "Add them to your .env file."
        )
    import asyncio
    return await asyncio.to_thread(_acquire_token_sync, tenant_id, client_id)


# ── Graph API helpers ──────────────────────────────────────────────────────────

async def _get(token: str, path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        r.raise_for_status()
        return r.json()


async def _post(token: str, path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{GRAPH_BASE}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        r.raise_for_status()
        return r.json() if r.content else {}


# ── Email ──────────────────────────────────────────────────────────────────────

async def read_emails(
    token: str,
    count: int = 5,
    unread_only: bool = True,
) -> list[dict]:
    params: dict = {
        "$top": count,
        "$select": "subject,from,receivedDateTime,isRead,bodyPreview",
        "$orderby": "receivedDateTime desc",
    }
    if unread_only:
        params["$filter"] = "isRead eq false"
    data = await _get(token, "/me/mailFolders/inbox/messages", params)
    return data.get("value", [])


async def send_email(token: str, to: str, subject: str, body: str) -> None:
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }
    await _post(token, "/me/sendMail", payload)


# ── Calendar ───────────────────────────────────────────────────────────────────

async def read_calendar(token: str, days_ahead: int = 3) -> list[dict]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    params = {
        "startDateTime": now.isoformat(),
        "endDateTime": end.isoformat(),
        "$select": "subject,start,end,location,organizer",
        "$orderby": "start/dateTime",
        "$top": 15,
    }
    data = await _get(token, "/me/calendarView", params)
    return data.get("value", [])


async def create_event(
    token: str,
    subject: str,
    start: str,
    end: str,
    body: str = "",
    location: str = "",
) -> dict:
    """Create a calendar event. start/end should be ISO 8601 UTC strings."""
    payload: dict = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
    }
    if location:
        payload["location"] = {"displayName": location}
    return await _post(token, "/me/events", payload)
