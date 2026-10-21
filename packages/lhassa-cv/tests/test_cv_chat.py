"""Tests for the chat → CV work-flow bridge (the cv pack's inbound hook).

Transport-free: every turn is played through the chat engine with
``tests.chat_fakes.FakeTransport`` (an upload's ``fetch()`` is a fake), and the
tailored file reaches a fake file-capable channel through ``org.deliver``; no
Telegram, no network, no real model. The CV work-flow is either faked (routing
tests) or run fully OFFLINE ($0, the injected base-CV override test). Covers:

  * an upload → extract → ``run_flow(base_cv=<extracted>)`` → the file is delivered;
  * a no-caption document → asks for the target role (no flow, no download);
  * a gate failure → a short text reply, and NO file sent;
  * the ``run_flow`` ``base_cv`` override is honoured (a fake executor sees the base).
"""

import asyncio
from types import SimpleNamespace

from lhassa import cv
from lhassa.cv.surfaces import chat as cvtg
from tests.chat_fakes import FakeTransport, FileChannel, install_channel


# ── company parsing ─────────────────────────────────────────────────────────────
def test_parse_company_forms():
    assert cvtg._parse_company("Backend role @ Acme Corp") == "Acme Corp"
    assert cvtg._parse_company("company: Globex") == "Globex"
    assert cvtg._parse_company("company=Initech.") == "Initech"
    assert cvtg._parse_company("just a plain job posting") is None


# ── config / helpers ────────────────────────────────────────────────────────────
def _cfg(**over):
    base = dict(
        telegram_bot_token="TOKEN",
        telegram_chat_id="123",
        telegram_poll_enabled=True,
        cv_flow_id="flow-zvzpry",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _files(monkeypatch, **kw):
    """A fake file-capable owner channel: ``org.deliver`` sends the CV through it."""
    return install_channel(monkeypatch, FileChannel(**kw))


async def _boom_send_doc(path, *, caption="", config=None):
    raise AssertionError("must not send a file here")


def _upload(transport, content, *, caption=None, file_name="cv.tex"):
    asyncio.run(
        transport.upload(
            config=_cfg(), content=content, caption=caption, file_name=file_name
        )
    )


# ── document update → extract → run_flow(base_cv=...) → sendDocument ─────────────
def test_document_update_runs_flow_and_sends_tex(monkeypatch, tmp_path):
    tex_body = (
        "\\documentclass{article}\\begin{document}"
        "owner@example.edu +57 300 111 2222\\end{document}"
    )
    files = _files(monkeypatch)
    transport = FakeTransport()
    # No real LaTeX engine in the suite: compile fails → fall back to the .tex.
    monkeypatch.setattr(cv, "compile_pdf", lambda p: None)

    # The configured flow exists (any object; run_flow is faked below).
    import lhassa.flows.store as store_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: object())

    # Fake the DELEGATION seam: record what it was called with, return a tex record.
    # (The bridge now runs through org.delegate.delegate_flow, not run_flow directly.)
    out_file = tmp_path / "ab12cd_acme.tex"
    out_file.write_text("% tailored", encoding="utf-8")
    seen = {}

    async def _fake_delegate_flow(flow, *, inputs, config, company, base_cv):
        seen.update(inputs=inputs, company=company, base_cv=base_cv)
        return {
            "status": "completed",
            "steps": [
                {"kind": "task", "ok": True},
                {"kind": "gate", "passed": True},
                {"kind": "output", "output": "tex", "path": str(out_file)},
            ],
        }

    from lhassa.org import delegate as delegate_mod

    monkeypatch.setattr(delegate_mod, "delegate_flow", _fake_delegate_flow)

    _upload(transport, tex_body.encode(), caption="Senior Backend Engineer @ Acme")

    assert transport.fetched == ["FID"]
    # delegate_flow got the EXTRACTED text as base_cv, caption as summary, parsed company.
    assert seen["base_cv"] == tex_body
    assert seen["inputs"] == {"summary": "Senior Backend Engineer @ Acme"}
    assert seen["company"] == "Acme"
    # The exported .tex was sent as a document (not a plain text reply).
    assert files.documents and files.documents[0]["path"] == str(out_file)
    assert "Acme" in files.documents[0]["caption"]
    assert transport.sent == []


