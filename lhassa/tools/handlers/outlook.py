# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outlook / Microsoft Graph tool handlers (email + calendar)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.llm_service import FunctionCallParams

from loguru import logger

from lhassa.tools.handlers._shared import _bump_tool


async def _graph_token() -> str:
    from lhassa.config import Config
    from lhassa.tools.graph import get_access_token
    config = Config()
    return await get_access_token(config.ms_tenant_id, config.ms_client_id)


async def handle_read_emails(params: FunctionCallParams):
    count = params.arguments.get("count", 5)
    unread_only = params.arguments.get("unread_only", True)
    logger.info(f"[tool] read_emails count={count} unread={unread_only}")
    print("\033[2m[📧 reading emails...]\033[0m")
    _bump_tool()

    try:
        from lhassa.tools.graph import read_emails
        token = await _graph_token()
        emails = await read_emails(token, count=count, unread_only=unread_only)

        if not emails:
            suffix = " unread" if unread_only else ""
            await params.result_callback(f"No{suffix} emails in your inbox.")
            return

        lines = []
        for e in emails:
            sender = e.get("from", {}).get("emailAddress", {}).get("name", "?")
            subject = e.get("subject", "(no subject)")
            preview = e.get("bodyPreview", "")[:100]
            date = e.get("receivedDateTime", "")[:10]
            lines.append(f"[{date}] From: {sender} — {subject}. {preview}")

        await params.result_callback("\n\n".join(lines))
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"read_emails error: {e}")
        await params.result_callback(f"Error reading emails: {e}")


async def handle_send_email(params: FunctionCallParams):
    to = params.arguments.get("to", "")
    subject = params.arguments.get("subject", "")
    body = params.arguments.get("body", "")
    logger.info(f"[tool] send_email to={to!r}")
    print(f"\033[2m[📤 sending email to {to}...]\033[0m")
    _bump_tool()

    try:
        from lhassa.tools.graph import send_email
        token = await _graph_token()
        await send_email(token, to=to, subject=subject, body=body)
        await params.result_callback(f"Email sent to {to}.")
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"send_email error: {e}")
        await params.result_callback(f"Error sending email: {e}")


async def handle_read_calendar(params: FunctionCallParams):
    days = params.arguments.get("days_ahead", 3)
    logger.info(f"[tool] read_calendar days={days}")
    print(f"\033[2m[📅 reading calendar ({days} days)...]\033[0m")
    _bump_tool()

    try:
        from lhassa.tools.graph import read_calendar
        token = await _graph_token()
        events = await read_calendar(token, days_ahead=days)

        if not events:
            await params.result_callback(f"No events in the next {days} day{'s' if days != 1 else ''}.")
            return

        lines = []
        for ev in events:
            subject = ev.get("subject", "(no title)")
            start = ev.get("start", {}).get("dateTime", "")[:16].replace("T", " ")
            end = ev.get("end", {}).get("dateTime", "")[:16].replace("T", " ")
            loc = ev.get("location", {}).get("displayName", "")
            loc_str = f" @ {loc}" if loc else ""
            lines.append(f"{start} – {end}{loc_str}: {subject}")

        await params.result_callback("\n".join(lines))
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"read_calendar error: {e}")
        await params.result_callback(f"Error reading calendar: {e}")


async def handle_create_event(params: FunctionCallParams):
    subject = params.arguments.get("subject", "")
    start = params.arguments.get("start", "")
    end = params.arguments.get("end", "")
    body = params.arguments.get("body", "")
    location = params.arguments.get("location", "")
    logger.info(f"[tool] create_event subject={subject!r} start={start}")
    print(f"\033[2m[📅 creating event: {subject}]\033[0m")
    _bump_tool()

    try:
        from lhassa.tools.graph import create_event
        token = await _graph_token()
        await create_event(token, subject=subject, start=start, end=end, body=body, location=location)
        await params.result_callback(
            f"Event '{subject}' created for {start[:16].replace('T', ' ')}."
        )
    except RuntimeError as e:
        await params.result_callback(str(e))
    except Exception as e:
        logger.error(f"create_event error: {e}")
        await params.result_callback(f"Error creating event: {e}")
