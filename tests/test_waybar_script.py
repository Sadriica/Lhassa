# SPDX-License-Identifier: AGPL-3.0-or-later
"""The standalone Waybar widget script (scripts/waybar/lhassa-waybar)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "waybar" / "lhassa-waybar"
NOW = 10_000.0


def _load():
    loader = SourceFileLoader("lhassa_waybar_script", str(SCRIPT))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


wb = _load()


def state(**over):
    base = {
        "v": 1, "written_at": NOW - 5, "cost_usd": 0.42, "budget": 1000, "pct_used": 0.1,
        "soft_pct": 0.8, "approvals": 0, "tasks": 0, "health_failing": [],
        "dashboard_url": "http://127.0.0.1:7766",
    }
    base.update(over)
    return base


def render(tmp_path, payload=None, raw=None, env=None):
    path = tmp_path / "waybar.json"
    if raw is not None:
        path.write_text(raw)
    elif payload is not None:
        path.write_text(json.dumps(payload))
    return wb.render(str(path), NOW, {"LHASSA_WAYBAR_LANG": "en", **(env or {})})


def test_ok(tmp_path):
    out = render(tmp_path, state())
    assert out["class"] == "ok"
    assert "$0.42" in out["text"] and "·" not in out["text"]
    assert "Spent today: $0.42" in out["tooltip"]
    assert "Budget used: 10%" in out["tooltip"]
    assert "All calm" in out["tooltip"]
    assert set(out) == {"text", "tooltip", "class"}


def test_pending_approvals_warn(tmp_path):
    out = render(tmp_path, state(approvals=3, tasks=2))
    assert out["class"] == "warn"
    assert out["text"].endswith("$0.42 · 3")
    assert "Approvals waiting: 3" in out["tooltip"]
    assert "Tasks queued: 2" in out["tooltip"]
    assert "All calm" not in out["tooltip"]


def test_high_budget_warn_and_exhausted_alert(tmp_path):
    assert render(tmp_path, state(pct_used=0.85))["class"] == "warn"
    assert render(tmp_path, state(pct_used=1.0))["class"] == "alert"


def test_unlimited_budget_never_warns(tmp_path):
    out = render(tmp_path, state(budget=0, pct_used=0.0))
    assert out["class"] == "ok"
    assert "Budget used" not in out["tooltip"]


def test_a_health_check_at_alert_level_beats_warn(tmp_path):
    out = render(tmp_path, state(approvals=1, health_failing=["disk", "net"], health_alert=["disk"]))
    assert out["class"] == "alert"
    assert "Health: disk, net" in out["tooltip"]


def test_a_health_warning_alone_is_a_warn_not_an_alert(tmp_path):
    """A failing check below alert level (e.g. a system unit that is not Lhassa's)."""
    out = render(tmp_path, state(health_failing=["failed_units"], health_alert=[]))
    assert out["class"] == "warn"
    assert "Health: failed_units" in out["tooltip"]


def test_an_older_state_file_without_health_alert_degrades_to_warn(tmp_path):
    payload = state(health_failing=["disk"])
    payload.pop("health_alert", None)
    assert render(tmp_path, payload)["class"] == "warn"


def test_old_file_is_stale(tmp_path):
    out = render(tmp_path, state(written_at=NOW - 3600))
    assert out["class"] == "stale"
    assert "no brain" in out["text"]
    assert "1h00m" in out["tooltip"]


def test_stale_threshold_is_configurable(tmp_path):
    payload = state(written_at=NOW - 100)
    assert render(tmp_path, payload)["class"] == "stale"
    assert render(tmp_path, payload, env={"LHASSA_WAYBAR_STALE_S": "300"})["class"] == "ok"


def test_future_timestamp_is_not_stale(tmp_path):
    assert render(tmp_path, state(written_at=NOW + 50))["class"] == "ok"


def test_missing_file_is_idle(tmp_path):
    out = render(tmp_path)
    assert out["class"] == "idle" and "no brain" in out["text"]


@pytest.mark.parametrize("raw", ["", "{not json", "[]", "null", '{"cost_usd": 1}', '{"written_at": "x"}'])
def test_corrupt_file_is_idle_not_a_crash(tmp_path, raw):
    out = render(tmp_path, raw=raw)
    assert out["class"] == "idle"
    assert "unreadable" in out["tooltip"]


def test_garbage_field_types_degrade(tmp_path):
    out = render(tmp_path, state(cost_usd="lots", approvals="x", health_failing="oops", pct_used=None))
    assert out["class"] == "ok" and "$0.00" in out["text"]


def test_spanish(tmp_path):
    out = render(tmp_path, env={"LHASSA_WAYBAR_LANG": "es"})
    assert "sin cerebro" in out["text"]
    assert "Aprobaciones" in render(tmp_path, state(approvals=1), env={"LHASSA_WAYBAR_LANG": "es"})["tooltip"]


def test_markup_is_escaped(tmp_path):
    out = render(tmp_path, state(health_failing=["<b>&x"]))
    assert "<b>" not in out["tooltip"] and "&lt;b&gt;&amp;x" in out["tooltip"]


def test_state_path_rules(tmp_path):
    assert wb.state_path({"LHASSA_WAYBAR_STATE_FILE": "/a/b"}) == "/a/b"
    assert wb.state_path({"XDG_RUNTIME_DIR": str(tmp_path)}) == str(tmp_path / "lhassa" / "waybar.json")
    assert wb.state_path({}).endswith("waybar.json")


def test_writer_and_script_agree_on_the_path(tmp_path):
    from lhassa.views import waybar_state

    for env in ({}, {"XDG_RUNTIME_DIR": str(tmp_path)}, {"XDG_RUNTIME_DIR": "/nope/missing"},
                {"LHASSA_WAYBAR_STATE_FILE": "/x/y"}):
        assert str(waybar_state.state_path(env)) == wb.state_path(env)


def test_dashboard_url_only_local(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"dashboard_url": "http://127.0.0.1:9000"}))
    assert wb.dashboard_url(str(path)) == "http://127.0.0.1:9000"
    path.write_text(json.dumps({"dashboard_url": "https://evil.example/x"}))
    assert wb.dashboard_url(str(path)) == wb.DEFAULT_URL
    path.write_text("junk")
    assert wb.dashboard_url(str(path)) == wb.DEFAULT_URL
    assert wb.dashboard_url(str(tmp_path / "nope")) == wb.DEFAULT_URL


