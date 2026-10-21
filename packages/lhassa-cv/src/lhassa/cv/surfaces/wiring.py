# SPDX-License-Identifier: AGPL-3.0-or-later
"""The cv pack's shell-touching registrations (called once by :mod:`lhassa.cv.pack`).

Thin wrappers only: each surface module (commands, Telegram hook, seed) is imported
inside its wrapper, on first use. :func:`register` is idempotent.
"""

from __future__ import annotations


# ── action ───────────────────────────────────────────────────────────────────
def _register_actions() -> None:
    from lhassa.core.actions import _REGISTRY, register_action
    from lhassa.cv.actions import compile_cv

    from lhassa.security.allowlist import WILDCARD, ActionSpec, register_action_spec
    from lhassa.security.trust import TrustLevel

    if _REGISTRY.get_or_none("compile_cv") is None:
        register_action("compile_cv", compile_cv)
    # Compiles the on-disk .tex with tectonic; local file work, no send.
    register_action_spec(
        "compile_cv",
        ActionSpec(objetivos=WILDCARD, min_trust=TrustLevel.INTERNO, requires_approval=False),
    )


# ── chat commands ─────────────────────────────────────────────────────────────
def _command(name: str):
    async def _handler(args: str, *, config=None) -> str:
        from lhassa.cv.surfaces import commands

        return await commands.HANDLERS[name](args, config=config)

    _handler.__name__ = f"cmd_{name}"
    return _handler


_COMMAND_HELP = {
    "cvnewbase": "Arm a base-CV replacement.",
    "cvbase": "Show the base CV on file.",
}


def _register_commands() -> None:
    from lhassa.notify.commands import COMMANDS, describe_command, register_command

    for name, help_line in _COMMAND_HELP.items():
        if COMMANDS.get_or_none(name) is None:
            register_command(name, _command(name))
        describe_command(name, help_line, group="CV")


# ── Telegram inbound hook ──────────────────────────────────────────────────────
async def _on_text(text: str, *, config, chat_id: str = "") -> bool:
    from lhassa.cv.surfaces import telegram

    return await telegram.on_text(text, config=config, chat_id=chat_id)


async def _on_document(document: dict, caption, *, config, chat_id: str = "") -> bool:
    from lhassa.cv.surfaces import telegram

    return await telegram.on_document(document, caption, config=config, chat_id=chat_id)


def _reset() -> None:
    import sys

    # Only reset state that exists; never import the surface just to clear it.
    module = sys.modules.get("lhassa.cv.surfaces.telegram")
    if module is not None:
        module.reset()


def _register_inbound_hook() -> None:
    from lhassa.notify.inbound_hooks import register_inbound_hook

    register_inbound_hook("cv", on_text=_on_text, on_document=_on_document, reset=_reset)


# ── work-flow entries ───────────────────────────────────────────────────────────
def _register_flow_entries() -> None:
    from lhassa.cv.surfaces import flows

    flows.register()


# ── seed ──────────────────────────────────────────────────────────────────────
def _apply_seed(config=None) -> dict:
    from lhassa.cv.surfaces import seed

    return seed.apply(config)


def _register_seed() -> None:
    from lhassa.seeds import SEEDS, register_seed

    if "cv" not in SEEDS:
        register_seed(
            "cv",
            "Recreate the 'Impossible-to-reject CV' pipeline: the interns department, "
            "its 6 worker brains, the monitor role, the canonical CV work-flow, and a "
            "placeholder base CV (never overwriting a real one).",
        )(_apply_seed)


def register() -> None:
    """Register the action, commands, inbound hook, flow entries and seed."""
    _register_actions()
    _register_commands()
    _register_inbound_hook()
    _register_flow_entries()
    _register_seed()
