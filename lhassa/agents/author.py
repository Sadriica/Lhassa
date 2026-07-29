"""
Candidate authoring — the missing first mile of self-improvement 2b (§2.8).

The four gates (:mod:`lhassa.agents.gates`) and local-git versioning
(:mod:`lhassa.agents.versioning`) have always existed in isolation: something has
to actually WRITE a candidate tool before it can be quarantined, gated and
graduated. This module is that first mile.

``author_candidate`` asks the ROUTINE (cheap) model — never the hard/smart one,
this is a small mechanical writing task — for a small, self-contained Python tool
plus its test file. It NEVER executes what comes back; it only returns text.
``propose_tool`` chains authoring -> :func:`lhassa.agents.versioning.create_candidate`
-> :func:`lhassa.agents.gates.promote` (which runs the candidate through the
quarantine sandbox before anything else touches it) and, only if every gate
passes, parks a ``tool_graduation`` approval — the same queue
``POST /api/tools/{nombre}/graduate`` consumes. Nothing here auto-graduates a
tool: a human (or the autonomy knob) still has to say yes.

Degrades gracefully, exactly like :func:`lhassa.memory.consolidate.distill_episodes`:
no provider configured / budget exceeded / a malformed reply all return an empty
result rather than raising.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from typing import Any, Optional

from loguru import logger

from lhassa import config as _config_mod
from lhassa.agents import gates as _gates
from lhassa.agents import versioning as _versioning
from lhassa.config import Config
from lhassa.core import db as _db
from lhassa.core.episode import Episode
from lhassa.llm import router as _router
from lhassa.llm.gateway import BudgetExceeded
from lhassa.llm.moa import moa as _moa_consult
from lhassa.llm.provider import ModelProvider, ProviderNotConfigured, get_model_provider

# A cheap, mechanical writing task — plenty of budget for a small tool + test.
_MAX_TOKENS = 1500


def _extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object out of a model answer. Never raises."""
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        pass
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fenced:
        try:
            obj = json.loads(fenced.group(1))
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def _messages(
    name: str, description: str, examples: Optional[list], feedback: Optional[str] = None
) -> list[dict]:
    ex = "\n".join(f"- {e}" for e in (examples or [])) or "(none given)"
    prompt = (
        "Write a small, self-contained Python tool and its test file.\n"
        f"Tool name: {name!r}. Purpose: {description}\n"
        f"Usage examples:\n{ex}\n\n"
        "Rules: pure standard library only (no third-party imports, no network "
        "access, no filesystem access outside the current directory), a single "
        f"top-level function, deterministic behavior. The tool file must be "
        f"importable as `{name}.py`; the test file must be `test_{name}.py` "
        f"runnable with `python3 -m pytest -q test_{name}.py`.\n\n"
        "Return ONLY JSON of the form "
        '{"files": {"' + name + '.py": "<source>", "test_' + name + '.py": "<source>"}}'
        " — no prose, no markdown fences."
    )
    if feedback:
        # Iteration turn (turn > 1): thread the previous turn's objective gate
        # failure back in — never a raw pass/fail judgment of the model's own
        # output, only the independent verifier's detail text (loop-engineering
        # principle: the doer never judges).
        prompt += (
            "\n\nThe previous attempt failed an objective check. Fix it and "
            f"resubmit the SAME JSON form.\nFeedback: {feedback}"
        )
    return [{"role": "user", "content": prompt}]