def test_end_to_end_subprocess_is_fast_and_valid(tmp_path):
    path = tmp_path / "w.json"
    path.write_text(json.dumps(state(written_at=time.time(), approvals=1)))
    env = {"PATH": "/usr/bin:/bin", "LHASSA_WAYBAR_STATE_FILE": str(path), "LHASSA_WAYBAR_LANG": "en"}
    t0 = time.perf_counter()
    done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
    elapsed = time.perf_counter() - t0
    assert done.returncode == 0
    assert json.loads(done.stdout)["class"] == "warn"
    assert elapsed < 1.0  # generous CI bound; typically ~30 ms


def test_subprocess_without_state_dir_still_prints_json(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "LHASSA_WAYBAR_STATE_FILE": str(tmp_path / "none" / "w.json")}
    done = subprocess.run([sys.executable, str(SCRIPT), "status"], env=env, capture_output=True, text=True, timeout=10)
    assert done.returncode == 0 and json.loads(done.stdout)["class"] == "idle"


def test_unknown_command_exits_2():
    done = subprocess.run([sys.executable, str(SCRIPT), "wipe"], capture_output=True, text=True, timeout=10)
    assert done.returncode == 2


def test_script_is_stdlib_only_and_executable():
    text = SCRIPT.read_text()
    assert "import lhassa" not in text and "from lhassa" not in text
    assert SCRIPT.stat().st_mode & 0o111
    assert chr(0x2014) not in text


