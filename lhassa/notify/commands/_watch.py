# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watch rules + goals from chat; /watch /watches /unwatch /goal.

Moved verbatim out of the former ``lhassa/notify/commands.py``.
"""

from __future__ import annotations

import re
from typing import Optional

from loguru import logger

from ._registry import register_command
from ._shared import _config, _oops

# "<pattern> -> <accion>": the arrow-syntax watch rule.
_WATCH_ARROW_RE = re.compile(r"^\s*(.+?)\s*->\s*(.+?)\s*$")

# "--every daily|hourly" flag on /goal (defaults to daily when absent).
_GOAL_EVERY_RE = re.compile(r"--every\s+(daily|hourly)\b", re.IGNORECASE)
_GOAL_CADENCE_SECONDS = {"hourly": 3600, "daily": 86400}

# "--turns N" (only meaningful alongside --verify; the loop's max_turns cap).
_GOAL_TURNS_RE = re.compile(r"--turns\s+(\d+)\b", re.IGNORECASE)

# "--verify command:<cmd>" / "--verify regex:<pattern>": a compact shorthand onto
# agents.verify's spec machinery (make_verifier). Applied AFTER --every/--turns are
# stripped so its body can simply run to the end of the (remaining) text; a shell
# command or a regex may itself contain "--", so no lookahead heuristics.
_GOAL_VERIFY_RE = re.compile(r"--verify\s+(command|regex):(.+)$", re.IGNORECASE)

# Default turn cap for a --verify'd goal loop when --turns is not given.
_GOAL_DEFAULT_MAX_TURNS = 10


async def _known_actions(config) -> set:
    """The full set of registered fixed-action names (core actions + actionpack).

    Ensures the actionpack is registered first (idempotent, $0) so a freshly
    started process still recognises actionpack action names in ``/watch``.
    """
    try:
        from lhassa import actionpack

        actionpack.register_all()
    except Exception as exc:
        logger.debug(f"commands: actionpack.register_all failed: {exc}")
    from lhassa.core.actions import ACTIONS

    return set(ACTIONS.keys())


@register_command("watch")
async def _cmd_watch(args: str, *, config=None) -> str:
    """``/watch <pattern> -> <accion>``: add a message-pattern watch rule.

    ``accion`` must be a registered fixed action or the literal ``"judgment"``
    (any action, the brain thinks about it later). ``pattern`` must compile as a
    regex. On success the rule is appended to the same JSON file the Telegram
    poller reads (:mod:`lhassa.notify.telegram_inbound`); on any validation
    failure NOTHING is written and a clear reason is returned.
    """
    m = _WATCH_ARROW_RE.match(args or "")
    if not m:
        return "Usage: /watch <pattern> -> <accion>"
    pattern_src, accion = m.group(1).strip(), m.group(2).strip()
    if not pattern_src or not accion:
        return "Usage: /watch <pattern> -> <accion>"

    try:
        re.compile(pattern_src)
    except re.error as exc:
        return f"Invalid regex {pattern_src!r}: {exc}"

    cfg = _config(config)
    if accion != "judgment":
        known = await _known_actions(cfg)
        if accion not in known:
            return (
                f"Unknown accion {accion!r}. Must be a registered action or "
                "'judgment'."
            )

    try:
        from lhassa.notify import telegram_inbound as _ti

        rules = _ti._load_raw_watch_rules(cfg)
        rules.append({"pattern": pattern_src, "accion": accion, "params": {}})
        if not _ti.save_watch_rules(cfg, rules):
            return _oops("save the watch rule (is watch_rules_file configured?)")
        return f"Watch added (#{len(rules) - 1}): /{pattern_src}/ -> {accion}"
    except Exception as exc:
        logger.warning(f"commands: /watch failed: {exc}")
        return _oops("add that watch rule")


@register_command("watches")
async def _cmd_watches(args: str, *, config=None) -> str:
    """``/watches``: list configured watch rules with their index."""
    try:
        cfg = _config(config)
        from lhassa.notify import telegram_inbound as _ti

        rules = _ti._load_raw_watch_rules(cfg)
        if not rules:
            return "No watch rules configured."
        lines = ["Watch rules:"]
        for i, rule in enumerate(rules):
            lines.append(f"{i}: /{rule.get('pattern', '')}/ -> {rule.get('accion', '')}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning(f"commands: /watches failed: {exc}")
        return _oops("list the watch rules")


@register_command("unwatch")
async def _cmd_unwatch(args: str, *, config=None) -> str:
    """``/unwatch <index>``: remove a watch rule by its ``/watches`` index."""
    idx_raw = (args or "").strip()
    if not idx_raw.lstrip("-").isdigit():
        return "Usage: /unwatch <index>; see /watches for indices."
    idx = int(idx_raw)

    try:
        cfg = _config(config)
        from lhassa.notify import telegram_inbound as _ti

        rules = _ti._load_raw_watch_rules(cfg)
        if idx < 0 or idx >= len(rules):
            return f"No watch rule at index {idx}. See /watches."
        removed = rules.pop(idx)
        if not _ti.save_watch_rules(cfg, rules):
            return _oops("save after removing that rule")
        return (
            f"Removed watch rule {idx}: "
            f"/{removed.get('pattern', '')}/ -> {removed.get('accion', '')}"
        )
    except Exception as exc:
        logger.warning(f"commands: /unwatch failed: {exc}")
        return _oops("remove that watch rule")


_GOAL_USAGE = (
    "Usage: /goal <text> [--every daily|hourly] "
    "[--verify command:<cmd>|regex:<pattern>] [--turns N]"
)


@register_command("goal")
async def _cmd_goal(args: str, *, config=None) -> str:
    """``/goal <text> [--every ...] [--verify <spec>] [--turns N]``: schedule a goal.

    Mints a recurring ``judgment=True`` task (``core.dispatch.mint_task``) with
    HUMANO-origin trust and actor ``"telegram"``. The task is picked up and REASONED
    about by the brain on its own schedule (a real, later token spend); minting it
    here is $0; only that later think costs anything.

    With ``--verify`` (a compact shorthand onto :mod:`lhassa.agents.verify`'s spec
    machinery; ``command:<shell cmd>`` or ``regex:<pattern>``), this ALSO creates a
    bounded ``loops`` row (kind="closed", origin="telegram-goal", max_turns from
    ``--turns`` or :data:`_GOAL_DEFAULT_MAX_TURNS`) and stamps its id into the
    minted task's ``params["_loop_id"]``: ``core.step._judge`` reads that seam each
    turn to verify-and-terminate (see there for the feedback-threading detail).
    A bad ``--verify`` spec parses defensively: nothing is minted, an honest usage
    reply is returned. Without ``--verify`` this is byte-identical to before.
    """
    text = args or ""
    cadence = "daily"
    m = _GOAL_EVERY_RE.search(text)
    if m:
        cadence = m.group(1).lower()
        text = _GOAL_EVERY_RE.sub("", text).strip()

    turns: Optional[int] = None
    tm = _GOAL_TURNS_RE.search(text)
    if tm:
        turns = int(tm.group(1))
        text = _GOAL_TURNS_RE.sub("", text).strip()
        if turns <= 0:
            return _GOAL_USAGE

    verify_spec: Optional[dict] = None
    vm = _GOAL_VERIFY_RE.search(text)
    if vm:
        vtype = vm.group(1).lower()
        vbody = vm.group(2).strip()
        text = _GOAL_VERIFY_RE.sub("", text).strip()
        if not vbody:
            return _GOAL_USAGE
        verify_spec = (
            {"type": "command", "cmd": vbody}
            if vtype == "command"
            else {"type": "regex", "pattern": vbody}
        )
        try:
            from lhassa.agents import verify as _verify

            _verify.make_verifier(verify_spec)
        except Exception as exc:
            return f"Bad --verify spec: {exc}"

    text = text.strip()
    if not text:
        return _GOAL_USAGE

    try:
        from datetime import datetime, timezone

        from lhassa.core import db as _db
        from lhassa.core.dispatch import mint_task
        from lhassa.security.trust import Origin, TrustLevel

        cfg = _config(config)
        instance_id = getattr(cfg, "instance_id", None) or "inst_personal"
        await _db.init_db()

        origin = Origin(trust=TrustLevel.HUMANO, actor_id="telegram")
        due_at = datetime.now(timezone.utc).isoformat()
        interval = _GOAL_CADENCE_SECONDS[cadence]

        params: dict = {}
        max_turns = turns or _GOAL_DEFAULT_MAX_TURNS
        if verify_spec is not None:
            loop_id = await _db.insert_loop({
                "id": _db._new_id("loop"),
                "instance_id": instance_id,
                "goal": text,
                "kind": "closed",
                "origin": "telegram-goal",
                "status": "plan",
                "turn": 0,
                "max_turns": max_turns,
                "verifier_spec": verify_spec,
                "created_at": _db._now(),
                "updated_at": _db._now(),
            })
            params["_loop_id"] = loop_id

        task_id, estado = await mint_task(
            instance_id,
            "goal",
            objetivo=text,
            params=params,
            origin=origin,
            due_at=due_at,
            recurrence=str(interval),
            judgment=True,
        )
    except Exception as exc:
        logger.warning(f"commands: /goal failed: {exc}")
        return _oops("schedule that goal")

    if not task_id:
        return f"Could not schedule goal: {estado}"
    suffix = (
        f" [verify: {verify_spec['type']}, max {max_turns} turns]"
        if verify_spec is not None
        else ""
    )
    return f"Goal scheduled ({cadence}): {text} [task {task_id}]{suffix}"
