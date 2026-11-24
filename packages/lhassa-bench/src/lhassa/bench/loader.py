# SPDX-License-Identifier: AGPL-3.0-or-later
"""LhassaBench fixture loading. YAML on disk, validated eagerly.

Each fixture is one self-contained YAML file under ``lhassa/bench/fixtures/``:
``{id, title, prompt, subject_kind, verify, tags}``. ``verify`` is a spec dict
shaped EXACTLY like what :func:`lhassa.agents.verify.make_verifier` accepts
(``{"type": "regex"|"command"|"metric"|..., ...}``); the one exception is a
``metric`` spec's ``metric_fn`` key, a NAME from :data:`METRIC_FNS` resolved to
a real callable by :func:`resolve_verify_spec` before it reaches
``make_verifier`` (a bare Python callable cannot round-trip through YAML).

No network, no repo state: every fixture is scored on the completion TEXT
alone, so a fixture is fully reproducible offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import yaml

# Importing registers the task verifiers (label, facts, json_fields) in the
# shared verifier registry, so any fixture spec can name them.
from lhassa.bench import verifiers as _task_verifiers  # noqa: F401

FIXTURES_DIR = Path(__file__).parent / "fixtures"
# The task suite (classify, summarize, route, policy, extract): one
# sub-directory per task. Kept apart from the general fixtures so the default
# ``lhassa bench run`` is unchanged; select it with ``--suite tasks``.
TASKS_DIR = FIXTURES_DIR / "tasks"

REQUIRED_KEYS = ("id", "title", "prompt", "subject_kind", "verify")


# ── named metric functions (the only way a YAML fixture can reach a callable) ─

def _word_count(text: str, _context=None) -> int:
    return len((text or "").split())


def _char_count(text: str, _context=None) -> int:
    return len(text or "")


def _line_count(text: str, _context=None) -> int:
    return len((text or "").splitlines())


METRIC_FNS: dict[str, Callable[[str, Optional[dict]], float]] = {
    "word_count": _word_count,
    "char_count": _char_count,
    "line_count": _line_count,
}


class FixtureError(ValueError):
    """A fixture file is missing a required key or names an unknown metric_fn."""


def resolve_verify_spec(spec: dict) -> dict:
    """Return a ``make_verifier``-ready copy of ``spec``.

    A ``{"type": "metric", "metric_fn": "word_count", ...}`` spec has its
    ``metric_fn`` name swapped for the real callable under the key
    ``fn_or_value`` (what :class:`~lhassa.agents.verify.MetricVerifier`
    expects); every other spec shape passes through unchanged.
    """
    spec = dict(spec or {})
    if spec.get("type") == "metric" and "metric_fn" in spec:
        name = spec.pop("metric_fn")
        fn = METRIC_FNS.get(name)
        if fn is None:
            raise FixtureError(f"unknown metric_fn: {name!r}")
        spec["fn_or_value"] = fn
    return spec


def _validate(fixture: dict, *, source: str) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in fixture]
    if missing:
        raise FixtureError(f"{source}: missing required key(s): {missing}")
    if not isinstance(fixture["verify"], dict) or "type" not in fixture["verify"]:
        raise FixtureError(f"{source}: 'verify' must be a dict with a 'type'")


def load_fixture_file(path: Path) -> dict:
    """Load and validate one fixture YAML file. Raises :class:`FixtureError`."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    _validate(data, source=path.name)
    return data


def load_fixtures(ids: Optional[list[str]] = None, *, directory: Optional[Path] = None) -> list[dict]:
    """Load every fixture under ``directory`` (default the bundled set).

    With ``ids`` given, only fixtures whose ``id`` is in that list are
    returned (order follows ``ids``; an unknown id is silently skipped;
    callers that care can diff ``[f["id"] for f in result]`` against ``ids``).
    Raises :class:`FixtureError` on a malformed fixture file; fixtures are
    developer-authored content, not untrusted input, so loud failure here is
    correct (unlike the best-effort runner below).
    """
    directory = directory or FIXTURES_DIR
    all_fixtures = [
        load_fixture_file(p) for p in sorted(directory.glob("*.yaml"))
    ]
    if ids is None:
        return all_fixtures
    by_id = {f["id"]: f for f in all_fixtures}
    return [by_id[i] for i in ids if i in by_id]


def load_task_fixtures(task: Optional[str] = None) -> list[dict]:
    """Load the task suite (optionally one task), each with a ``task`` key set
    from its directory name."""
    out: list[dict] = []
    for sub in sorted(p for p in TASKS_DIR.iterdir() if p.is_dir()):
        if task and sub.name != task:
            continue
        for fixture in load_fixtures(directory=sub):
            fixture.setdefault("task", sub.name)
            out.append(fixture)
    return out


def fixture_ids(*, directory: Optional[Path] = None) -> list[str]:
    """Convenience: every fixture id available on disk, in load order."""
    return [f["id"] for f in load_fixtures(directory=directory)]
