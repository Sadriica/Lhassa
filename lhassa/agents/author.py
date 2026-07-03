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
from lhassa.llm import router as _router
from lhassa.llm.gateway import BudgetExceeded
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


def _messages(name: str, description: str, examples: Optional[list]) -> list[dict]:
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
    return [{"role": "user", "content": prompt}]


async def author_candidate(
    name: str,
    description: str,
    examples: Optional[list] = None,
    *,
    provider: Optional[ModelProvider] = None,
    model: Optional[str] = None,
    completion_fn: Any = None,
    config: Any = Config,
) -> dict[str, str]:
    """Ask the routine (cheap) model to write a tool file + its test file.

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
            _messages(name, description, examples),
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


async def propose_tool(
    name: str,
    description: str,
    examples: Optional[list] = None,
    *,
    domain: str = "optimization",
    tests_spec: Optional[dict] = None,
    metric_fn: Any = None,
    baseline: Optional[float] = None,
    instance_id: Optional[str] = None,
    provider: Optional[ModelProvider] = None,
    model: Optional[str] = None,
    completion_fn: Any = None,
    config: Any = Config,
) -> dict[str, Any]:
    """Author -> version -> gate a candidate tool. Parks approval iff gates pass.

    1. :func:`author_candidate` asks the cheap model to write the tool + test.
    2. :func:`lhassa.agents.versioning.create_candidate` commits the files on a
       ``candidate/<name>`` branch (the local-git repo IS the on-disk location
       the gates then exercise from — the candidate never runs from anywhere
       else before it is trusted).
    3. :func:`lhassa.agents.gates.promote` runs the segura (quarantine) /
       correcta (tests) / mejor (metric vs. baseline) gates, in order, stopping
       at the first failure — deny-by-default, exactly like the rest of §2.8.
    4. Only when every gate passes does this park a ``tool_graduation`` approval
       (the same queue the ``POST /api/tools/{nombre}/graduate`` endpoint
       consumes) — nothing here auto-graduates a tool.

    Degrades at every step: no authored files / no git / gates not passing all
    return a descriptive, non-graduated result rather than raising.
    """
    inst = instance_id or getattr(config, "instance_id", Config.instance_id)

    files = await author_candidate(
        name,
        description,
        examples,
        provider=provider,
        model=model,
        completion_fn=completion_fn,
        config=config,
    )
    if not files:
        return {
            "name": name,
            "authored": False,
            "graduated": False,
            "reason": "author_candidate produced no files (degraded)",
        }

    ref = await _versioning.create_candidate(name, files)
    if not ref:
        return {
            "name": name,
            "authored": True,
            "graduated": False,
            "reason": "versioning unavailable (git not found on PATH)",
            "files": sorted(files),
        }

    # Read tools_repo_dir via the MODULE (not the class bound at import time) so
    # a test that monkeypatches ``Config.tools_repo_dir`` after this module was
    # first imported is still honored — the same resilient pattern
    # ``lhassa.agents.versioning`` already uses for the same setting.
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

    if outcome.get("graduated"):
        from lhassa.security import approval as _approval

        approval_id = await _approval.request_approval(
            inst,
            tipo="tool_graduation",
            payload={"accion": "tool_graduation", "tool": name, "branch": ref},
        )
        outcome["approval_id"] = approval_id

    return outcome
