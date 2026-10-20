"""High-risk closure guard; every dangerous action must be classified.

Style guard in the spirit of ``tests/test_architecture.py``: deterministic, $0,
no LLM, pure ``ast`` over the real registry. Today nothing but human discipline
forces a NEW action registered in ``core/actions.ACTIONS`` to be classified
against the high-risk frontier (``Config.always_approve_actions`` /
``requires_approval`` in the allowlist). This test closes that gap:

1. It loads the real registry (``actionpack.register_all()``, same as runtime).
2. For each registered action it scans the handler's source; module-level
   import aliases resolved, then a transitive walk of same-module helpers;
   for calls that touch a DANGEROUS SURFACE:
   - ``exec``  : subprocess / os.system / asyncio.create_subprocess_*
   - ``net``   : httpx / requests / urllib (outbound network)
   - ``send``  : ``lhassa.notify`` or ``lhassa.org.deliver`` (external delivery)
   - ``fs-del``: rmtree / unlink (destructive filesystem)
3. Every flagged action MUST be either (a) ``requires_approval=True`` in the
   allowlist, (b) in ``Config.always_approve_actions``, or (c) listed in
   ``REVIEWED_LOW_RISK`` below with a written reason; the ALLOWED_EXCEPTIONS
   pattern from the architecture guard.

Registering a new action that shells out, phones out, or delivers externally
WITHOUT classifying it breaks this test. That is the point: the failure message
tells the author exactly which list to update, and updating it is itself the
review.

The walk resolves module-level AND function-local imports (this codebase
imports lazily by convention, so the latter is where most of the danger is),
and follows a ``lhassa.*`` callee ONE module hop past the handler; that hop is
what catches a thin wrapper like ``check_health`` -> ``health.checks.run_all``.
The bound is deliberate: at two hops or more the scan degenerates into the
transitive closure of the package and flags every action that can reach the
embedder, which is noise, not review. Danger buried deeper than one hop is a
known blind spot; widen ``_MAX_HOPS`` or the markers when a new shape appears.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap

import pytest

from lhassa import actionpack
from lhassa.config import Config
from lhassa.core import actions
from lhassa.security.allowlist import build_allowlist

# ── The reviewed-low-risk register ──────────────────────────────────────────
# Actions whose dangerous-surface hit was reviewed and judged acceptable
# WITHOUT a standing approval gate. One entry per action, with the reason.
# Removing an entry (or registering a new dangerous action without adding one)
# must break the test; that is the review forcing-function.
REVIEWED_LOW_RISK: dict[str, str] = {
    # send: delivers only to the OWNER's own configured channels
    # (LHASSA_NOTIFY_CHANNELS); no arbitrary recipient, no payload from
    # untrusted params beyond the text itself.
    "notify": "fan-out to the owner's own configured channels only",
    "send_digest": "deterministic $0 digest to the owner's channels",
    "send_reminder": "owner-minted reminder back to the owner's channels",
    "check_health": "health digest notification to the owner's channels",
    "detect_repetition": "parks an approval + notifies the owner; the send is the ack",
    "research_reply": "web-search answer delivered back to the owner over Telegram",
    "draft_research_note": "draft written locally; notify is the completion ack",
    # net: read-only outbound fetches, SSRF-guarded (actionpack/_ssrf.py)
    "ping_url": "read-only reachability check; SSRF-guarded, bounded",
    "web_fetch": "read-only fetch; SSRF-guarded, size/time-bounded",
    "research_intake": "captures an owner-submitted URL into a custody bundle; SSRF-guarded",
    # exec: fixed binaries with constrained arguments, not arbitrary shell
    "power_mode": "fixed NOPASSWD wrapper script; argument is a closed mode set",
    "sync_repo": "git fetch/status in LHASSA_AUTO_SYNC_REPOS only; no arbitrary command",
    "git_status_repos": "read-only git status over the configured repo roots",
    "compile_cv": "tectonic over the on-disk CV source; fixed binary, fixed input",
    "system_snapshot": "read-only system introspection commands, fixed set",
    "calendar_sync": "vdirsyncer/khal-style sync of the owner's own calendar",
    # send: the org up-direction, one hop out. Same owner-only destination as
    # the notify entries above; the seam differs, the recipient does not.
    "review_capabilities": "utility-tribunal docket notified to the owner's channels",
    "temple_run": "down-execute-up wrapper; the send is the result delivered to the owner",
    # net: the embedder seam. Writing or indexing a fact may embed it through
    # the endpoint the OWNER configured (LHASSA_EMBEDDER_*); the default
    # embedder is offline and no URL comes from action params.
    "add_fact": "embeds through the owner-configured embedder endpoint; no param-supplied URL",
    "ingest_knowledge": "same embedder endpoint; the files read are the owner's own roots",
    "derive_world_facts": "reads the owner's own configured calendar feed, read-only",
    # fs-del: not a delete of owner data. The curator backs a skill up to
    # tar.gz and MOVES it to .archive/; the only unlink is a temp file being
    # cleaned up after a failed atomic write.
    "curate_skills": "unlink is tmpfile cleanup on an atomic write; skills are archived, never deleted",
}

# Reviewed entries for actions a capability pack registers. They are legitimately
# absent when that pack is not installed (``make test-core``), so an absent one
# is not stale; a registered one is checked like any other entry.
PACK_OWNED_ACTIONS: frozenset[str] = frozenset({"compile_cv"})

# ── Dangerous-surface markers ───────────────────────────────────────────────
_EXEC_MODULES = {"subprocess"}
_EXEC_OS_ATTRS = {"system", "execv", "execvp", "execve", "spawnl", "popen"}
_EXEC_ASYNCIO_ATTRS = {"create_subprocess_exec", "create_subprocess_shell"}
_NET_MODULES = {"httpx", "requests", "urllib"}
# External delivery reaches the owner through TWO seams, not one: the notify
# fan-out, and the org up-direction (``org/deliver.py``, which notifies at the
# end of the hop walk). Marking only the first read nine already-reviewed
# actions as "no longer dangerous"; they had simply moved onto the org seam.
_SEND_MODULE_PREFIXES = ("lhassa.notify", "lhassa.org.deliver")
_SEND_ATTRS = {"send_message", "send_document", "send_photo"}
_FS_DELETE = {"rmtree", "unlink"}
# How many module hops the walk follows inside ``lhassa.*`` before giving up.
_MAX_HOPS = 1


def _module_index(module) -> "tuple[dict[str, str], dict[str, ast.AST]]":
    """Parse a module once → (import alias map, top-level function defs).

    The alias map resolves a local name to the dotted origin it was imported
    from (``_notify`` → ``lhassa.notify.dispatch.notify``); the def map lets
    the walker follow same-module helper calls.
    """
    src = inspect.getsource(module)
    tree = ast.parse(src)
    aliases: dict[str, str] = {}
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs[node.name] = node
    return aliases, defs


def _follow_into_lhassa(origin: str, visited: "set[str]", depth: int) -> "set[str]":
    """Scan a ``lhassa.*`` callee in ITS OWN module, one hop deeper.

    Without this the walk stopped at the module edge, and an action was judged
    safe purely because its danger lived one import away: ``check_health`` ->
    ``health.checks.run_all`` (subprocess + notify), ``compile_cv`` ->
    ``cv.compile.compile_pdf`` (a LaTeX binary), ``git_status_repos`` ->
    ``check_git_status`` (git). Bounded by ``_MAX_HOPS`` and the shared visited
    set; an unimportable module or an unreadable source degrades to no signal,
    never to an error.
    """
    if depth > _MAX_HOPS or not origin.startswith("lhassa."):
        return set()
    module_name, _, fn_name = origin.rpartition(".")
    key = f"{module_name}.{fn_name}"
    if not module_name or key in visited:
        return set()
    visited.add(key)
    try:
        module = importlib.import_module(module_name)
        aliases, defs = _module_index(module)
    except Exception:
        return set()
    node = defs.get(fn_name)
    if node is None:
        return set()
    return _signals_in(node, aliases, defs, visited, depth=depth + 1)


def _signals_in(
    fn_node: ast.AST,
    aliases: "dict[str, str]",
    defs: "dict[str, ast.AST]",
    visited: "set[str]",
    depth: int = 0,
) -> "set[str]":
    """Dangerous-surface signals reachable from ``fn_node`` within its module."""
    found: set[str] = set()
    # This codebase imports LAZILY by convention (inside the function, to keep
    # module import graphs thin), so a module-level alias map alone sees almost
    # none of the real danger. Fold the function's own imports in first.
    aliases = dict(aliases)
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            origin = aliases.get(f.id, "")
            if origin.startswith(_SEND_MODULE_PREFIXES):
                found.add(f"send:{origin}")
            elif origin.split(".")[0] in _EXEC_MODULES:
                found.add(f"exec:{origin}")
            elif origin.split(".")[0] in _NET_MODULES:
                found.add(f"net:{origin}")
            elif f.id in _FS_DELETE:
                found.add(f"fs-del:{f.id}")
            elif f.id in defs and f.id not in visited:
                visited.add(f.id)
                found |= _signals_in(defs[f.id], aliases, defs, visited, depth)
            elif origin.startswith("lhassa."):
                found |= _follow_into_lhassa(origin, visited, depth)
        elif isinstance(f, ast.Attribute):
            root = f.value.id if isinstance(f.value, ast.Name) else None
            root_origin = aliases.get(root or "", root or "")
            base = root_origin.split(".")[0]
            if base in _EXEC_MODULES:
                found.add(f"exec:{root_origin}.{f.attr}")
            elif base == "os" and f.attr in _EXEC_OS_ATTRS:
                found.add(f"exec:os.{f.attr}")
            elif base == "asyncio" and f.attr in _EXEC_ASYNCIO_ATTRS:
                found.add(f"exec:asyncio.{f.attr}")
            elif base in _NET_MODULES:
                found.add(f"net:{root_origin}.{f.attr}")
            elif root_origin.startswith(_SEND_MODULE_PREFIXES) or f.attr in _SEND_ATTRS:
                found.add(f"send:{root_origin}.{f.attr}")
            elif f.attr in _FS_DELETE:
                found.add(f"fs-del:{f.attr}")
            elif root_origin.startswith("lhassa."):
                found |= _follow_into_lhassa(f"{root_origin}.{f.attr}", visited, depth)
    return found


def _dangerous_actions() -> "dict[str, set[str]]":
    """Map each registered action to its dangerous-surface signals (if any)."""
    actionpack.register_all()
    out: dict[str, set[str]] = {}
    module_cache: dict[str, tuple[dict, dict]] = {}
    for name in sorted(actions.ACTIONS):
        fn = actions.ACTIONS[name]
        module = inspect.getmodule(fn)
        if module is None:
            out[name] = {"<no-module>"}
            continue
        key = module.__name__
        if key not in module_cache:
            try:
                module_cache[key] = _module_index(module)
            except (OSError, TypeError, SyntaxError):
                out[name] = {"<no-source>"}
                continue
        aliases, defs = module_cache[key]
        fn_name = getattr(fn, "__name__", "")
        node = defs.get(fn_name)
        if node is None:
            # A lambda / dynamically-built handler we cannot see into: treat
            # as dangerous-by-default so it must be classified explicitly.
            out[name] = {"<opaque-handler>"}
            continue
        signals = _signals_in(node, aliases, defs, visited={fn_name})
        if signals:
            out[name] = signals
    return out


def _classified() -> "set[str]":
    """Action names already gated by a standing approval frontier."""
    allowlist = build_allowlist()
    gated = {n for n, spec in allowlist.items() if spec.requires_approval}
    return gated | set(Config.always_approve_actions)


def test_dangerous_actions_are_classified():
    """A dangerous-surface action must be gated or explicitly reviewed."""
    dangerous = _dangerous_actions()
    classified = _classified()
    unclassified = {
        name: sorted(signals)
        for name, signals in dangerous.items()
        if name not in classified and name not in REVIEWED_LOW_RISK
    }
    if unclassified:
        lines = "\n".join(f"  {n}: {s}" for n, s in sorted(unclassified.items()))
        pytest.fail(
            "Actions touching a dangerous surface without classification:\n"
            f"{lines}\n"
            "Either mark the action requires_approval=True in the allowlist, "
            "add it to LHASSA_ALWAYS_APPROVE_ACTIONS, or (after review) add "
            "an entry with a reason to REVIEWED_LOW_RISK in this test."
        )


def test_reviewed_low_risk_entries_are_live():
    """Every REVIEWED_LOW_RISK entry must name a real, currently-flagged action.

    A stale entry (action renamed/removed, or no longer touching a dangerous
    surface) would silently widen the exemption; prune it instead.
    """
    dangerous = _dangerous_actions()
    stale = [
        name
        for name in REVIEWED_LOW_RISK
        if name not in dangerous
        and not (name in PACK_OWNED_ACTIONS and name not in actions.ACTIONS)
    ]
    if stale:
        pytest.fail(
            "Stale REVIEWED_LOW_RISK entries (no longer registered or no "
            f"longer flagged as dangerous): {sorted(stale)}; remove them."
        )


def test_reviewed_low_risk_reasons_are_written():
    """Each exemption must carry a non-trivial written reason."""
    empty = [n for n, reason in REVIEWED_LOW_RISK.items() if len(reason.strip()) < 10]
    if empty:
        pytest.fail(f"REVIEWED_LOW_RISK entries need a real reason: {sorted(empty)}")


def test_high_risk_gate_is_not_empty():
    """The frontier itself must exist: the gated set can never be empty."""
    classified = _classified()
    if not classified:
        pytest.fail(
            "No action is gated at all (no requires_approval, empty "
            "always_approve_actions); the high-risk frontier disappeared."
        )
    # The two arbitrary-execution doors must never lose their gate.
    for must_gate in ("run_command", "run_script"):
        if must_gate in build_allowlist() and must_gate not in classified:
            pytest.fail(f"{must_gate} lost its requires_approval gate.")


# Sanity check used while authoring REVIEWED_LOW_RISK; kept because it makes
# the heuristic itself testable; if the walker stops seeing through the
# ``from lhassa.notify.dispatch import notify as _notify`` shape, this fails
# before the guard silently goes blind.
def test_heuristic_sees_through_module_aliases():
    src = textwrap.dedent(
        """
        from lhassa.notify.dispatch import notify as _notify

        def handler(params):
            return _do(params)

        def _do(params):
            _notify("hello")
            return "ok"
        """
    )
    tree = ast.parse(src)
    aliases: dict[str, str] = {}
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs[node.name] = node
    signals = _signals_in(defs["handler"], aliases, defs, visited={"handler"})
    if "send:lhassa.notify.dispatch.notify" not in signals:
        pytest.fail(f"walker went blind to aliased notify imports: {signals}")