async def author_candidate(
    name: str,
    description: str,
    examples: Optional[list] = None,
    *,
    feedback: Optional[str] = None,
    provider: Optional[ModelProvider] = None,
    model: Optional[str] = None,
    completion_fn: Any = None,
    config: Any = Config,
) -> dict[str, str]:
    """Ask the routine (cheap) model to write a tool file + its test file.

    ``feedback``, when given (turn > 1 of :func:`propose_tool`'s iteration loop),
    is appended to the SAME authoring prompt — the failing gate's objective
    detail text, never a judgment of the model's own prior output.

    Returns ``{"<name>.py": "...", "test_<name>.py": "..."}`` on success, or an
    EMPTY dict on any degrade path (no provider configured, budget exceeded,
    malformed reply). This function ONLY produces text — it never executes the
    returned code; the caller (:func:`propose_tool`) is what hands it to the
    quarantine + gates pipeline.
    """
    try:
        resolved_provider = provider or get_model_provider(config)
        resolved_model = model or _router.pick_model(
            resolved_provider, hard=False, config=config
        )
    except (BudgetExceeded, ProviderNotConfigured) as exc:
        logger.debug(f"author_candidate({name}): provider unavailable: {exc}")
        return {}
    except Exception as exc:
        logger.debug(f"author_candidate({name}): provider setup failed: {exc}")
        return {}

    try:
        opts: dict[str, Any] = {}
        if completion_fn is not None:
            opts["completion_fn"] = completion_fn
        completion = await resolved_provider.complete(
            _messages(name, description, examples, feedback),
            model=resolved_model,
            max_tokens=_MAX_TOKENS,
            **opts,
        )
    except (BudgetExceeded, ProviderNotConfigured) as exc:
        logger.debug(f"author_candidate({name}): deferred: {exc}")
        return {}
    except Exception as exc:
        logger.debug(f"author_candidate({name}): completion failed: {exc}")
        return {}

    data = _extract_json(completion.text)
    files = data.get("files")
    if not isinstance(files, dict):
        logger.debug(f"author_candidate({name}): model returned no files")
        return {}

    out: dict[str, str] = {}
    for path, content in files.items():
        if isinstance(path, str) and isinstance(content, str) and path.strip():
            out[path.strip()] = content
    return out


def _default_tests_spec(repo_dir: str, files: dict[str, str]) -> Optional[dict]:
    """Build a default ``correcta`` spec: run the authored test file, if any."""
    test_path = next((p for p in files if p.startswith("test_")), None)
    if test_path is None:
        return None
    cmd = f"cd {shlex.quote(repo_dir)} && python3 -m pytest -q {shlex.quote(test_path)}"
    return {"type": "command", "cmd": cmd}


def _quarantine_script(name: str, files: dict[str, str]) -> str:
    """Build the gate-1 (segura) candidate: a self-contained bash script.

    Materializes every authored file via a heredoc and, if a test file was
    authored, runs it — all INSIDE whatever workdir the quarantine sandbox
    gives it (a fresh, scrubbed, no-network temp directory; see
    :func:`lhassa.security.quarantine.run_in_quarantine`). This is the actual
    "run it caged" step: the authored code never executes anywhere else before
    this passes. The heredoc delimiter is content-derived so it cannot collide
    with an authored file that happens to contain the literal marker text.
    """
    delim = "LHASSA_EOF_" + hashlib.sha1(
        ("".join(files.values())).encode("utf-8")
    ).hexdigest()[:12]
    lines: list[str] = ["set -e"]
    for path, content in files.items():
        safe_path = shlex.quote(path)
        body = content if content.endswith("\n") else content + "\n"
        lines.append(f"cat > {safe_path} <<'{delim}'\n{body}{delim}")
    test_path = next((p for p in files if p.startswith("test_")), None)
    if test_path:
        lines.append(f"python3 -m pytest -q {shlex.quote(test_path)}")
    else:
        # No test authored — a best-effort syntax check of every file.
        for path in files:
            if path.endswith(".py"):
                lines.append(f"python3 -m py_compile {shlex.quote(path)}")
    return "\n".join(lines)


async def _forge_episode(
    instance_id: str, *, domain: str, turn: int, max_turns: int, resultado: str
) -> None:
    """Log one Episode per forge turn (observability from the first commit)."""
    try:
        ep = Episode.begin(despertar_por="forge", instance_id=instance_id)
        ep.dominio = domain
        ep.finish(
            decision=f"forge turn {turn}/{max_turns}",
            accion="author_tool",
            resultado=resultado,
        )
        await ep.append()
    except Exception as exc:  # observability must never break the loop
        logger.debug(f"propose_tool: could not log forge episode: {exc}")


async def _moa_code_review(
    name: str, description: str, files: dict[str, str], *, config: Any
) -> Optional[dict[str, Any]]:
    """One opt-in :func:`lhassa.llm.moa.moa` pass reviewing a GRADUATED candidate.

    Advisory only — this runs AFTER the objective gates already passed, purely
    to give the owner a panel opinion on the approval card; it never gates or
    blocks graduation. Defensive: any failure (no panelists, moa() degrading,
    an unexpected exception) returns ``None`` so the caller attaches nothing
    rather than breaking a graduation that already earned its approval.
    """
    try:
        listing = "\n\n".join(
            f"--- {path} ---\n{content}" for path, content in files.items()
        )
        question = (
            f"Review this self-authored tool named {name!r} (purpose: "
            f"{description}) for correctness and safety concerns before a human "
            f"approves it for use. It already passed automated tests and a "
            f"quarantine sandbox run — focus on anything those checks would "
            f"miss (edge cases, unsafe assumptions, unclear behavior).\n\n"
            f"{listing}"
        )
        result = await _moa_consult(
            [{"role": "user", "content": question}], config=config
        )
        return {
            "text": result.text,
            "panel": result.panel,
            "aggregator_provider": result.aggregator_provider,
            "total_cost_usd": result.total_cost_usd,
        }
    except Exception as exc:
        logger.debug(f"propose_tool({name}): moa_verdict review degraded: {exc}")
        return None


