# SPDX-License-Identifier: AGPL-3.0-or-later
"""The research pack's registrations into shell registries.

Kept out of :mod:`lhassa.research.pack` so the pack's inner layer never
imports a shell: the chat ``COMMANDS`` registry and its ``/help`` line live in
``lhassa.notify``, the integrity requirements in ``lhassa.org.integrity``.
Idempotent; every step tolerates having run before.
"""

from __future__ import annotations

#: What the integrity watcher must find wired while this pack is installed.
REQUIRED_ACTIONS = ("research_intake", "draft_research_note")
REQUIRED_VERIFIERS = ("research_corpus", "idea_assessment")


def _canonical_flows(config) -> dict:
    from lhassa.research.surfaces.seed import canonical_flows

    return canonical_flows(config)


def register_chat_command() -> None:
    from lhassa.notify.commands._registry import COMMANDS, register_command
    from lhassa.research.surfaces.commands import DESCRIPTION, _cmd_idea

    if "idea" not in COMMANDS:
        register_command("idea", _cmd_idea)
    from lhassa.notify.commands._core import DESCRIPTIONS

    DESCRIPTIONS.setdefault("idea", DESCRIPTION)


def register_integrity() -> None:
    from lhassa.org.integrity import register_pack_requirements

    register_pack_requirements(
        "research",
        actions=REQUIRED_ACTIONS,
        verifiers=REQUIRED_VERIFIERS,
        canonical_flows=_canonical_flows,
        remedy="lhassa seed apply research",
    )


def register() -> None:
    register_chat_command()
    register_integrity()
