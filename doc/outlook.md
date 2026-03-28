# Outlook Integration (Microsoft Graph)

Potts can read your emails, send emails, read your calendar, and create calendar events — all through voice commands. This is powered by the Microsoft Graph API and requires a free Azure app registration.

---

## Prerequisites

- A **Microsoft 365 account** (work, school, or personal — Outlook.com also works)
- Access to the **Azure portal** at [portal.azure.com](https://portal.azure.com) (free, uses your Microsoft account)

---

## Step-by-Step Azure Setup

### 1. Go to App Registrations

1. Sign in to [portal.azure.com](https://portal.azure.com)
2. Search for **"App registrations"** in the top search bar
3. Click **New registration**

### 2. Register the application

Fill in the form:

| Field | Value |
|---|---|
| **Name** | `Potts` (or anything you like) |
| **Supported account types** | Accounts in this organizational directory only — **or** Personal Microsoft accounts |
| **Redirect URI** | Leave blank |

Click **Register**.

### 3. Copy your IDs

On the app's overview page, copy:
- **Application (client) ID** → this is your `MS_CLIENT_ID`
- **Directory (tenant) ID** → this is your `MS_TENANT_ID`

> For personal Microsoft accounts (Outlook.com, Hotmail, etc.), use `consumers` as the tenant ID. For work/school accounts, use the actual tenant ID (a UUID).

### 4. Enable public client flows

1. In the left sidebar, click **Authentication**
2. Scroll down to **Advanced settings**
3. Set **Allow public client flows** to **Yes**
4. Click **Save**

This is required for the device flow authentication that Potts uses.

### 5. Add API permissions

1. In the left sidebar, click **API permissions**
2. Click **Add a permission**
3. Choose **Microsoft Graph** → **Delegated permissions**
4. Search for and add these four permissions:
   - `Mail.Read`
   - `Mail.Send`
   - `Calendars.Read`
   - `Calendars.ReadWrite`
5. Click **Add permissions**

You do **not** need to click "Grant admin consent" for delegated personal permissions.

---

## Configure Your .env File

Add the two IDs to your `.env`:

```
MS_TENANT_ID=your-tenant-id-here
MS_CLIENT_ID=your-client-id-here
```

No other setup is required. Potts will prompt you to authenticate the first time you use an email or calendar command.

---

## First Authentication: Device Flow

The first time you ask Potts to do anything with email or calendar, it will pause and print an authentication prompt to the terminal:

```
============================================================
Potts needs access to your Microsoft account.
Go to:  https://microsoft.com/devicelogin
Code:   ABCD-1234
============================================================
```

1. Open that URL in any browser (on any device)
2. Enter the code shown
3. Sign in with your Microsoft account
4. Accept the permissions

Back in the terminal, Potts will continue automatically once authentication completes:

```
Microsoft Graph: authenticated successfully.
```

Then it will proceed with your original request — reading emails, checking the calendar, etc.

---

## Token Cache

After the first authentication, the access token and refresh token are saved to:

```
~/.potts/ms_token_cache.json
```

MSAL handles refresh token rotation automatically. As long as you use Potts regularly, you will never need to re-authenticate. The cache survives restarts — Potts loads it silently on each startup.

**Security note:** The token cache contains a refresh token that grants access to your Microsoft account. Keep `~/.potts/` private (`chmod 700 ~/.potts`).

---

## Troubleshooting

### Corrupted cache

If the token cache file gets corrupted (e.g. a partial write during a crash), Potts detects it automatically:

```
Token cache corrupted — deleting and re-authenticating.
```

The file is deleted and you'll see the device flow prompt on your next email/calendar command. This is safe — just complete the device flow again.

### Expired token / re-authentication triggered

MSAL's refresh tokens have a rolling expiry of 90 days (for personal accounts). If you haven't used Potts in a while, the refresh may fail and you'll see the device flow again. Complete it and you're good for another 90 days.

### "MS_TENANT_ID and MS_CLIENT_ID are required"

You asked for email/calendar but didn't set the env vars. Add them to your `.env` file and restart Potts.

### "Device flow failed"

Usually means either:
- The app registration wasn't set up correctly (check that public client flows are enabled)
- Your tenant ID is wrong (try `common` or `consumers` for personal accounts)

### Permission errors from Graph API

If you get 403 errors from the Graph API, go back to the Azure portal and verify all four permissions are listed. Sometimes the wrong permission type (application vs. delegated) is added by mistake.

---

## Example Voice Commands

```
You: Potts, do I have any new emails?
Potts: You have three unread emails. One from Alice about the Q2 roadmap,
       one from GitHub with a PR notification, and a newsletter.

You: Potts, send an email to alice@company.com, subject "Re: Q2 roadmap",
     body "Sounds good, let's review it on Thursday."
Potts: Email sent to alice@company.com.

You: Potts, what's on my calendar this week?
Potts: You have 5 events this week. Standup every morning at 9:30,
       a one-on-one Thursday at 14:00, and a team dinner Friday at 19:00.

You: Potts, create a calendar event called "Doctor appointment"
     tomorrow at 10am for 1 hour.
Potts: Done. Doctor appointment added for tomorrow at 10:00.
```

---

## Disabling the Integration

If you don't use Microsoft 365, simply don't set `MS_TENANT_ID` and `MS_CLIENT_ID`. Potts works fine without them — the email and calendar tools will just return an error message if you try to use them:

```
MS_TENANT_ID and MS_CLIENT_ID are required for Outlook integration. Add them to your .env file.
```

Everything else (voice, web search, shell, reminders, agents) works without the Microsoft integration.