# ── the click: which dashboard section opens ─────────────────────────────────

def _section(tmp_path, payload=None, section=None):
    path = tmp_path / "waybar.json"
    if payload is not None:
        path.write_text(json.dumps(payload))
    return wb.section_url(str(path), section)


def test_the_click_opens_approvals_by_default(tmp_path):
    assert _section(tmp_path, state(approvals=3)) == "http://127.0.0.1:7766/#approvals"
    assert _section(tmp_path, state()) == "http://127.0.0.1:7766/#approvals"


def test_the_click_opens_health_when_only_a_check_is_failing(tmp_path):
    assert _section(tmp_path, state(health_failing=["disk"])) == "http://127.0.0.1:7766/#fleet"
    assert _section(tmp_path, state(approvals=2, health_failing=["disk"])).endswith("#approvals")


def test_an_explicit_known_section_wins_and_an_unknown_one_is_ignored(tmp_path):
    assert _section(tmp_path, state(), "integrity").endswith("#integrity")
    assert _section(tmp_path, state(), "../evil").endswith("#approvals")  # never a free-form fragment


def test_the_click_without_a_state_file_still_gives_a_local_url(tmp_path):
    assert _section(tmp_path) == "http://127.0.0.1:7766/#approvals"


# ── v2: icon + counter, tooltip sections, drawer modules ─────────────────────

def state2(**over):
    base = state(v=2, **{"class": "ok"})
    base.update({
        "agents": {"window_s": 300, "total": 3, "hosts": {"claude-code": 2, "codex": 1},
                   "sessions": [{"host": "claude-code", "repo": "lhassa"},
                                {"host": "claude-code", "repo": "witara"},
                                {"host": "codex", "repo": "lhassa"}]},
        "attention": {"count": 0, "items": []},
        "system": {"power_mode": "balanced", "temp_c": 62.4, "temp_level": "ok", "cpu_pct": 12,
                   "battery_pct": 80, "charging": False, "suggestion": None},
        "today": {"events": 120, "commits": 4, "pushes": 1, "api_usd": 0.42, "top_repo": "lhassa"},
        "lhassa": {"health": "notice", "digest_age_s": 720, "model": ""},
    })
    base.update(over)
    return base


APPROVAL = {"kind": "approval", "id": "ap_123", "label": "git_push", "source": "approvals"}
CI = {"kind": "ci", "label": "lhassa", "source": "GitHub Actions"}


def test_v2_at_rest_is_only_the_icon(tmp_path):
    out = render(tmp_path, state2())
    assert out["text"] == wb.WHEEL and out["class"] == "ok"
    assert set(out) == {"text", "tooltip", "class"}


def test_v2_counter_shows_attention_by_default(tmp_path):
    items = [APPROVAL, CI]
    out = render(tmp_path, state2(**{"class": "warn"}, attention={"count": 2, "items": items}))
    assert out["text"] == wb.WHEEL + " ⚠ 2" and out["class"] == "warn"


def test_v2_counter_modes(tmp_path):
    payload = state2(attention={"count": 1, "items": [APPROVAL]})
    (tmp_path / "waybar.json").write_text(json.dumps(payload))
    env = {"LHASSA_WAYBAR_LANG": "en"}
    for mode, expect in (("agents", "● 3"), ("temp", "62°"), ("spend", "$0.42"), ("attention", "⚠ 1")):
        (tmp_path / "waybar-mode").write_text(mode)
        assert wb.render(str(tmp_path / "waybar.json"), NOW, env)["text"] == wb.WHEEL + " " + expect
    (tmp_path / "waybar-mode").write_text("bogus")  # an unknown mode falls back to attention
    assert wb.render(str(tmp_path / "waybar.json"), NOW, env)["text"].endswith("⚠ 1")


