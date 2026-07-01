"""CV file-I/O seam + engine export — offline, isolated under a temp LHASSA_CV_DIR."""

import pytest

from lhassa import cv
from lhassa.cv import paths, store
from lhassa.flows.engine import resolve_company, run_flow
from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind


@pytest.fixture
def cv_dir(tmp_path, monkeypatch):
    """Point the CV seam at a temp dir so tests never touch the owner's ~/.lhassa/cv."""
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    return tmp_path


# ── slug rules ──────────────────────────────────────────────────────────────────
def test_slug_company_rules(cv_dir):
    assert store.slug_company("Acme Backend") == "acme-backend"
    assert store.slug_company("  Açme  Corp!!  ") == "acme-corp"
    assert store.slug_company("Foo / Bar & Baz") == "foo-bar-baz"
    # "Nº" (numero sign) NFKD-decomposes to "No"; symbols/spaces collapse to hyphens.
    assert store.slug_company("Convocatoria Nº 42 (2026)") == "convocatoria-no-42-2026"


def test_slug_company_empty_is_untitled(cv_dir):
    assert store.slug_company("") == "untitled"
    assert store.slug_company(None) == "untitled"
    assert store.slug_company("   ") == "untitled"
    assert store.slug_company("!!!") == "untitled"  # no alphanumerics


# ── load_base ───────────────────────────────────────────────────────────────────
def test_load_base_absent_is_none(cv_dir):
    assert cv.load_base() is None


def test_load_base_reads_the_file(cv_dir):
    paths.base_path().write_text("\\documentclass{article}\n", encoding="utf-8")
    assert cv.load_base() == "\\documentclass{article}\n"


# ── export ──────────────────────────────────────────────────────────────────────
def test_export_writes_to_out_with_id_company_name(cv_dir):
    path = cv.export("hello tex", company="Acme Backend", run_id="run-123")
    assert path.parent == paths.out_dir()
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "hello tex"
    # {id}_{company}.tex — a short id, an underscore, the company slug, .tex
    assert path.name.endswith("_acme-backend.tex")
    ident = path.name.split("_", 1)[0]
    assert 3 <= len(ident) <= 6 and ident.isalnum()


def test_export_run_id_makes_the_name_stable(cv_dir):
    a = cv.export("x", company="Acme", run_id="same")
    b = cv.export("y", company="Acme", run_id="same")
    assert a.name == b.name  # same run id → same id slug (overwritten atomically)


def test_export_untitled_when_no_company(cv_dir):
    path = cv.export("x", company=None)
    assert path.name.endswith("_untitled.tex")


def test_short_id_is_deterministic_per_seed(cv_dir):
    assert store.short_id("run-1") == store.short_id("run-1")
    assert store.short_id("run-1") != store.short_id("run-2")
    fresh = store.short_id()
    assert 3 <= len(fresh) <= 6 and fresh.isalnum()


# ── company resolution precedence ────────────────────────────────────────────────
def test_resolve_company_precedence():
    # override wins
    assert resolve_company("Override Co", {"company": "Context Co"}) == "Override Co"
    # else context
    assert resolve_company(None, {"company": "Context Co"}) == "Context Co"
    assert resolve_company("   ", {"company": "Context Co"}) == "Context Co"
    # else untitled
    assert resolve_company(None, None) == "untitled"
    assert resolve_company(None, {}) == "untitled"
    assert resolve_company("  ", {"company": "   "}) == "untitled"


# ── engine: an offline run writes a .tex with a company override ──────────────────
def _tex_flow():
    return Flow(
        id="cvf",
        name="cv export",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "documentation", "prompt": "draft for {company}"}),
            FlowNode("o", NodeKind.OUTPUT, {"type": "tex", "target": "cv"}),
        ],
        edges=[FlowEdge("t", "w"), FlowEdge("w", "o")],
    )


@pytest.mark.asyncio
async def test_engine_offline_run_exports_tex_with_company_override(cv_dir):
    async def _exec(goal, *, department, config, **kw):
        return {"ok": True, "summary": "% tailored tex\n" + goal, "department": department}

    rec = await run_flow(
        _tex_flow(), inputs={"summary": "job posting"},
        company="Acme Backend", executor=_exec,
    )
    assert rec["status"] == "completed"
    out_step = next(s for s in rec["steps"] if s["kind"] == "output")
    assert out_step["output"] == "tex"
    written = out_step["path"]
    assert written.endswith("_acme-backend.tex")
    from pathlib import Path

    p = Path(written)
    assert p.exists() and p.parent == paths.out_dir()
    assert "tailored tex" in p.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_engine_injects_base_cv_and_company_into_prompt(cv_dir):
    paths.base_path().write_text("BASE-CV-BODY", encoding="utf-8")
    seen = {}

    async def _exec(goal, *, department, config, **kw):
        seen["goal"] = goal
        return {"ok": True, "summary": goal, "department": department}

    flow = Flow(
        id="cvp", name="cv prompt",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "documentation",
                                          "prompt": "build on {base_cv} for {company}"}),
        ],
        edges=[FlowEdge("t", "w")],
    )
    await run_flow(flow, company="Acme Backend", executor=_exec)
    assert "BASE-CV-BODY" in seen["goal"]
    assert "Acme Backend" in seen["goal"]
