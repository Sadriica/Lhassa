"""CV file-I/O seam + engine export; offline, isolated under a temp LHASSA_CV_DIR."""

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
def test_export_writes_to_a_per_run_folder(cv_dir):
    path = cv.export("hello tex", company="Acme Backend", run_id="run-123")
    # out/{id}_{company}/{company}.tex; every run's artifacts live together
    assert path.parent.parent == paths.out_dir()
    assert path.parent.name.endswith("_acme-backend")
    assert path.name == "acme-backend.tex"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "hello tex"
    ident = path.parent.name.split("_", 1)[0]
    assert 3 <= len(ident) <= 6 and ident.isalnum()


def test_export_run_id_makes_the_name_stable(cv_dir):
    a = cv.export("x", company="Acme", run_id="same")
    b = cv.export("y", company="Acme", run_id="same")
    assert a == b  # same run id → same folder + name (overwritten atomically)


def test_export_untitled_when_no_company(cv_dir):
    path = cv.export("x", company=None)
    assert path.parent.name.endswith("_untitled") and path.name == "untitled.tex"


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
    assert written.endswith("acme-backend.tex")
    from pathlib import Path

    p = Path(written)
    assert p.exists() and p.parent.parent == paths.out_dir()
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


# ── clean_latex: strip prose/fences down to a compile-ready document ──────────────
def test_clean_latex_unwraps_a_markdown_fence(cv_dir):
    wrapped = ("Here is the final CV:\n```latex\n"
               "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n```\nHope it helps!")
    out = cv.clean_latex(wrapped)
    assert out.startswith("\\documentclass")
    assert out.rstrip().endswith("\\end{document}")
    assert "Here is" not in out and "```" not in out and "Hope it helps" not in out


def test_clean_latex_strips_leading_and_trailing_prose(cv_dir):
    prose = ("Sure! Below is the optimized CV.\n\n"
             "\\documentclass{article}\n\\begin{document}\nBody\n\\end{document}\n\nLet me know.")
    out = cv.clean_latex(prose)
    assert out.startswith("\\documentclass") and out.rstrip().endswith("\\end{document}")
    assert "Sure!" not in out and "Let me know" not in out


def test_clean_latex_passes_plain_latex_through(cv_dir):
    plain = "\\documentclass{article}\n\\begin{document}\nClean\n\\end{document}"
    assert cv.clean_latex(plain).strip() == plain.strip()


def test_clean_latex_empty_is_empty(cv_dir):
    assert cv.clean_latex("") == ""


# ── engine threads a task node's max_tokens down to the executor ──────────────────
@pytest.mark.asyncio
async def test_task_max_tokens_threaded_to_executor(cv_dir):
    seen = {}

    async def _exec(goal, *, department, config, **kw):
        seen["max_tokens"] = kw.get("max_tokens")
        return {"ok": True, "summary": "x", "department": department}

    flow = Flow(
        id="mtf", name="max tokens",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"brain": "cv-writer", "prompt": "go", "max_tokens": 1234}),
            FlowNode("o", NodeKind.OUTPUT, {"type": "log"}),
        ],
        edges=[FlowEdge("t", "w"), FlowEdge("w", "o")],
    )
    await run_flow(flow, executor=_exec)
    assert seen["max_tokens"] == 1234


# ── the contact-preservation gate (the CV árbitro: never invent contact details) ──
def test_contact_gate_passes_when_details_preserved(cv_dir):
    from lhassa.agents.verify import verify

    base = r"\href{mailto:owner@example.edu}{owner@example.edu} +57 300 111 2222"
    out = "Email owner@example.edu, phone +57 300 111 2222; tailored CV body."
    v = verify(out, spec={"type": "contact_preserved"}, context={"base_cv": base})
    assert v.passed


def test_contact_gate_fails_when_email_or_phone_invented(cv_dir):
    from lhassa.agents.verify import verify

    base = "owner@example.edu +57 300 111 2222"
    altered = "impostor@example.edu +57 316 000 0000"  # both invented
    v = verify(altered, spec={"type": "contact_preserved"}, context={"base_cv": base})
    assert not v.passed
    assert "owner@example.edu" in v.detail and "573001112222" in v.detail


def test_contact_gate_passes_with_no_base(cv_dir):
    from lhassa.agents.verify import verify

    v = verify("anything", spec={"type": "contact_preserved"}, context={})
    assert v.passed  # nothing to enforce


# ── restore_contact; deterministic contact preservation ──────────────────────────


def test_restore_contact_swaps_all_altered_occurrences(cv_dir):
    import re as _re

    base = r"\href{mailto:owner@real.com}{owner@real.com} \\ +57 300 111 2222"
    tailored = (
        r"\documentclass{article}\begin{document}"
        r"\href{mailto:wrong@fake.com}{wrong@fake.com} \\ +57 316 999 8888"
        r"\end{document}"
    )
    out = cv.restore_contact(tailored, base)
    assert "owner@real.com" in out
    assert "wrong@fake.com" not in out  # BOTH occurrences (mailto + display) swapped
    assert "3001112222" in _re.sub(r"\D", "", out)
    assert "3169998888" not in _re.sub(r"\D", "", out)


def test_restore_contact_leaves_a_faithful_output_untouched(cv_dir):
    base = r"owner@real.com +57 300 111 2222"
    good = r"\documentclass{article}\begin{document}owner@real.com +57 300 111 2222\end{document}"
    assert cv.restore_contact(good, base) == good


def test_restore_contact_appends_when_contact_dropped(cv_dir):
    base = r"owner@real.com"
    dropped = r"\documentclass{article}\begin{document}no contact here\end{document}"
    out = cv.restore_contact(dropped, base)
    assert "owner@real.com" in out  # presence guaranteed via the comment marker
    assert out.rstrip().endswith(r"\end{document}")


def test_restore_contact_degrades_without_base(cv_dir):
    tailored = r"\begin{document}anything@else.com\end{document}"
    assert cv.restore_contact(tailored, "") == tailored
    assert cv.restore_contact(tailored, None) == tailored


def test_restored_output_passes_the_contact_gate(cv_dir):
    from lhassa.agents.verify import verify

    base = r"owner@real.com +57 300 111 2222"
    tailored = r"\begin{document}impostor@x.com +57 316 000 0000\end{document}"
    out = cv.restore_contact(tailored, base)
    v = verify(out, spec={"type": "contact_preserved"}, context={"base_cv": base})
    assert v.passed