def test_v2_tooltip_has_the_four_sections_and_only_names(tmp_path):
    items = [APPROVAL, {"kind": "review", "label": "witara", "n": 2, "source": "lhassa review"}]
    out = render(tmp_path, state2(attention={"count": 2, "items": items}))
    tip = out["tooltip"]
    for head in ("<b>Now</b>", "<b>Needs your attention</b>", "<b>Today</b>", "<b>Lhassa</b>"):
        assert head in tip
    assert "claude-code 2: lhassa, witara" in tip and "codex 1: lhassa" in tip
    assert "balanced · 62°C · CPU 12% · 80%" in tip
    assert "approval: git_push  [approvals]" in tip
    assert "critical finding: witara (2)  [lhassa review]" in tip
    assert "120 events · 4 commits · 1 pushes · API $0.42 · most active: lhassa" in tip
    assert "health digest notice, 12m ago" in tip
    assert "Counter shows: attention" in tip


def test_v2_tooltip_without_attention_or_lhassa_extras(tmp_path):
    payload = state2(lhassa={"health": "", "digest_age_s": None, "model": ""})
    payload["agents"] = {"window_s": 300, "total": 0, "hosts": {}, "sessions": []}
    tip = render(tmp_path, payload)["tooltip"]
    assert "No active agents" in tip and "Nothing needs you" in tip
    assert "digest" not in tip and "local model" not in tip


def test_v2_tooltip_is_spanish_on_request(tmp_path):
    tip = render(tmp_path, state2(), env={"LHASSA_WAYBAR_LANG": "es"})["tooltip"]
    assert "<b>Ahora</b>" in tip and "<b>Necesita tu atención</b>" in tip and "<b>Hoy</b>" in tip


def test_v2_markup_in_names_is_escaped(tmp_path):
    payload = state2(attention={"count": 1, "items": [{"kind": "ci", "label": "<i>x&", "source": "s"}]})
    payload["agents"]["sessions"] = [{"host": "orca", "repo": "<b>"}]
    tip = render(tmp_path, payload)["tooltip"]
    assert "<i>" not in tip and "&lt;i&gt;x&amp;" in tip and "&lt;b&gt;" in tip


def test_v2_stale_and_missing_still_say_no_brain(tmp_path):
    assert render(tmp_path, state2(written_at=NOW - 3600))["class"] == "stale"
    assert render(tmp_path / "empty")["class"] == "idle"


def mod(tmp_path, name, payload=None, env=None):
    path = tmp_path / "waybar.json"
    if payload is not None:
        path.write_text(json.dumps(payload))
    return wb.module(name, str(path), NOW, {"LHASSA_WAYBAR_LANG": "en", **(env or {})})


def test_agents_module_counts_per_host(tmp_path):
    out = mod(tmp_path, "agents", state2())
    assert out["text"] == "claude 2  codex 1" and out["class"] == "ok"
    assert "claude-code 2: lhassa, witara" in out["tooltip"]


def test_agents_module_with_none_says_so(tmp_path):
    payload = state2()
    payload["agents"] = {"window_s": 300, "total": 0, "hosts": {}, "sessions": []}
    out = mod(tmp_path, "agents", payload)
    assert out["text"] == "no agents" and out["class"] == "idle"


def test_attention_module_is_hidden_when_nothing_needs_you(tmp_path):
    assert mod(tmp_path, "attention", state2()) == {"text": "", "tooltip": "", "class": "hidden"}


def test_attention_module_groups_by_kind_and_escalates(tmp_path):
    items = [APPROVAL, dict(APPROVAL, id="ap_2"), CI]
    out = mod(tmp_path, "attention", state2(**{"class": "warn"}, attention={"count": 3, "items": items}))
    assert out["text"] == "⚠ 2 approvals · 1 CI red" and out["class"] == "warn"
    assert "CI red: lhassa  [GitHub Actions]" in out["tooltip"]
    out = mod(tmp_path, "attention", state2(**{"class": "alert"}, attention={"count": 3, "items": items}))
    assert out["class"] == "alert"


