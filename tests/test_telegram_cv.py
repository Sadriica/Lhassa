"""Tests for the Telegram → CV work-flow bridge.

Never hits the real Telegram API and never calls a real model. The Bot API file
helpers (``get_file`` / ``send_document``) are monkeypatched; the CV work-flow is
either faked (routing tests) or run fully OFFLINE ($0, the injected base-CV override
test). Covers:

  * :func:`lhassa.cv.extract_text` on a ``.tex`` payload and a tiny generated PDF;
  * a document update → extract → ``run_flow(base_cv=<extracted>)`` → ``sendDocument``;
  * a no-caption document → asks for the target role (no flow, no download);
  * a gate failure → a short text reply, and NO file sent;
  * the ``run_flow`` ``base_cv`` override is honoured (a fake executor sees the base).
"""

import asyncio
from types import SimpleNamespace

from lhassa import cv
from lhassa.notify import telegram, telegram_api, telegram_inbound


# ── a tiny, valid PDF with extractable text (no third-party writer needed) ────
def _make_pdf(text: str) -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        None,  # content stream, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode() + b") Tj ET"
    objs[3] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 " + str(n).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    return bytes(out)


# ── extract_text ──────────────────────────────────────────────────────────────
def test_extract_text_reads_tex():
    data = "\\documentclass{article}\n\\begin{document}\nHola\n\\end{document}".encode()
    assert cv.extract_text(data, filename="cv.tex") == data.decode("utf-8")


def test_extract_text_reads_pdf():
    out = cv.extract_text(_make_pdf("Hello CV Test 42"), filename="cv.pdf")
    assert "Hello CV Test 42" in out


def test_extract_text_sniffs_pdf_without_extension():
    # No ``.pdf`` name, but the %PDF- magic bytes route it to the PDF extractor.
    out = cv.extract_text(_make_pdf("Magic Sniffed"), filename="document")
    assert "Magic Sniffed" in out


def test_extract_text_empty_and_garbage_never_raise():
    assert cv.extract_text(b"") == ""
    # A ``.pdf`` name over non-PDF bytes → pypdf fails → "" (never raises).
    assert cv.extract_text(b"not a pdf at all", filename="x.pdf") == ""


# ── company parsing ─────────────────────────────────────────────────────────────
def test_parse_company_forms():
    assert telegram_inbound._parse_company("Backend role @ Acme Corp") == "Acme Corp"
    assert telegram_inbound._parse_company("company: Globex") == "Globex"
    assert telegram_inbound._parse_company("company=Initech.") == "Initech"
    assert telegram_inbound._parse_company("just a plain job posting") is None


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


def _client_returning(payload):
    """An httpx.AsyncClient stand-in whose GET returns ``payload`` (getUpdates)."""

    class _Resp:
        status_code = 200
        text = "ok"

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _Resp()

    return _Client


def _patch_send(monkeypatch):
    sent = []

    async def _fake_send(text, *, config=None):
        sent.append(text)
        return True

    monkeypatch.setattr(telegram, "send", _fake_send)
    return sent


# ── document update → extract → run_flow(base_cv=...) → sendDocument ─────────────
def test_document_update_runs_flow_and_sends_tex(monkeypatch, tmp_path):
    tex_body = (
        "\\documentclass{article}\\begin{document}"
        "owner@example.edu +57 300 111 2222\\end{document}"
    )
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 300,
                "message": {
                    "chat": {"id": 123},
                    "document": {"file_id": "FID", "file_name": "cv.tex"},
                    "caption": "Senior Backend Engineer @ Acme",
                },
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    # Bot API file helpers — no network.
    async def _fake_get_file(file_id, *, config=None):
        assert file_id == "FID"
        return tex_body.encode()

    monkeypatch.setattr(telegram_api, "get_file", _fake_get_file)

    sent_docs = []

    async def _fake_send_document(path, *, caption="", config=None):
        sent_docs.append({"path": str(path), "caption": caption})
        return True

    monkeypatch.setattr(telegram_api, "send_document", _fake_send_document)
    sent_text = _patch_send(monkeypatch)
    # No real LaTeX engine in the suite: compile fails → fall back to the .tex.
    monkeypatch.setattr(cv, "compile_pdf", lambda p: None)

    # The configured flow exists (any object; run_flow is faked below).
    import lhassa.flows.store as store_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: object())

    # Fake run_flow: record what it was called with, return a completed tex record.
    out_file = tmp_path / "ab12cd_acme.tex"
    out_file.write_text("% tailored", encoding="utf-8")
    seen = {}

    async def _fake_run_flow(flow, *, inputs, config, company, base_cv):
        seen.update(inputs=inputs, company=company, base_cv=base_cv)
        return {
            "status": "completed",
            "steps": [
                {"kind": "task", "ok": True},
                {"kind": "gate", "passed": True},
                {"kind": "output", "output": "tex", "path": str(out_file)},
            ],
        }

    import lhassa.flows.engine as engine_mod

    monkeypatch.setattr(engine_mod, "run_flow", _fake_run_flow)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert new_offset == 301
    # run_flow got the EXTRACTED text as base_cv, the caption as summary, parsed company.
    assert seen["base_cv"] == tex_body
    assert seen["inputs"] == {"summary": "Senior Backend Engineer @ Acme"}
    assert seen["company"] == "Acme"
    # The exported .tex was sent as a document (not a plain text reply).
    assert sent_docs and sent_docs[0]["path"] == str(out_file)
    assert "Acme" in sent_docs[0]["caption"]
    assert sent_text == []


