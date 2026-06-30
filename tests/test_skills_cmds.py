"""Tests for the ``lhassa skills`` sub-app (slice M).

Offline: the skill store + org topology are redirected to a tmp dir by patching
the LIVE ``lhassa.config.Config`` class attrs (resolved through the module at call
time, so the override survives ``test_config.py``'s ``importlib.reload``). The AI
commands are tested only on their OFFLINE/degrade paths — no real model is called.
The sub-app is invoked directly (the integrator wires it onto the main app).
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from lhassa.cli.skills_cmds import skills_app

runner = CliRunner()


def _use_tmp(tmp_path, monkeypatch):
    import lhassa.config as config_mod

    monkeypatch.setattr(config_mod.Config, "skills_dir", str(tmp_path / "skills"))
    monkeypatch.setattr(
        config_mod.Config, "org_topology_file", str(tmp_path / "org.json")
    )


def _seed_topology(tmp_path):
    (tmp_path / "org.json").write_text(
        json.dumps(
            [
                {"id": "general", "role": "general", "parent": None},
                {
                    "id": "dept:opt",
                    "role": "department",
                    "department": "optimizacion",
                    "parent": "general",
                    "skills": ["optimizacion"],
                },
            ]
        )
    )


# ── create / list / show ──────────────────────────────────────────────────────


def test_create_template_and_list(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(
        skills_app, ["create", "my-skill", "--from-brief", "do a thing", "--json"]
    )
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["created"] == "my-skill"

    res = runner.invoke(skills_app, ["list", "--json"])
    assert res.exit_code == 0
    names = {s["name"] for s in json.loads(res.output)}
    assert "my-skill" in names


def test_create_duplicate_fails(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    runner.invoke(skills_app, ["create", "dup"])
    res = runner.invoke(skills_app, ["create", "dup"])
    assert res.exit_code == 1


def test_show(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    runner.invoke(skills_app, ["create", "shown", "--from-brief", "describe me"])
    res = runner.invoke(skills_app, ["show", "shown", "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["name"] == "shown"
    assert payload["description"] == "describe me"


def test_show_missing_fails(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(skills_app, ["show", "ghost"])
    assert res.exit_code == 1


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    runner.invoke(skills_app, ["create", "gone"])
    res = runner.invoke(skills_app, ["delete", "gone", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["deleted"] == "gone"
    assert runner.invoke(skills_app, ["delete", "gone"]).exit_code == 1


# ── seed / recommend ──────────────────────────────────────────────────────────


def test_seed_and_recommend(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(skills_app, ["seed", "--json"])
    assert res.exit_code == 0
    assert set(json.loads(res.output)["seeded"]) == {
        "optimizacion",
        "documentation",
        "code-review",
    }

    res = runner.invoke(skills_app, ["recommend", "--json"])
    assert res.exit_code == 0
    names = {r["name"] for r in json.loads(res.output)}
    # seeded ones are excluded; uninstalled catalog entries remain
    assert "optimizacion" not in names
    assert "research" in names


def test_recommend_with_context(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(
        skills_app, ["recommend", "audit my secrets for security", "--json"]
    )
    assert res.exit_code == 0
    recs = json.loads(res.output)
    assert recs[0]["name"] == "security-audit"


# ── assign / who-has ──────────────────────────────────────────────────────────


def test_assign_and_who_has(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    _seed_topology(tmp_path)
    res = runner.invoke(skills_app, ["assign", "research", "dept:opt", "--json"])
    assert res.exit_code == 0
    node = json.loads(res.output)
    assert set(node["skills"]) == {"optimizacion", "research"}

    res = runner.invoke(skills_app, ["who-has", "research", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["brains"] == ["dept:opt"]


def test_assign_unknown_brain_fails(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    _seed_topology(tmp_path)
    res = runner.invoke(skills_app, ["assign", "research", "ghost"])
    assert res.exit_code == 1


# ── import / export ───────────────────────────────────────────────────────────


def test_import_and_export(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    ext = tmp_path / "ext" / "ported"
    ext.mkdir(parents=True)
    (ext / "SKILL.md").write_text(
        "---\nname: ported\ndescription: external\n---\n\nBody."
    )
    res = runner.invoke(skills_app, ["import", str(ext), "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["name"] == "ported"

    out_dir = tmp_path / "exported"
    res = runner.invoke(skills_app, ["export", "ported", "--to", str(out_dir), "--json"])
    assert res.exit_code == 0
    path = json.loads(res.output)["path"]
    assert (tmp_path / "exported" / "ported" / "SKILL.md").exists()
    assert "ported" in path


def test_import_missing_fails(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    res = runner.invoke(skills_app, ["import", str(tmp_path / "nope")])
    assert res.exit_code == 1


# ── create --ai degrades cleanly with no provider/creds ──────────────────────


def test_create_ai_degrades(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)

    # Force the AI path to have no usable provider by making the provider raise.
    from lhassa.llm.provider import ProviderNotConfigured

    class _Broken:
        name = "broken"
        zero_retention = False
        is_local = False

        async def complete(self, *a, **k):
            raise ProviderNotConfigured("no creds")

    monkeypatch.setattr(
        "lhassa.skills.ai.get_model_provider", lambda *a, **k: _Broken(), raising=False
    )

    res = runner.invoke(
        skills_app, ["create", "ai-skill", "--from-brief", "help me", "--ai", "--json"]
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["created"] == "ai-skill"
    # the skill is still created (a template), with a clear AI-unavailable note
    assert payload["error"] and "AI unavailable" in payload["error"]


def test_help_lists_commands():
    res = runner.invoke(skills_app, ["--help"])
    assert res.exit_code == 0
    for cmd in ("create", "improve", "assign", "who-has", "recommend", "import", "export"):
        assert cmd in res.output