def test_system_module(tmp_path):
    out = mod(tmp_path, "system", state2())
    assert out["text"] == "balanced · 62° · 12% · 80%" and out["class"] == "ok"
    hot = state2()
    hot["system"].update(temp_level="alert", charging=True)
    out = mod(tmp_path, "system", hot)
    assert out["class"] == "alert" and "⚡80%" in out["text"]


def test_modules_are_hidden_without_a_fresh_v2_file(tmp_path):
    for name in ("agents", "attention", "system"):
        assert mod(tmp_path, name, state2(written_at=NOW - 3600))["class"] == "hidden"
        assert mod(tmp_path, name, state())["class"] == "hidden"  # a v1 file has no sections
    assert mod(tmp_path / "empty", "agents")["class"] == "hidden"


def test_a_v1_file_still_renders_the_old_widget(tmp_path):
    out = render(tmp_path, state(approvals=2))
    assert out["text"].endswith("$0.42 · 2") and out["class"] == "warn"


def test_garbage_v2_sections_degrade(tmp_path):
    payload = state2(agents="x", attention=[], system=None, today=5, lhassa=[])
    out = render(tmp_path, payload)
    assert out["class"] == "ok" and out["text"].startswith(wb.WHEEL)  # no usable v2 section: the v1 look
    payload = state2(attention="x", system=None, today=5, lhassa=[])
    assert render(tmp_path, payload)["text"] == wb.WHEEL
    for name in ("agents", "attention", "system"):
        mod(tmp_path, name, payload)  # must not raise


# ── scroll: cycling the counter mode ─────────────────────────────────────────

class Rec:
    """A recorder standing in for subprocess.run."""

    def __init__(self, stdout="", returncode=0):
        self.calls, self.inputs = [], []
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        self.inputs.append(kw.get("input"))
        return self

    @property
    def commands(self):
        return [" ".join(c) for c in self.calls]


def test_scroll_cycles_modes_and_signals_only_the_icon(tmp_path):
    path = str(tmp_path / "waybar.json")
    run = Rec()
    seen = [wb.scroll(path, "up", {}, run) for _ in range(5)]
    assert seen == ["agents", "temp", "spend", "attention", "agents"]
    assert set(run.commands) == {"pkill -RTMIN+14 waybar"}
    assert wb.scroll(path, "down", {}, run) == "attention"
    assert wb.scroll(path, "down", {}, run) == "spend"