def test_no_caption_document_holds_cv_and_asks_for_role(monkeypatch):
    telegram_inbound._PENDING_CV.clear()
    telegram_inbound._AWAIT_BASE.clear()
    tex_body = "\\documentclass{article}\\begin{document}Body\\end{document}"
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 310,
                "message": {
                    "chat": {"id": 123},
                    "document": {"file_id": "FID", "file_name": "cv.tex"},
                },
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    async def _fake_get_file(file_id, *, config=None):
        return tex_body.encode()

    async def _boom_send_doc(path, *, caption="", config=None):
        raise AssertionError("must not send a file without a target role")

    monkeypatch.setattr(telegram_api, "get_file", _fake_get_file)
    monkeypatch.setattr(telegram_api, "send_document", _boom_send_doc)
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert new_offset == 311
    # Not armed → the CV is HELD and we ask (plain text) for the posting; no file/flow.
    assert telegram_inbound._PENDING_CV["123"]["base_cv"] == tex_body
    assert len(sent) == 1 and sent[0].startswith("Got your CV")
    telegram_inbound._PENDING_CV.clear()


def test_held_cv_then_followup_text_runs_flow(monkeypatch, tmp_path):
    telegram_inbound._PENDING_CV.clear()
    telegram_inbound._AWAIT_BASE.clear()
    tex_body = (
        "\\documentclass{article}\\begin{document}"
        "owner@example.edu +57 300 111 2222\\end{document}"
    )
    # Two updates in order: the caption-less document, then the follow-up job posting.
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 400,
                "message": {
                    "chat": {"id": 123},
                    "document": {"file_id": "FID", "file_name": "cv.tex"},
                },
            },
            {
                "update_id": 401,
                "message": {"chat": {"id": 123}, "text": "AI Engineer @ Sezzle"},
            },
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    async def _fake_get_file(file_id, *, config=None):
        return tex_body.encode()

    monkeypatch.setattr(telegram_api, "get_file", _fake_get_file)

    sent_docs = []

    async def _fake_send_document(path, *, caption="", config=None):
        sent_docs.append(str(path))
        return True

    monkeypatch.setattr(telegram_api, "send_document", _fake_send_document)
    _patch_send(monkeypatch)
    monkeypatch.setattr(cv, "compile_pdf", lambda p: None)  # no engine → send the .tex

    import lhassa.flows.store as store_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: object())

    out_file = tmp_path / "zz99_sezzle.tex"
    out_file.write_text("% tailored", encoding="utf-8")
    seen = {}

    async def _fake_run_flow(flow, *, inputs, config, company, base_cv):
        seen.update(inputs=inputs, company=company, base_cv=base_cv)
        return {
            "status": "completed",
            "steps": [{"kind": "output", "output": "tex", "path": str(out_file)}],
        }

    import lhassa.flows.engine as engine_mod

    monkeypatch.setattr(engine_mod, "run_flow", _fake_run_flow)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    # The follow-up text ran the flow with the HELD CV as base + the text as the posting.
    assert seen["base_cv"] == tex_body
    assert seen["inputs"] == {"summary": "AI Engineer @ Sezzle"}
    assert seen["company"] == "Sezzle"
    assert sent_docs == [str(out_file)]
    assert telegram_inbound._PENDING_CV == {}  # consumed
    telegram_inbound._PENDING_CV.clear()