def test_no_caption_document_holds_cv_and_asks_for_role(monkeypatch):
    cvtg._PENDING_CV.clear()
    cvtg._AWAIT_BASE.clear()
    tex_body = "\\documentclass{article}\\begin{document}Body\\end{document}"
    _files(monkeypatch, on_document=_boom_send_doc)  # no file without a target role
    transport = FakeTransport()
    sent = transport.sent

    _upload(transport, tex_body.encode())

    # Not armed → the CV is HELD and we ask (plain text) for the posting; no file/flow.
    assert cvtg._PENDING_CV["123"]["base_cv"] == tex_body
    assert len(sent) == 1 and sent[0].startswith("Got your CV")
    cvtg._PENDING_CV.clear()


def test_held_cv_then_followup_text_runs_flow(monkeypatch, tmp_path):
    cvtg._PENDING_CV.clear()
    cvtg._AWAIT_BASE.clear()
    tex_body = (
        "\\documentclass{article}\\begin{document}"
        "owner@example.edu +57 300 111 2222\\end{document}"
    )
    files = _files(monkeypatch)
    transport = FakeTransport()
    monkeypatch.setattr(cv, "compile_pdf", lambda p: None)  # no engine → send the .tex

    import lhassa.flows.store as store_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: object())

    out_file = tmp_path / "zz99_sezzle.tex"
    out_file.write_text("% tailored", encoding="utf-8")
    seen = {}

    async def _fake_delegate_flow(flow, *, inputs, config, company, base_cv):
        seen.update(inputs=inputs, company=company, base_cv=base_cv)
        return {
            "status": "completed",
            "steps": [{"kind": "output", "output": "tex", "path": str(out_file)}],
        }

    from lhassa.org import delegate as delegate_mod

    monkeypatch.setattr(delegate_mod, "delegate_flow", _fake_delegate_flow)

    # Two turns in order: the caption-less document, then the follow-up job posting.
    _upload(transport, tex_body.encode())
    asyncio.run(transport.say("AI Engineer @ Sezzle", config=_cfg()))

    # The follow-up text ran the flow with the HELD CV as base + the text as the posting.
    assert seen["base_cv"] == tex_body
    assert seen["inputs"] == {"summary": "AI Engineer @ Sezzle"}
    assert seen["company"] == "Sezzle"
    assert [d["path"] for d in files.documents] == [str(out_file)]
    assert cvtg._PENDING_CV == {}  # consumed
    cvtg._PENDING_CV.clear()


def test_gate_failure_replies_and_sends_no_file(monkeypatch):
    _files(monkeypatch, on_document=_boom_send_doc)  # no file when a gate failed
    transport = FakeTransport()
    sent = transport.sent

    import lhassa.flows.store as store_mod
    from lhassa.org import delegate as delegate_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: object())

    async def _fake_delegate_flow(flow, *, inputs, config, company, base_cv):
        # The contact gate failed → the path never reached the tex output.
        return {
            "status": "completed",
            "steps": [
                {"kind": "task", "ok": True},
                {
                    "kind": "gate",
                    "passed": False,
                    "detail": "contact altered/dropped: email:owner@example.edu",
                },
            ],
        }

    monkeypatch.setattr(delegate_mod, "delegate_flow", _fake_delegate_flow)

    _upload(transport, b"owner@example.edu +57 300 111 2222 base cv", caption="Backend @ Acme")

    assert len(sent) == 1
    assert "contact details" in sent[0].lower()


def test_missing_flow_replies_not_set_up(monkeypatch):
    transport = FakeTransport()
    sent = transport.sent

    import lhassa.flows.store as store_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: None)

    _upload(transport, b"a base cv body", caption="Backend role")
    assert len(sent) == 1 and "flow-zvzpry" in sent[0]


# ── run_flow base_cv override is honoured (OFFLINE, $0) ───────────────────────
def test_run_flow_base_cv_override_reaches_the_executor(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    from lhassa.cv import paths
    from lhassa.flows.engine import run_flow
    from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind

    # An on-disk base CV that must be IGNORED when an override is supplied.
    paths.base_path().write_text("ON-DISK-BASE", encoding="utf-8")

    seen = {}

    async def _exec(goal, *, department, config, **kw):
        seen["goal"] = goal
        return {"ok": True, "summary": goal, "department": department}

    flow = Flow(
        id="cvo",
        name="cv override",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "documentation", "prompt": "use {base_cv}"}),
        ],
        edges=[FlowEdge("t", "w")],
    )

    rec = asyncio.run(
        run_flow(flow, executor=_exec, base_cv="INJECTED-UPLOADED-CV")
    )
    assert rec["status"] == "completed"
    assert "INJECTED-UPLOADED-CV" in seen["goal"]
    assert "ON-DISK-BASE" not in seen["goal"]