def test_scroll_mode_file_is_private_and_atomic(tmp_path):
    import os
    import stat

    path = str(tmp_path / "run" / "waybar.json")
    wb.scroll(path, "up", {}, Rec())
    mode_file = tmp_path / "run" / "waybar-mode"
    assert mode_file.read_text().strip() == "agents"
    assert stat.S_IMODE(mode_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(mode_file.parent.stat().st_mode) == 0o700
    assert sorted(os.listdir(mode_file.parent)) == ["waybar-mode"]  # no temp left


def test_scroll_subcommand_end_to_end(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "LHASSA_WAYBAR_STATE_FILE": str(tmp_path / "w.json")}
    done = subprocess.run([sys.executable, str(SCRIPT), "scroll", "up"], env=env, capture_output=True, text=True, timeout=10)
    assert done.returncode == 0 and (tmp_path / "waybar-mode").read_text().strip() == "agents"
    bad = subprocess.run([sys.executable, str(SCRIPT), "scroll", "sideways"], env=env, capture_output=True, text=True, timeout=10)
    assert bad.returncode == 2


def test_subprocess_modules_print_json(tmp_path):
    path = tmp_path / "w.json"
    path.write_text(json.dumps(state2(written_at=time.time())))
    env = {"PATH": "/usr/bin:/bin", "LHASSA_WAYBAR_STATE_FILE": str(path), "LHASSA_WAYBAR_LANG": "en"}
    for name in ("agents", "attention", "system"):
        done = subprocess.run([sys.executable, str(SCRIPT), name], env=env, capture_output=True, text=True, timeout=10)
        assert done.returncode == 0 and set(json.loads(done.stdout)) == {"text", "tooltip", "class"}


# ── open: v2 items pick the section ──────────────────────────────────────────

def test_open_picks_fleet_for_a_failed_service_but_approvals_first(tmp_path):
    svc = {"kind": "service", "label": "lhassa-monitor", "source": "systemd"}
    assert _section(tmp_path, state2(attention={"count": 1, "items": [svc]})).endswith("#fleet")
    assert _section(tmp_path, state2(attention={"count": 2, "items": [svc, APPROVAL]})).endswith("#approvals")


# ── the right-click menu (wofi and Lhassa simulated) ─────────────────────────

class Wofi:
    """Scripted answers for wofi: the n-th prompt gets the n-th reply."""

    def __init__(self, *replies):
        self.replies, self.asked = list(replies), []

    def __call__(self, prompt, options):
        self.asked.append((prompt, list(options)))
        reply = self.replies.pop(0) if self.replies else None
        return reply(options) if callable(reply) else reply


def menu_env(tmp_path, payload):
    path = tmp_path / "waybar.json"
    path.write_text(json.dumps(payload))
    return str(path), {"LHASSA_WAYBAR_LANG": "en", "LHASSA_BIN": "/fake/lhassa"}


def with_tools(monkeypatch):
    monkeypatch.setattr(wb, "which_or", lambda name, *rest: "/fake/" + name)


def live_payload(**over):
    over.setdefault("attention", {"count": 1, "items": [APPROVAL]})
    return state2(written_at=time.time(), **over)


def test_wofi_is_driven_with_dmenu_and_only_known_choices_count(monkeypatch):
    with_tools(monkeypatch)
    run = Rec(stdout="b\n")
    assert wb.wofi_ask("P", ["a", "b"], run) == "b"
    assert "--dmenu" in run.calls[0] and run.inputs[0] == "a\nb"
    run.stdout = "hacked\n"
    assert wb.wofi_ask("P", ["a", "b"], run) is None
    run.stdout, run.returncode = "a\n", 1  # escape pressed
    assert wb.wofi_ask("P", ["a", "b"], run) is None


def test_wofi_missing_gives_none(monkeypatch):
    monkeypatch.setattr(wb, "which_or", lambda *a: None)
    assert wb.wofi_ask("P", ["a"], Rec()) is None


def test_menu_lists_approve_deny_only_when_something_pends(tmp_path):
    path, env = menu_env(tmp_path, live_payload())
    ask = Wofi(None)
    wb.menu(path, env, ask=ask, run=Rec())
    options = ask.asked[0][1]
    assert any("Approve" in o for o in options) and any("Deny" in o for o in options)
    assert any("not available yet" in o for o in options)
    path, env = menu_env(tmp_path, state2(written_at=time.time()))
    ask = Wofi(None)
    wb.menu(path, env, ask=ask, run=Rec())
    assert not any("Approve" in o for o in ask.asked[0][1])


def test_menu_summary_notifies_with_numbers_and_names(tmp_path, monkeypatch):
    with_tools(monkeypatch)
    path, env = menu_env(tmp_path, live_payload())
    run = Rec()
    assert wb.menu(path, env, ask=Wofi(lambda o: o[0]), run=run) == "summary"
    assert run.calls[0][0] == "/fake/dunstify"
    body = run.calls[0][-1]
    assert "120 events" in body and "git_push" in body


def test_menu_approve_confirms_then_calls_the_cli(tmp_path, monkeypatch):
    with_tools(monkeypatch)
    path, env = menu_env(tmp_path, live_payload())
    run = Rec()
    ask = Wofi(lambda o: o[1], lambda o: o[0], lambda o: o[0])
    assert wb.menu(path, env, ask=ask, run=run) == "approve"
    assert ["/fake/lhassa", "approve", "ap_123"] in run.calls
    assert "Approve git_push [ap_123]?" == ask.asked[2][0]
    assert "pkill -RTMIN+15 waybar" in run.commands and "pkill -RTMIN+14 waybar" in run.commands


def test_menu_deny_and_cancel(tmp_path, monkeypatch):
    with_tools(monkeypatch)
    path, env = menu_env(tmp_path, live_payload())
    run = Rec()
    assert wb.menu(path, env, ask=Wofi(lambda o: o[2], lambda o: o[0], lambda o: o[0]), run=run) == "deny"
    assert ["/fake/lhassa", "deny", "ap_123"] in run.calls
    run = Rec()  # Cancel at the confirmation: nothing runs
    assert wb.menu(path, env, ask=Wofi(lambda o: o[1], lambda o: o[0], lambda o: o[1]), run=run) is None
    assert not any(c[:2] == ["/fake/lhassa", "approve"] for c in run.calls)


def test_menu_never_passes_a_hostile_id_to_the_cli(tmp_path, monkeypatch):
    with_tools(monkeypatch)
    bad = dict(APPROVAL, id="x; rm -rf ~")
    path, env = menu_env(tmp_path, live_payload(attention={"count": 1, "items": [bad]}))
    ask = Wofi(None)
    wb.menu(path, env, ask=ask, run=Rec())
    assert not any("Approve" in o for o in ask.asked[0][1])  # an id with shell characters is not offered


def test_menu_opens_a_dashboard_section(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(wb, "open_url", lambda url: opened.append(url) or True)
    path, env = menu_env(tmp_path, live_payload())
    ask = Wofi(lambda o: next(x for x in o if x.startswith("Open")), lambda o: "integrity")
    assert wb.menu(path, env, ask=ask, run=Rec()) == "open:integrity"
    assert opened == ["http://127.0.0.1:7766/#integrity"]
    assert ask.asked[1][1] == list(wb.OPEN_SECTIONS)


def test_menu_pause_is_a_visible_placeholder_that_does_nothing(tmp_path, monkeypatch):
    with_tools(monkeypatch)
    path, env = menu_env(tmp_path, live_payload())
    run = Rec()
    ask = Wofi(lambda o: next(x for x in o if x.startswith("Pause")))
    assert wb.menu(path, env, ask=ask, run=run) == "pause-unavailable"
    assert all(c[0] == "/fake/dunstify" for c in run.calls)  # only a notice, no lhassa call
    assert "Not available yet" in run.calls[0][-1]


def test_menu_power_mode_goes_through_the_governed_action(tmp_path, monkeypatch):
    with_tools(monkeypatch)
    path, env = menu_env(tmp_path, live_payload())
    run = Rec()
    ask = Wofi(lambda o: next(x for x in o if x.startswith("Power")), lambda o: "saver", lambda o: o[0])
    assert wb.menu(path, env, ask=ask, run=run) == "power:saver"
    assert ["/fake/lhassa", "action", "run", "power_mode", "--param", "mode=saver"] in run.calls
    assert not any("power-mode" in c[0] or c[0].endswith("sudo") for c in run.calls)
    assert "pkill -RTMIN+16 waybar" in run.commands


def test_menu_power_mode_rejects_unknown_profiles(tmp_path, monkeypatch):
    with_tools(monkeypatch)
    path, env = menu_env(tmp_path, live_payload())
    run = Rec()
    ask = Wofi(lambda o: next(x for x in o if x.startswith("Power")), lambda o: "turbo")
    assert wb.menu(path, env, ask=ask, run=run) is None
    assert not any("action" in c for c in run.calls)


def test_menu_escape_does_nothing(tmp_path):
    path, env = menu_env(tmp_path, live_payload())
    run = Rec()
    assert wb.menu(path, env, ask=Wofi(None), run=run) is None
    assert run.calls == []


def test_menu_without_a_brain_still_opens_and_says_so(tmp_path, monkeypatch):
    with_tools(monkeypatch)
    run = Rec()
    env = {"LHASSA_WAYBAR_LANG": "en"}
    assert wb.menu(str(tmp_path / "none.json"), env, ask=Wofi(lambda o: o[0]), run=run) == "summary"
    assert "not running" in run.calls[0][-1]
