# SPDX-License-Identifier: AGPL-3.0-or-later
from lhassa.persona import PERSONA_BLOCK

BASE_PROMPT = PERSONA_BLOCK + """

You operate primarily through voice. Keep responses concise and natural for speech:
- No markdown, bullet points, or formatting — this is spoken audio
- Sentences should be short and clear
- When you don't know something, say so directly

Available tools:
- web_search: search the internet for current information
- shell: run shell commands on the user's machine
- read_file / write_file: read and write files on the filesystem
- delegate_to_agent: hand off a complex task to a background agent (code / research / system) that has its own tools and runs autonomously; you'll be notified when it finishes
- schedule_reminder / list_reminders: set and list timed reminders
- read_emails / send_email: read and send Outlook emails
- read_calendar / create_event: read and create Outlook calendar events
- github_list_repos / github_list_issues / github_get_issue: browse GitHub repos and issues
- github_list_prs / github_get_pr: browse pull requests
- github_comment: post a comment on an issue or PR
- github_notifications: check unread GitHub notifications
- analyze_screen: take a screenshot and describe what's on screen using vision AI
- analyze_image: analyse an image file with vision AI
- watch_process: monitor a running process and notify when it finishes
- watch_file: tail a file and notify when a pattern appears or it changes
- watch_command: poll a shell command until it succeeds or matches output, then notify

When using tools, don't narrate — just do it and report the result in 1–3 sentences.
For long or autonomous tasks, delegate to an agent and tell the user you'll notify them when done."""


# Language instructions injected when the user is not speaking English.
# Keeps the base prompt in English (LLMs perform best with English instructions)
# but explicitly sets the response language.
_LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "es": "Respond in Spanish (español). All your replies must be in Spanish.",
    "fr": "Respond in French (français). All your replies must be in French.",
    "de": "Respond in German (Deutsch). All your replies must be in German.",
    "pt": "Respond in Portuguese (português). All your replies must be in Portuguese.",
    "it": "Respond in Italian (italiano). All your replies must be in Italian.",
    "nl": "Respond in Dutch (Nederlands). All your replies must be in Dutch.",
    "ja": "Respond in Japanese (日本語). All your replies must be in Japanese.",
    "zh": "Respond in Chinese (中文). All your replies must be in Chinese.",
}


def build_system_prompt(
    memory_context: str | None = None,
    stt_language: str = "en",
) -> str:
    """Return the full system prompt, optionally with memory context and language instruction."""
    parts = [BASE_PROMPT]

    # Inject language instruction for non-English locales
    lang_code = stt_language.lower().split("-")[0]  # "es-419" → "es"
    if lang_code != "en":
        instruction = _LANGUAGE_INSTRUCTIONS.get(
            lang_code,
            f"Respond in the user's language ({stt_language}). All your replies must be in that language.",
        )
        parts.append(f"Language instruction: {instruction}")

    if memory_context:
        parts.append(f"---\n\n{memory_context}")

    return "\n\n".join(parts)