def test_run_flow_base_cv_none_still_loads_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    from lhassa.cv import paths
    from lhassa.flows.engine import run_flow
    from lhassa.flows.model import Flow, FlowEdge, FlowNode, NodeKind

    paths.base_path().write_text("ON-DISK-BASE", encoding="utf-8")
    seen = {}

    async def _exec(goal, *, department, config, **kw):
        seen["goal"] = goal
        return {"ok": True, "summary": goal, "department": department}

    flow = Flow(
        id="cvd",
        name="cv disk",
        nodes=[
            FlowNode("t", NodeKind.TRIGGER, {"type": "manual"}),
            FlowNode("w", NodeKind.TASK, {"skill": "documentation", "prompt": "use {base_cv}"}),
        ],
        edges=[FlowEdge("t", "w")],
    )

    # base_cv omitted (None) → the historical load-from-disk behaviour is unchanged.
    asyncio.run(run_flow(flow, executor=_exec))
    assert "ON-DISK-BASE" in seen["goal"]


# ── /cv text command runs against the on-disk base CV ─────────────────────────
def test_cv_command_runs_against_disk_base(monkeypatch, tmp_path):
    out_file = tmp_path / "zz99_untitled.tex"
    out_file.write_text("% tailored", encoding="utf-8")

    import lhassa.flows.store as store_mod
    from lhassa.org import delegate as delegate_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: object())
    seen = {}

    async def _fake_delegate_flow(flow, *, inputs, config, company, base_cv):
        seen.update(inputs=inputs, company=company, base_cv=base_cv)
        return {
            "status": "completed",
            "steps": [{"kind": "output", "output": "tex", "path": str(out_file)}],
        }

    monkeypatch.setattr(delegate_mod, "delegate_flow", _fake_delegate_flow)

    files = _files(monkeypatch)
    monkeypatch.setattr(cv, "compile_pdf", lambda p: None)  # no engine → send the .tex

    posting = 'Backend role @ Globex. Requirements: 5+ years Python, FastAPI and async IO; PostgreSQL schema design and query tuning; Docker/Kubernetes deployment to GCP; event-driven architectures with Kafka; strong testing culture (pytest, CI gates); fluent English. Nice to have: Rust, Terraform, observability with Grafana.'
    asyncio.run(FakeTransport().say("/cv " + posting, config=_cfg()))

    # No attachment → base_cv stays None (run_flow loads disk); posting is the summary.
    assert seen["base_cv"] is None
    assert seen["inputs"] == {"summary": posting}
    assert seen["company"] == "Globex"
    assert [d["path"] for d in files.documents] == [str(out_file)]


# ── /cvnewbase arms → next upload replaces the base CV (no buttons) ────────────
def test_cvnewbase_arms_chat_and_replies(monkeypatch):
    cvtg._AWAIT_BASE.clear()
    transport = FakeTransport()
    sent = transport.sent

    asyncio.run(transport.say("/cvnewbase", config=_cfg(), chat_id="123"))

    # The chat is ARMED for a base replacement and told to send the file next.
    assert cvtg._AWAIT_BASE.get("123") is not None
    assert len(sent) == 1 and "new base CV" in sent[0]
    cvtg._AWAIT_BASE.clear()


def test_armed_document_saves_base_and_disarms(monkeypatch):
    cvtg._PENDING_CV.clear()
    cvtg._AWAIT_BASE.clear()
    cvtg._arm_base("123")  # as if /cvnewbase ran
    tex_body = "\\documentclass{article}\\begin{document}Body\\end{document}"

    # Arming a base replacement must not tailor/send a file.
    _files(monkeypatch, on_document=_boom_send_doc)
    transport = FakeTransport()
    sent = transport.sent

    saved = {}

    def _fake_save_base(text, *, timestamp=None):
        saved["text"] = text
        return cv.base_path()

    monkeypatch.setattr(cv, "save_base", _fake_save_base)

    _upload(transport, tex_body.encode(), caption="ignored caption")

    assert saved["text"] == tex_body  # the upload replaced the base (caption ignored)
    assert len(sent) == 1 and "Base CV updated" in sent[0]
    assert cvtg._AWAIT_BASE == {}  # disarmed
    assert cvtg._PENDING_CV == {}  # no one-off hold created
    cvtg._AWAIT_BASE.clear()


