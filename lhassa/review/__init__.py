# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa review``: a deterministic, no-LLM pre-flight review.

One rule engine, three doors: a git hook (``commit-msg``, ``pre-push``), a
Claude Code ``PreToolUse`` hook, and the ``lhassa review`` CLI. The engine is
stdlib-only (plus ``lhassa.privacy`` for the secret shapes) so a hook starts
fast and never depends on the full runtime. It never imports a shell.
"""

from __future__ import annotations

from lhassa.review.rules import Finding, ReviewConfig, load_config

__all__ = ["Finding", "ReviewConfig", "load_config"]
