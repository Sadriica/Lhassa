# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Lhassa voice-tool handlers, grouped by domain family.

Each module holds the ``handle_<name>`` coroutines for one family of tools
(shell, files, web, agents, schedule, outlook, github, vision, monitor, jobs).
They share :func:`lhassa.tools.handlers._shared._bump_tool` and are composed
back together by ``lhassa.tools.registry`` (the public seam). No handler module
imports another handler module.
"""
