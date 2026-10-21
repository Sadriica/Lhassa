# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bare-``lhassa`` REPL surface: welcome card, slash menu, toolbar."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from prompt_toolkit.document import Document
from rich.console import Console

from lhassa.cli import repl_ui
from lhassa.personal.agenda import Commitment

STATUS = {
    "instance_id": "inst_test", "heartbeat_seat": "claude", "chat_provider": "prov",
    "model": "some-model", "budget": 0, "pct_used": 0.0, "cost_usd": 0.01,
    "org_live": True, "approvals": 2, "tasks": 0, "beat_age": 120, "health_failing": (),
}
TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 1, 7, 13, 30, tzinfo=TZ)  # a Wednesday; e13 is under way


def _ev(day, hour, summary, location="", minutes=60):
    start = datetime(2026, 1, day, hour, 0, tzinfo=TZ)
    return {"start": start, "end": start + timedelta(minutes=minutes), "summary": summary,
            "location": location, "calendar": "c", "all_day": False}


DAYS = [
    {"date": date(2026, 1, 7), "events": [_ev(7, 9, "Standup"), _ev(7, 16, "Seminar", "Room 4")]},
    {"date": date(2026, 1, 8), "events": []},
]
COMMITMENTS = [
    Commitment("reminder", "Call the bank", datetime(2026, 1, 9, 14, 0, tzinfo=timezone.utc)),
    Commitment("goal", "Ship the paper", datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc), 604800),
]
DATA = {"status": STATUS, "days": DAYS, "commitments": COMMITMENTS, "calendar": True}


def _text(renderable, width=120) -> str:
    console = Console(width=width, record=True, force_terminal=False)
    console.print(renderable)
    return console.export_text()


def _welcome(data, lang="en", **kw):
    return _text(repl_ui.welcome(data, lang=lang, cwd=kw.pop("cwd", "/tmp/x"), **kw))


def test_welcome_speaks_first_person():
    text = _welcome(DATA, version="v9", width=120)
    assert "My last heartbeat was 2m ago" in text
    assert "I think with some-model and keep watch with claude." in text
    assert "The org is live." in text and "We are in /tmp/x." in text
    assert "v9" in text and "▬▬" in text and "▄█████▄" in text


def test_calendar_is_laid_out_by_day():
    lines = [t.plain for t in repl_ui.calendar_lines(DAYS, has_calendar=True, lang="en", now=NOW)]
    assert lines[0] == "Today  Wed 7 Jan"
    assert lines[1].startswith("· 09:00  Standup")  # past: dimmed bullet
    assert lines[2] == "◆ 16:00  Seminar  Room 4"
    assert "Tomorrow  Thu 8 Jan" in lines and "  free" in lines


def test_today_keeps_whats_ahead_when_space_is_short():
    many = [{"date": date(2026, 1, 7), "events": [_ev(7, h, f"e{h}") for h in range(8, 18)]}]
    lines = [t.plain for t in repl_ui.calendar_lines(many, has_calendar=True, lang="en", now=NOW, per_day=3)]
    assert [ln.split()[-1] for ln in lines[1:4]] == ["e13", "e14", "e15"]
    assert lines[4].strip() == "+7 more"


def test_calendar_states_without_a_feed():
    def first(**kw):
        return [t.plain for t in repl_ui.calendar_lines(lang="es", **kw)]

    assert "Sin calendario conectado" in first(days=DAYS, has_calendar=False)[1]
    assert "no respondió" in first(days=None, has_calendar=True)[1]


def test_commitments_with_dates_and_recurrence():
    lines = [t.plain for t in repl_ui.commitment_lines(COMMITMENTS, STATUS, lang="en")]
    assert lines[0] == "Your commitments"
    assert lines[1].startswith("◇ Fri 9 Jan") and lines[1].endswith("Call the bank")
    assert "Ship the paper" in lines[2] and "↻ goal I keep watch on · every 7 days" in lines[3]
    assert "2 approvals await your word" in lines[-1]
    empty = [t.plain for t in repl_ui.commitment_lines([], {}, lang="es")]
    assert "Nada pendiente" in empty[1]


def test_welcome_two_columns_or_stacked():
    wide = _welcome(DATA, width=120)
    row = next(ln for ln in wide.splitlines() if "Today" in ln)
    assert "Your commitments" in row
    narrow = _welcome(DATA, width=70)
    assert not any("Today" in ln and "Your commitments" in ln for ln in narrow.splitlines())


def test_welcome_in_spanish():
    text = _welcome(DATA, lang="es", width=120)
    assert "Mi último latido fue hace 2m" in text and "Tus compromisos" in text
    assert "Hoy" in text and "Mañana" in text
    assert "Pienso con some-model y vigilo con claude." in text
    assert set(repl_ui.VOICE["en"]) == set(repl_ui.VOICE["es"])


def test_welcome_degrades_without_data():
    text = _welcome({"status": {}}, width=50)
    assert "Lhassa" in text and "No heartbeat yet today" in text
    assert "No calendar connected" in text and "Nothing pending" in text


def test_welcome_names_failing_health():
    text = _welcome({**DATA, "status": {**STATUS, "health_failing": ("disk",)}})
    assert "Heads up: disk need a look." in text


def test_agenda_view_is_untrimmed():
    many = [{"date": date(2026, 1, 7), "events": [_ev(7, h, f"e{h}") for h in range(8, 18)]}]
    text = _text(repl_ui.agenda_view({**DATA, "days": many}, lang="en"))
    assert "e8" in text and "e17" in text and "more" not in text


def test_short_date():
    assert repl_ui.short_date(date(2026, 10, 21), "es") == "mié 21 oct"
    assert repl_ui.short_date(date(2026, 10, 21), "en") == "Wed 21 Oct"


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


def test_toolbar_fragments_use_classed_roles():
    frags = repl_ui.toolbar_fragments(STATUS)
    assert "".join(t for _, t in frags)
    styles = {s for s, _ in frags if s}
    assert all(s.startswith("class:st.") for s in styles)
    for s in styles:
        assert s.removeprefix("class:") in repl_ui.TOOLBAR_STYLE


def test_greeting_by_hour():
    def greet(hour):
        return repl_ui._greeting(datetime(2026, 1, 1, hour), lang="en")

    assert greet(9).startswith("Good morning.")
    assert greet(15).startswith("Good afternoon.")
    assert greet(21).startswith("Good evening.")
    assert greet(2) == "Still up? So am I."


def test_history_lives_next_to_the_brain_db():
    assert repl_ui.history_path().name == "repl_history"
