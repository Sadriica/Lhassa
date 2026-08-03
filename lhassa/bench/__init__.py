# SPDX-License-Identifier: AGPL-3.0-or-later
"""LhassaBench — a deterministic, $0-scoring eval harness (adapter layer).

Runs the fixture set (:mod:`lhassa.bench.loader`) against named subjects
(providers today) via :func:`lhassa.bench.runner.run_bench`, scoring every
completion with the SAME objective verifier the rest of Lhassa trusts
(:mod:`lhassa.agents.verify`) — never an LLM judge. Results land in the
``bench_runs`` table (:mod:`lhassa.core.db`); :func:`lhassa.bench.runner.summarize`
renders a compact per-subject table for the CLI/dashboard/digest.
"""

from __future__ import annotations

from lhassa.bench.loader import fixture_ids, load_fixtures
from lhassa.bench.runner import run_bench, summarize

__all__ = ["fixture_ids", "load_fixtures", "run_bench", "summarize"]
