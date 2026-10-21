# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bare-``lhassa`` REPL surface: welcome card, slash menu, toolbar."""

from __future__ import annotations

from datetime import datetime

from prompt_toolkit.document import Document
from rich.console import Console

from lhassa.cli import repl_ui

STATUS = {
    "instance_id": "inst_test", "heartbeat_seat": "claude", "chat_provider": "prov",
    "model": "some-model", "budget": 0, "pct_used": 0.0, "cost_usd": 0.01,
    "org_live": True, "approvals": 2, "tasks": 0, "beat_age": 120, "health_failing": (),
}
AGENDA = {
    "events": ["09:00 Standup"],
    "approvals": ["Retire capability 'x'", "Run code task"],
    "tasks": [("temple_run", "Thu 09:00"), ("send_reminder", "Thu 17:00")],
}


def _text(renderable, width=120) -> str:
    console = Console(width=width, record=True, force_terminal=False)
    console.print(renderable)
    return console.export_text()


def test_welcome_shows_seats_and_owner_items_only():
    text = _text(repl_ui.welcome({"status": STATUS, "agenda": AGENDA}, cwd="/tmp/x", version="v9"))
    assert "some-model" in text and "claude" in text and "last beat 2m ago" in text
    assert "09:00 Standup" in text and "2 approvals" in text and "Run code task" in text
    assert "reminder  Thu 17:00" in text
    assert "temple_run" not in text  # internal upkeep stays out of the card
    assert "/tmp/x" in text and "v9" in text


def test_welcome_degrades_without_agenda_or_status():
    text = _text(repl_ui.welcome({"status": {}, "agenda": None}), width=70)
    assert "Lhassa" in text and "Waiting on you" in text
    text = _text(repl_ui.welcome({"status": {**STATUS, "approvals": 0}, "agenda": None}))
    assert "Nothing. The wheel turns on its own." in text


def test_welcome_names_failing_health():
    text = _text(repl_ui.welcome({"status": {**STATUS, "health_failing": ("disk",)}, "agenda": None}))
    assert "health" in text and "disk" in text


def test_slash_completer_filters_and_describes():
    comp = repl_ui.SlashCompleter()
    got = list(comp.get_completions(Document("/ap"), None))
    assert [c.text for c in got] == ["/approvals", "/approve "]
    assert all(c.display_meta_text for c in got)
    assert list(comp.get_completions(Document("hello"), None)) == []
    assert list(comp.get_completions(Document("/approve 12"), None)) == []


def test_help_lists_every_command_once():
    help_text = repl_ui.help_markup()
    for cmd, _, _ in repl_ui.SLASH_COMMANDS:
        assert help_text.count(cmd + " ") + help_text.count(cmd + "[") >= 1
    assert set(repl_ui.ALIASES.values()) <= {c for c, _, _ in repl_ui.SLASH_COMMANDS}


def test_agenda_markup():
    assert "could not be read" in repl_ui.agenda_markup(None)
    text = repl_ui.agenda_markup(AGENDA)
    assert "09:00 Standup" in text and "reminder [dim]Thu 17:00" in text
    assert "temple_run" not in text and "1 upkeep tasks" in text


def test_toolbar_fragments_use_classed_roles():
    frags = repl_ui.toolbar_fragments(STATUS)
    styles = {s for s, _ in frags if s}
    assert all(s.startswith("class:st.") for s in styles)
    for s in styles:
        assert s.removeprefix("class:") in repl_ui.TOOLBAR_STYLE


def test_greeting_by_hour():
    assert repl_ui._greeting(datetime(2026, 1, 1, 9)) == "Good morning."
    assert repl_ui._greeting(datetime(2026, 1, 1, 15)) == "Good afternoon."
    assert repl_ui._greeting(datetime(2026, 1, 1, 21)) == "Good evening."
    assert repl_ui._greeting(datetime(2026, 1, 1, 2)) == "Up late."


def test_history_lives_next_to_the_brain_db():
    assert repl_ui.history_path().name == "repl_history"