def test_gate_failure_replies_and_sends_no_file(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 320,
                "message": {
                    "chat": {"id": 123},
                    "document": {"file_id": "FID", "file_name": "cv.tex"},
                    "caption": "Backend @ Acme",
                },
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    async def _fake_get_file(file_id, *, config=None):
        return b"owner@example.edu +57 300 111 2222 base cv"

    monkeypatch.setattr(telegram_api, "get_file", _fake_get_file)

    async def _boom_send_doc(path, *, caption="", config=None):
        raise AssertionError("must not send a file when a gate failed")

    monkeypatch.setattr(telegram_api, "send_document", _boom_send_doc)
    sent = _patch_send(monkeypatch)

    import lhassa.flows.engine as engine_mod
    import lhassa.flows.store as store_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: object())

    async def _fake_run_flow(flow, *, inputs, config, company, base_cv):
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

    monkeypatch.setattr(engine_mod, "run_flow", _fake_run_flow)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert new_offset == 321
    assert len(sent) == 1
    assert "contact details" in sent[0].lower()


def test_missing_flow_replies_not_set_up(monkeypatch):
    payload = {
        "ok": True,
        "result": [
            {
                "update_id": 330,
                "message": {
                    "chat": {"id": 123},
                    "document": {"file_id": "FID", "file_name": "cv.tex"},
                    "caption": "Backend role",
                },
            }
        ],
    }
    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(payload)
    )

    async def _fake_get_file(file_id, *, config=None):
        return b"a base cv body"

    monkeypatch.setattr(telegram_api, "get_file", _fake_get_file)
    sent = _patch_send(monkeypatch)

    import lhassa.flows.store as store_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: None)

    asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))
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

    import lhassa.flows.engine as engine_mod
    import lhassa.flows.store as store_mod

    monkeypatch.setattr(store_mod.FlowStore, "get", lambda self, fid: object())
    seen = {}

    async def _fake_run_flow(flow, *, inputs, config, company, base_cv):
        seen.update(inputs=inputs, company=company, base_cv=base_cv)
        return {
            "status": "completed",
            "steps": [{"kind": "output", "output": "tex", "path": str(out_file)}],
        }

    monkeypatch.setattr(engine_mod, "run_flow", _fake_run_flow)

    sent_docs = []

    async def _fake_send_document(path, *, caption="", config=None):
        sent_docs.append(str(path))
        return True

    monkeypatch.setattr(telegram_api, "send_document", _fake_send_document)
    _patch_send(monkeypatch)
    monkeypatch.setattr(cv, "compile_pdf", lambda p: None)  # no engine → send the .tex

    asyncio.run(telegram_inbound._answer("/cv Backend role @ Globex", config=_cfg()))

    # No attachment → base_cv stays None (run_flow loads disk); posting is the summary.
    assert seen["base_cv"] is None
    assert seen["inputs"] == {"summary": "Backend role @ Globex"}
    assert seen["company"] == "Globex"
    assert sent_docs == [str(out_file)]


# ── /cvnewbase arms → next upload replaces the base CV (no buttons) ────────────
def _document_payload(update_id, *, file_name="cv.tex", caption=None):
    message = {
        "chat": {"id": 123},
        "document": {"file_id": "FID", "file_name": file_name},
    }
    if caption is not None:
        message["caption"] = caption
    return {"ok": True, "result": [{"update_id": update_id, "message": message}]}


def test_cvnewbase_arms_chat_and_replies(monkeypatch):
    telegram_inbound._AWAIT_BASE.clear()
    sent = _patch_send(monkeypatch)

    asyncio.run(telegram_inbound._answer("/cvnewbase", config=_cfg(), chat_id="123"))

    # The chat is ARMED for a base replacement and told to send the file next.
    assert telegram_inbound._AWAIT_BASE.get("123") is not None
    assert len(sent) == 1 and "new base CV" in sent[0]
    telegram_inbound._AWAIT_BASE.clear()


