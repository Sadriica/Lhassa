# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``lhassa tui`` screens.

Eight tabs — overview, projects, runs, approvals, flows, skills, memory, org —
plus the ``help`` overlay. Every table-driven screen subclasses
``base.TableScreen`` (``/`` filter + live re-poll + degrade-on-read-failure).
"""

from __future__ import annotations