async def propose_tool(
    name: str,
    description: str,
    examples: Optional[list] = None,
    *,
    domain: str = "optimization",
    tests_spec: Optional[dict] = None,
    metric_fn: Any = None,
    baseline: Optional[float] = None,
    max_turns: int = 3,
    instance_id: Optional[str] = None,
    provider: Optional[ModelProvider] = None,
    model: Optional[str] = None,
    completion_fn: Any = None,
    moa_verdict: bool = False,
    config: Any = Config,
) -> dict[str, Any]:
    """Author -> version -> gate a candidate tool, ITERATING up to ``max_turns``.

    Loop-engineering, applied to the forge (Wave A): a bounded, verifier-terminated
    iteration recorded on the ``loops`` table (:mod:`lhassa.core.db`).

    Each turn:
      1. :func:`author_candidate` asks the cheap model to write the tool + test —
         turn 1 as a fresh request; turn N>1 with the SAME prompt plus the
         previous turn's failing :class:`~lhassa.agents.gates.GateResult` detail
         appended as feedback. The doer never judges its own output — only the
         independent gates' objective detail threads forward.
      2. :func:`lhassa.agents.versioning.create_candidate` commits the files on a
         ``candidate/<name>`` branch (re-pointed fresh each turn).
      3. :func:`lhassa.agents.gates.promote` runs the segura (quarantine) /
         correcta (tests) / mejor (metric vs. baseline) gates, in order, stopping
         at the first failure — deny-by-default, exactly like the rest of §2.8.

    Every turn logs one Episode (``despertar_por="forge"``) before the loop moves
    on. A ``segura``/``correcta`` failure is RETRYABLE (feedback can plausibly fix
    it) and bumps the turn; a ``mejor`` failure with no baseline/metric_fn is NOT
    retryable (no feedback can conjure a baseline) and stops immediately with the
    same honest single-pass message this function has always returned. Turns are
    capped BEFORE any work — a candidate that never clears the gates within
    ``max_turns`` ends the loop ``status="capped"`` rather than looping forever.

    Only when a turn's gates all pass does this park a ``tool_graduation``
    approval (the same queue the ``POST /api/tools/{nombre}/graduate`` endpoint
    consumes) — nothing here auto-graduates a tool.

    ``moa_verdict`` (default ``False`` — byte-identical behavior when unset) is
    an OPT-IN extra: when a candidate graduates the objective gates AND this is
    True, one extra :func:`lhassa.llm.moa.moa` consultation asks a panel of
    models to review the authored code for correctness/safety concerns, and the
    synthesized review is attached to the graduation approval's payload
    (``moa_review``) so the owner sees the panel's opinion on the approval card.
    The panel is advisory color for the human — it NEVER overrides the
    objective gates, which remain the hard filter; a failed/degraded MoA
    consultation still lets graduation proceed (the approval simply carries no
    ``moa_review``).

    Degrades at every step: no authored files / no git / a mid-loop
    ``BudgetExceeded`` all stop the loop (``status="iterate"``, never raising)
    and return a descriptive, non-graduated result.
    """
    inst = instance_id or getattr(config, "instance_id", Config.instance_id)

    loop_id = ""
    try:
        loop_id = await _db.insert_loop({
            "id": _db._new_id("loop"),
            "instance_id": inst,
            "goal": f"tool {name} graduates the gates",
            "kind": "closed",
            "origin": "forge",
            "status": "plan",
            "turn": 0,
            "max_turns": max_turns,
            "verifier_spec": {"type": "gates"},
            "created_at": _db._now(),
            "updated_at": _db._now(),
        })
    except Exception as exc:
        logger.debug(f"propose_tool({name}): could not create loop row: {exc}")

    async def _advance(**kwargs) -> None:
        if not loop_id:
            return
        try:
            await _db.advance_loop(inst, loop_id, **kwargs)
        except Exception as exc:
            logger.debug(f"propose_tool({name}): could not advance loop: {exc}")

    feedback: Optional[str] = None
    outcome: dict[str, Any] = {}
    turn = 0  # 0-indexed, mirrors the loops row's own ``turn`` column

    while turn < max_turns:
        attempt = turn + 1

        files = await author_candidate(
            name,
            description,
            examples,
            feedback=feedback,
            provider=provider,
            model=model,
            completion_fn=completion_fn,
            config=config,
        )
        if not files:
            # No provider / budget exceeded / malformed reply mid-loop — degrade
            # without raising, exactly like the (former) single-pass behavior.
            reason = "author_candidate produced no files (degraded)"
            await _forge_episode(
                inst, domain=domain, turn=attempt, max_turns=max_turns,
                resultado=reason,
            )
            await _advance(status="iterate")
            return {
                "name": name,
                "authored": False,
                "graduated": False,
                "reason": reason,
                "loop_id": loop_id,
                "turns": attempt,
            }

        ref = await _versioning.create_candidate(name, files)
        if not ref:
            reason = "versioning unavailable (git not found on PATH)"
            await _forge_episode(
                inst, domain=domain, turn=attempt, max_turns=max_turns,
                resultado=reason,
            )
            await _advance(status="failed")
            return {
                "name": name,
                "authored": True,
                "graduated": False,
                "reason": reason,
                "files": sorted(files),
                "loop_id": loop_id,
                "turns": attempt,
            }

        # Read tools_repo_dir via the MODULE (not the class bound at import time)
        # so a test that monkeypatches ``Config.tools_repo_dir`` after this
        # module was first imported is still honored — the same resilient
        # pattern ``lhassa.agents.versioning`` already uses for the same setting.
        repo_dir = str(_config_mod.Config.tools_repo_dir)
        resolved_tests_spec = tests_spec or _default_tests_spec(repo_dir, files)

        candidate = _quarantine_script(name, files)
        outcome = await _gates.promote(
            inst,
            name,
            candidate,
            domain=domain,
            tests_spec=resolved_tests_spec,
            metric_fn=metric_fn,
            baseline=baseline,
        )
        outcome["name"] = name
        outcome["authored"] = True
        outcome["branch"] = ref
        outcome["loop_id"] = loop_id
        outcome["turns"] = attempt

        await _forge_episode(
            inst, domain=domain, turn=attempt, max_turns=max_turns,
            resultado=f"{outcome.get('estado')}: {outcome.get('reason')}",
        )

        if outcome.get("graduated"):
            from lhassa.security import approval as _approval

            payload: dict[str, Any] = {
                "accion": "tool_graduation", "tool": name, "branch": ref,
            }
            if moa_verdict:
                review = await _moa_code_review(name, description, files, config=config)
                if review is not None:
                    payload["moa_review"] = review

            approval_id = await _approval.request_approval(
                inst,
                tipo="tool_graduation",
                payload=payload,
            )
            outcome["approval_id"] = approval_id
            await _advance(status="done", verdict={"passed": True})
            return outcome

        gates_run: list = outcome.get("gates") or []
        failed = next((g for g in gates_run if not g.passed), None)

        # The "mejor" gate cannot be fixed by feedback when the caller never gave
        # it a baseline/metric_fn to prove "better" against — that is a caller
        # config gap, not something a rewritten candidate can address. Stop
        # early with the same honest message single-pass authoring always gave.
        if failed is not None and failed.gate == _gates.GATE_MEJOR and (
            metric_fn is None or baseline is None
        ):
            await _advance(
                status="failed",
                verdict={"passed": False, "gate": failed.gate, "detail": failed.detail},
            )
            return outcome

        verdict = (
            {"passed": False, "gate": failed.gate, "detail": failed.detail}
            if failed is not None
            else {"passed": False, "gate": "unknown", "detail": outcome.get("reason", "")}
        )
        await _advance(status="iterate", verdict=verdict, bump_turn=True)
        feedback = f"gate {verdict['gate']} failed: {verdict['detail']}"
        turn += 1

    # Exhausted max_turns without graduating — cap it, do not loop forever.
    await _advance(status="capped")
    if outcome:
        outcome["capped"] = True
        outcome["reason"] = (
            f"capped after {max_turns} turns without graduating; last: "
            f"{outcome.get('reason', '')}"
        )
    return outcome or {
        "name": name,
        "authored": False,
        "graduated": False,
        "reason": f"capped after {max_turns} turns",
        "loop_id": loop_id,
        "turns": max_turns,
    }