def test_armed_document_saves_base_and_disarms(monkeypatch):
    telegram_inbound._PENDING_CV.clear()
    telegram_inbound._AWAIT_BASE.clear()
    telegram_inbound._arm_base("123")  # as if /cvnewbase ran
    tex_body = "\\documentclass{article}\\begin{document}Body\\end{document}"

    monkeypatch.setattr(
        telegram_inbound.httpx,
        "AsyncClient",
        _client_returning(_document_payload(600, caption="ignored caption")),
    )

    async def _fake_get_file(file_id, *, config=None):
        return tex_body.encode()

    async def _boom_send_doc(path, *, caption="", config=None):
        raise AssertionError("arming a base replacement must not tailor/send a file")

    monkeypatch.setattr(telegram_api, "get_file", _fake_get_file)
    monkeypatch.setattr(telegram_api, "send_document", _boom_send_doc)
    sent = _patch_send(monkeypatch)

    saved = {}

    def _fake_save_base(text, *, timestamp=None):
        saved["text"] = text
        return cv.base_path()

    monkeypatch.setattr(cv, "save_base", _fake_save_base)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert new_offset == 601
    assert saved["text"] == tex_body  # the upload replaced the base (caption ignored)
    assert len(sent) == 1 and "Base CV updated" in sent[0]
    assert telegram_inbound._AWAIT_BASE == {}  # disarmed
    assert telegram_inbound._PENDING_CV == {}  # no one-off hold created
    telegram_inbound._AWAIT_BASE.clear()


def test_unarmed_document_holds_and_asks_no_save(monkeypatch):
    telegram_inbound._PENDING_CV.clear()
    telegram_inbound._AWAIT_BASE.clear()
    tex_body = "\\documentclass{article}\\begin{document}Body\\end{document}"

    monkeypatch.setattr(
        telegram_inbound.httpx, "AsyncClient", _client_returning(_document_payload(610))
    )

    async def _fake_get_file(file_id, *, config=None):
        return tex_body.encode()

    monkeypatch.setattr(telegram_api, "get_file", _fake_get_file)

    def _boom_save(text, *, timestamp=None):
        raise AssertionError("a non-armed upload must never replace the base CV")

    monkeypatch.setattr(cv, "save_base", _boom_save)
    sent = _patch_send(monkeypatch)

    new_offset = asyncio.run(telegram_inbound.poll_once(_cfg(), offset=None))

    assert new_offset == 611
    # Held for a one-off; asked for the posting; base untouched.
    assert telegram_inbound._PENDING_CV["123"]["base_cv"] == tex_body
    assert len(sent) == 1 and sent[0].startswith("Got your CV")
    telegram_inbound._PENDING_CV.clear()


# ── /cvbase reports the on-disk base CV ───────────────────────────────────────
def test_cvbase_reports_base(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    from lhassa.cv import paths

    paths.base_path().write_text("\\documentclass{article}\n% my base cv\n", encoding="utf-8")
    sent = _patch_send(monkeypatch)

    asyncio.run(telegram_inbound._answer("/cvbase", config=_cfg()))

    assert len(sent) == 1
    assert "Base CV on file" in sent[0]
    assert "\\documentclass{article}" in sent[0]  # first line reported


def test_cvbase_reports_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("LHASSA_CV_DIR", str(tmp_path))
    sent = _patch_send(monkeypatch)

    asyncio.run(telegram_inbound._answer("/cvbase", config=_cfg()))

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

    # Present base — first non-blank line is reported.
    paths.base_path().write_text("\n  \n\\documentclass{article}\nrest\n", encoding="utf-8")
    info = cv.base_info()
    assert info["exists"] is True
    assert info["chars"] == len("\n  \n\\documentclass{article}\nrest\n")
    assert info["first_line"] == "\\documentclass{article}"
    assert info["path"] == str(paths.base_path())


# ── compile_pdf (tectonic) — never runs a real engine in the suite ────────────
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
def _run_reply_with_record(monkeypatch, tmp_path, compile_result):
    """Drive ``_reply_with_cv`` with a completed tex record; return the sent paths.

    ``compile_result`` is a callable ``(tex_path) -> Path | None`` patched over
    ``cv.compile_pdf`` (a fake — no real engine).
    """
    tex = tmp_path / "ab12_acme.tex"
    tex.write_text("% tailored", encoding="utf-8")
    monkeypatch.setattr(cv, "compile_pdf", compile_result)

    sent_docs = []

    async def _fake_send_document(path, *, caption="", config=None):
        sent_docs.append({"path": str(path), "caption": caption})
        return True

    monkeypatch.setattr(telegram_api, "send_document", _fake_send_document)
    _patch_send(monkeypatch)

    record = {
        "status": "completed",
        "steps": [{"kind": "output", "output": "tex", "path": str(tex)}],
    }
    asyncio.run(
        telegram_inbound._reply_with_cv(record, "flow-zvzpry", "Acme", config=_cfg())
    )
    return tex, sent_docs


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
