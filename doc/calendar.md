# Calendar — two ways to connect

Lhassa can read your calendar into `/today` and the daily briefing, and turn a
reminder into a calendar event. There are **two paths** — pick the one your
account allows. If you're unsure which fits, just ask Lhassa (`/ai how do I
connect my calendar?`) and it will walk you through it.

## Path A — ICS (no admin, works for everyone)

The zero-friction path: files and feeds, no app registration, no API, no IT
approval. Ideal for **institutional calendars** an admin would otherwise gate.

**Write** — a tappable event file. Built in, nothing to configure:
- `/remind <what> <when>` gives you an internal reminder **and** delivers a
  `.ics` file. `/event <what> <when>` delivers just the file. One tap adds it to
  any calendar (institutional Outlook, Google, iPhone).
- Grounded in `lhassa/calendar/ics.py`; the file rides the sender-of-record seam
  (`org/deliver.py`) — a registered artifact with a per-brain delivery log.

**Read** — subscribe to your calendar's published feed:
1. Get the secret ICS URL from your calendar. In Outlook web: **Settings →
   Calendar → Shared calendars → Publish a calendar → pick the calendar → copy
   the ICS link**. (Google Calendar: Settings → *Integrate calendar* → *Secret
   address in iCal format*.)
2. Put it in `.env` (it is a secret — read access to your calendar; it lives in
   the gitignored `.env`, never in the repo):
   ```
   LHASSA_CALENDAR_ICS_URL=<your secret .ics link>
   ```
3. `/today`, `/tomorrow` and the briefing now show your real events alongside
   tasks — e.g. `📅 15:00 Cálculo III · Bloque 19`.

Read-only, `$0`, deterministic (`lhassa/calendar/feed.py`): the feed is fetched
and cached (`LHASSA_CALENDAR_FEED_TTL`, 1h default; a stale copy is served if the
network is down), parsed with no model. Recurring weekly classes, Windows
timezones (e.g. `SA Pacific Standard Time` = Bogotá) and all-day events are
handled; exotic monthly/yearly recurrences are best-effort.

### Multiple calendars

`LHASSA_CALENDAR_ICS_URL` above is a convenience shortcut for the common case of
**one** calendar. If you have several — an institutional one, a personal one, a
project one — subscribe each with a **name** so `/today` shows which calendar
each event is from:

```
/calendar add EAFIT   <your institutional secret .ics link>
/calendar add Personal <your personal secret .ics link>
/calendars               # list what's configured (name + a masked url)
/calendar remove EAFIT   # drop one
```

(the CLI equivalent is `lhassa calendar add|list|remove`). Once more than one
calendar is configured, `/today` tags each event with its source:

```
📅 15:00 Cálculo III · EAFIT · Bloque 19
📅 19:00 Team sync · Personal
```

With a single calendar configured (the env var alone, or exactly one added
entry) the name is omitted — it would just be redundant noise.

Under the hood: the named calendars live in a small JSON store
(`~/.lhassa/calendars.json`, `LHASSA_CALENDARS_FILE`) — a list of
`{"name", "url"}`. The env var is merged in automatically as an implicit
`"default"` feed whenever it's set and its url isn't already stored under a
name, so nothing breaks for someone using only `.env`. Each feed keeps its OWN
cache file (keyed by a hash of its url) so two calendars never clobber each
other's cached copy; a single unreachable feed is skipped — the rest still show.

## Path B — Microsoft Graph app (read + write via API)

For accounts where you **can** register an app (personal Outlook.com, or an org
that permits it). Full two-way sync — Lhassa reads and writes events through the
Graph API directly (`lhassa/tools/graph.py`, `read_calendar` / `create_event`).

1. Register an app at [portal.azure.com](https://portal.azure.com) →
   App registrations → New registration (name it, "any org directory + personal
   accounts", no redirect URI). Under **Authentication**, turn on **Allow public
   client flows**. Under **API permissions**, add the delegated Microsoft Graph
   permissions `Mail.Read`, `Mail.Send`, `Calendars.Read`, `Calendars.ReadWrite`.
2. Copy the **Application (client) ID** → `MS_CLIENT_ID`, and the **Directory
   (tenant) ID** → `MS_TENANT_ID` (use `common` for a personal Outlook.com
   account).
   ```
   MS_CLIENT_ID=<application client id>
   MS_TENANT_ID=<directory tenant id, or "common">
   ```
3. First use: Lhassa prints a `microsoft.com/devicelogin` URL + code; you
   authenticate once in a browser and the token is cached
   (`~/.lhassa/ms_token_cache.json`, auto-refreshed).

**The org-account caveat:** many organizations block user consent or app
registration entirely. If you hit "needs admin approval", either an IT admin
grants consent (the *Grant admin consent* button on API permissions), or you use
**Path A** — the ICS feed reads the same institutional calendar with no approval.

## Which to pick

| | Path A — ICS | Path B — Graph app |
|---|---|---|
| Registration / admin | none | app + possible admin consent |
| Read your calendar | ✅ feed | ✅ API |
| Write events | ✅ tappable `.ics` file | ✅ direct to the calendar |
| Institutional accounts | always works | only if IT allows |
| Freshness | cached (minutes) | real time |

Both are `$0` and deterministic — creating/reading events never touches a model.
Path A is the default recommendation for institutional calendars; Path B is the
smoother experience when your account permits it.