def test_unarmed_document_holds_and_asks_no_save(monkeypatch):
    cvtg._PENDING_CV.clear()
    cvtg._AWAIT_BASE.clear()
    tex_body = "\\documentclass{article}\\begin{document}Body\\end{document}"

    def _boom_save(text, *, timestamp=None):
        raise AssertionError("a non-armed upload must never replace the base CV")

    monkeypatch.setattr(cv, "save_base", _boom_save)
    transport = FakeTransport()
    sent = transport.sent

    _upload(transport, tex_body.encode())

    # Held for a one-off; asked for the posting; base untouched.
    assert cvtg._PENDING_CV["123"]["base_cv"] == tex_body
    assert len(sent) == 1 and sent[0].startswith("Got your CV")
    cvtg._PENDING_CV.clear()


# ── /cvbase reports the on-disk base CV ───────────────────────────────────────
def test_cvbase_reports_base(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    from lhassa.cv import paths

    paths.base_path().write_text("\\documentclass{article}\n% my base cv\n", encoding="utf-8")
    transport = FakeTransport()
    sent = transport.sent

    asyncio.run(transport.say("/cvbase", config=_cfg()))

    assert len(sent) == 1
    assert "Base CV on file" in sent[0]
    assert "\\documentclass{article}" in sent[0]  # first line reported


def test_cvbase_reports_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    transport = FakeTransport()
    sent = transport.sent

    asyncio.run(transport.say("/cvbase", config=_cfg()))

    assert len(sent) == 1 and "No base CV on file" in sent[0]


# ── save_base / base_info (disk seam) ─────────────────────────────────────────
def test_save_base_writes_and_backs_up_existing(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    from lhassa.cv import paths

    paths.base_path().write_text("OLD BASE", encoding="utf-8")

    dest = cv.save_base("NEW BASE", timestamp=1234567890)

    assert dest == paths.base_path()
    assert dest.read_text(encoding="utf-8") == "NEW BASE"
    backup = tmp_path / "cv_largo.1234567890.bak"
    assert backup.exists() and backup.read_text(encoding="utf-8") == "OLD BASE"


def test_save_base_no_existing_writes_without_backup(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))

    dest = cv.save_base("FIRST BASE", timestamp=42)

    assert dest.read_text(encoding="utf-8") == "FIRST BASE"
    assert list(tmp_path.glob("*.bak")) == []  # nothing to back up


def test_base_info_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    from lhassa.cv import paths

    # Absent base.
    absent = cv.base_info()
    assert absent == {
        "exists": False,
        "path": str(paths.base_path()),
        "chars": 0,
        "first_line": "",
    }

    # Present base; first non-blank line is reported.
    paths.base_path().write_text("\n  \n\\documentclass{article}\nrest\n", encoding="utf-8")
    info = cv.base_info()
    assert info["exists"] is True
    assert info["chars"] == len("\n  \n\\documentclass{article}\nrest\n")
    assert info["first_line"] == "\\documentclass{article}"
    assert info["path"] == str(paths.base_path())


# ── compile_pdf (tectonic); never runs a real engine in the suite ────────────
def test_compile_pdf_missing_engine_returns_none(monkeypatch, tmp_path):
    import shutil as _shutil

    from lhassa.cv import compile as cv_compile

    tex = tmp_path / "cv.tex"
    tex.write_text("\\documentclass{article}\\begin{document}Hi\\end{document}", encoding="utf-8")

    monkeypatch.setattr(_shutil, "which", lambda name: None)  # no engine on PATH

    def _boom_run(*a, **k):
        raise AssertionError("must not spawn a subprocess when the engine is absent")

    monkeypatch.setattr(cv_compile.subprocess, "run", _boom_run)

    assert cv.compile_pdf(tex) is None


def test_compile_pdf_missing_source_returns_none(monkeypatch, tmp_path):
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/tectonic")
    # A .tex that does not exist → None before any engine call.
    assert cv.compile_pdf(tmp_path / "nope.tex") is None


def test_compile_pdf_success_returns_pdf_path(monkeypatch, tmp_path):
    import shutil as _shutil
    from types import SimpleNamespace

    from lhassa.cv import compile as cv_compile

    tex = tmp_path / "cv.tex"
    tex.write_text("\\documentclass{article}\\begin{document}Hi\\end{document}", encoding="utf-8")
    expected_pdf = tmp_path / "cv.pdf"

    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/tectonic")

    calls = {}

    def _fake_run(argv, **kwargs):
        calls["argv"] = argv
        # A successful engine writes the PDF next to the .tex.
        expected_pdf.write_bytes(b"%PDF-1.5 fake")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(cv_compile.subprocess, "run", _fake_run)

    out = cv.compile_pdf(tex)
    assert out == expected_pdf and out.is_file()
    # tectonic was invoked non-interactively writing into the .tex's directory.
    assert calls["argv"][0] == "/usr/bin/tectonic"
    assert "--outdir" in calls["argv"] and str(tmp_path) in calls["argv"]


def test_compile_pdf_nonzero_exit_returns_none(monkeypatch, tmp_path):
    import shutil as _shutil
    from types import SimpleNamespace

    from lhassa.cv import compile as cv_compile

    tex = tmp_path / "cv.tex"
    tex.write_text("\\documentclass{article}\\begin{document}Hi\\end{document}", encoding="utf-8")

    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/tectonic")
    # Engine exits non-zero and writes no PDF → None (caller falls back to the .tex).
    monkeypatch.setattr(
        cv_compile.subprocess,
        "run",
        lambda argv, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom"),
    )

    assert cv.compile_pdf(tex) is None


# ── reply sends the compiled PDF when possible, else falls back to the .tex ────
def _reply_to(transport):
    from lhassa.chat.outbound import text_sender

    return text_sender(transport.send)


def _run_reply_with_record(monkeypatch, tmp_path, compile_result):
    """Drive ``_reply_with_cv`` with a completed tex record; return the sent paths.

    ``compile_result`` is a callable ``(tex_path) -> Path | None`` patched over
    ``cv.compile_pdf`` (a fake; no real engine).
    """
    tex = tmp_path / "ab12_acme.tex"
    tex.write_text("% tailored", encoding="utf-8")
    monkeypatch.setattr(cv, "compile_pdf", compile_result)

    files = _files(monkeypatch)
    transport = FakeTransport()

    record = {
        "status": "completed",
        "steps": [{"kind": "output", "output": "tex", "path": str(tex)}],
    }
    asyncio.run(
        cvtg._reply_with_cv(
            record, "flow-zvzpry", "Acme", config=_cfg(), reply=_reply_to(transport)
        )
    )
    return tex, files.documents


def test_reply_sends_pdf_when_compile_succeeds(monkeypatch, tmp_path):
    pdf = tmp_path / "ab12_acme.pdf"
    pdf.write_bytes(b"%PDF-1.5 fake")

    tex, sent_docs = _run_reply_with_record(monkeypatch, tmp_path, lambda p: pdf)

    assert len(sent_docs) == 1
    assert sent_docs[0]["path"] == str(pdf)  # the PDF, not the .tex
    assert "Acme" in sent_docs[0]["caption"]


def test_reply_falls_back_to_tex_when_compile_fails(monkeypatch, tmp_path):
    tex, sent_docs = _run_reply_with_record(monkeypatch, tmp_path, lambda p: None)

    assert len(sent_docs) == 1
    assert sent_docs[0]["path"] == str(tex)  # fell back to the raw .tex
    assert "Acme" in sent_docs[0]["caption"]


# ── THE flagship migration: the tailored CV travels UP the org chain ──────────
# (§org: "the hierarchy is the philosophy, not a feature": a surface-direct send
# is a design bug). ``_reply_with_cv`` now goes through ``org.deliver.deliver``
# instead of sending the file itself: every brain on the
# command chain (the owning department, then Lhassa) logs its own ``org-deliver``
# Episode, and Lhassa is the one that actually sends.
def test_cv_reply_writes_the_per_brain_delivery_chain(monkeypatch, tmp_path):
    from lhassa.core import db
    from lhassa.org import topology as topo_mod
    from lhassa.org.topology import BrainNode, Topology

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "brain.db")
    asyncio.run(db.init_db())

    # A minimal seeded topology: lhassa (root admin) -> interns (department leaf).
    monkeypatch.setattr(
        topo_mod,
        "load_topology",
        lambda config=None: Topology(
            [
                BrainNode(id="lhassa", role="administrator"),
                BrainNode(
                    id="interns", role="department", department="interns", parent="lhassa"
                ),
            ]
        ),
    )

    tex = tmp_path / "ab12_acme.tex"
    tex.write_text("% tailored", encoding="utf-8")
    monkeypatch.setattr(cv, "compile_pdf", lambda p: None)  # no engine -> send the .tex

    files = _files(monkeypatch)
    sent_docs = files.documents

    record = {
        "id": "run_xyz789",
        "status": "completed",
        "steps": [{"kind": "output", "output": "tex", "path": str(tex)}],
        "delegation": {"chain": ["lhassa", "interns"], "department": "interns"},
    }

    asyncio.run(
        cvtg._reply_with_cv(
            record,
            "flow-zvzpry",
            "Acme",
            config=_cfg(instance_id="inst_personal"),
            reply=_reply_to(FakeTransport()),
            posting="Backend role @ Acme",
        )
    )

    # The final outcome: exactly the same file/caption the owner used to get
    # directly from the Telegram file upload: UX is byte-for-byte identical.
    assert sent_docs == [{"path": str(tex), "caption": "Tailored CV for Acme."}]

    rows = asyncio.run(db.load_recent_episodes("inst_personal", n=100))
    rows.reverse()  # oldest first
    eps = [r for r in rows if r["despertar_por"] == "org-deliver"]

    # One hop for the department that produced it, one for Lhassa's intent to
    # deliver, and one for the actual send outcome.
    assert [e["dominio"] for e in eps] == ["interns", "lhassa", "lhassa"]
    dept_hop, root_intent, root_final = eps

    # The department hop names the file it received and forwards up, plus the
    # company metadata threaded through from the CV bridge.
    assert "produced:" in dept_hop["resultado"]  # origin brain produced, not received from itself
    assert "ab12_acme.tex" in dept_hop["resultado"]
    assert "company=Acme" in dept_hop["resultado"]
    assert "forwarding ↑ lhassa" in dept_hop["resultado"]

    # Lhassa's own hop names the artifact and its intent to deliver to the owner.
    assert "artifact" in root_intent["resultado"]
    assert "ab12_acme.tex" in root_intent["resultado"]
    assert "delivering" in root_intent["resultado"]

    # The final outcome episode is honest: it happened, and it worked.
    assert root_final["resultado"] == "delivered: ab12_acme.tex → owner (telegram, ok)"

    # The artifact itself was registered with the CV's metadata.
    from lhassa.core import artifacts

    artifact_rows = asyncio.run(artifacts.list_artifacts("inst_personal"))
    assert len(artifact_rows) == 1
    assert artifact_rows[0]["kind"] == "cv"
    assert artifact_rows[0]["origin_department"] == "interns"
    assert artifact_rows[0]["metadata"]["company"] == "Acme"
    assert artifact_rows[0]["metadata"]["posting"] == "Backend role @ Acme"
    assert artifact_rows[0]["metadata"]["run_id"] == "run_xyz789"


# ── /cv preflight and gate-failure replies ────────────────────────────────────
def test_cv_with_bare_title_asks_for_the_posting_without_waking_brains(monkeypatch):
    ran = []

    async def fake_flow(**kw):
        ran.append(kw)
        return None, "flow-x"

    monkeypatch.setattr(cvtg, "_run_cv_flow", fake_flow)
    transport = FakeTransport()
    sent = transport.sent
    asyncio.run(transport.say("/cv Analista de datos", config=_cfg()))
    assert ran == []  # six brains stayed asleep
    assert any("FULL job posting" in s for s in sent)


def test_gate_failure_reply_relays_the_teams_actual_words():
    record = {
        "steps": [
            {"kind": "task", "output": {"summary": "No vi el contenido de la convocatoria; ¿puedes pegar el posting?"}},
            {"kind": "output", "output": "tex"},
        ]
    }
    msg = cvtg._gate_failure_reply(record)
    assert "pegar el posting" in msg
