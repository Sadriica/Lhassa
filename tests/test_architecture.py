"""Architecture / layer-boundary guard (slice C of the modularity hardening pass).

Lhassa must stay *elastic* and *highly modular*: clean layering that does NOT
erode over time. This test encodes the architecture's layering rules and FAILS
if a future change crosses a forbidden boundary. It has **no third-party
dependency** (no import-linter): it parses every ``lhassa/**/*.py`` with the
standard-library :mod:`ast` and inspects its ``import`` / ``from ... import``
statements.

The rules below were *measured from the current tree*, not assumed — every
forbidden edge is one that does not exist today, so this test passes now and
only trips when a NEW violation is introduced. See ``doc/architecture-layers.md``
for the prose description of the layers and the allowed-exceptions list.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# ── Layer model ──────────────────────────────────────────────────────────────
# Subpackages of ``lhassa`` grouped into layers (outer shells are the only ones
# allowed to depend "down" freely; nothing below a shell may depend "up" on it).
#
#   shells        : cli, api, voice, notify, org, dashboard  (user/world surfaces)
#   spine         : core                                      (the do-and-sleep loop)
#   interfaces    : memory, security, llm                     (mid layers / contracts)
#   adapters      : harness, tools, agents, remote, health    (the rentable "hands")
#   cv seam       : cv                                         (leaf file-I/O used by flows)
#   leaf infra    : registry.py, plugins.py                   (keystone — depend on nothing)
#
# The forbidden directions encode "inner layers must not reach out to shells",
# the keystone-leaf rule, and the model-vs-harness swap-axis independence.

SHELLS = {"cli", "api", "voice", "notify", "org", "dashboard", "actionpack"}


@dataclass(frozen=True)
class Rule:
    """A forbidden-edge rule: any module in ``sources`` importing any module
    whose top-level subpackage is in ``forbidden`` is a violation."""

    name: str
    sources: frozenset[str]
    forbidden: frozenset[str]


RULES: tuple[Rule, ...] = (
    # The spine never reaches out to a shell — the loop is driven *by* the
    # shells (heartbeat/api/cli), it must not know them.
    Rule("spine-isolation", frozenset({"core"}), frozenset(SHELLS)),
    # The mid-layer contracts (memory/security/llm) are reusable by any shell;
    # they must not depend on a shell themselves.
    Rule(
        "interface-isolation",
        frozenset({"memory", "security", "llm"}),
        frozenset(SHELLS),
    ),
    # The "hands" (adapters) must not depend on the UI/messaging shells. ``voice``
    # is included; the single legitimate desktop-notifier edge is whitelisted in
    # ALLOWED_EXCEPTIONS below so this rule still blocks NEW voice coupling.
    Rule(
        "adapter-isolation",
        frozenset({"harness", "tools", "agents", "remote", "health"}),
        frozenset(SHELLS),
    ),
    # Swap-axis independence: the harness adapter (agentic body) must not import
    # the model-provider interface (think-directly axis). The two axes swap
    # independently. The reverse edge (llm/router.py choosing a harness) is the
    # intentional bridge seam and is therefore NOT forbidden.
    Rule("swap-axis-independence", frozenset({"harness"}), frozenset({"llm"})),
    # The CV file-I/O seam is leaf infrastructure used BY the flow engine (the "build
    # on a base CV, export a tailored .tex" foundation). Like the other inner layers
    # it must never reach out to a shell — it depends only on the standard library.
    Rule("cv-infra-isolation", frozenset({"cv"}), frozenset(SHELLS)),
)

# Keystone leaf-infrastructure files: pure generic infrastructure that must not
# import ANY ``lhassa.*`` feature module (they sit at the very bottom).
KEYSTONE_FILES = ("registry.py", "plugins.py")

# Documented allowed exceptions — precise (source-file-suffix, imported-module)
# pairs that are intentionally permitted despite tripping a rule above.
#   tools/{monitor.py, handlers/agents.py} -> lhassa.voice.notifier:
#     ``voice/notifier.py`` is the cross-platform DESKTOP notification router
#     (notify-send / osascript / BurntToast), a shared utility, not the voice
#     conversation shell. Tools legitimately use it to surface alerts (the
#     delegate-to-agent handler notifies when a background agent finishes).
#     Keying the exception on the exact module keeps the rest of the ``voice``
#     package (e.g. ``voice.pipeline``, ``voice.brain_bridge``) forbidden to
#     adapters.
#   plugins.py -> lhassa.config:
#     ``config`` is itself leaf infrastructure (pure configuration, not a feature
#     module), so the plugin loader reading ``Config`` does not break the keystone
#     rule. Whitelisted precisely so any *feature* import from a keystone still trips.
ALLOWED_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("tools/monitor.py", "lhassa.voice.notifier"),
        ("tools/handlers/agents.py", "lhassa.voice.notifier"),
        ("plugins.py", "lhassa.config"),
    }
)


# ── Import extraction (stdlib ast only) ──────────────────────────────────────
@dataclass
class ImportRecord:
    src_rel: str  # e.g. "core/step.py" (relative to the lhassa package root)
    src_sub: str  # top-level subpackage of the source ("core", or "(root)")
    module: str  # fully-resolved dotted module being imported
    tgt_sub: str  # top-level lhassa subpackage of the target, or "(non-lhassa)"


def _subpackage(parts: list[str]) -> str:
    """Top-level subpackage name for a dotted-path split as ``["lhassa", ...]``."""
    if not parts or parts[0] != "lhassa":
        return "(non-lhassa)"
    rest = parts[1:]
    return rest[0] if rest else "(root)"


def _package_parts(lhassa_root: Path, py_file: Path) -> list[str]:
    """Dotted parts of the *package* that contains ``py_file`` (the anchor used
    to resolve relative imports)."""
    rel = py_file.relative_to(lhassa_root)
    # The package is the directory containing the file.
    return ["lhassa", *rel.parent.parts]


def iter_import_records(lhassa_root: Path, py_file: Path) -> Iterator[ImportRecord]:
    src_rel = py_file.relative_to(lhassa_root).as_posix()
    src_sub = _subpackage(["lhassa", *py_file.relative_to(lhassa_root).parts])
    pkg_parts = _package_parts(lhassa_root, py_file)
    tree = ast.parse(py_file.read_text(encoding="utf-8"), str(py_file))
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    modules.append(node.module)
            else:
                # Relative import: strip (level-1) trailing parts from the package.
                anchor = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                tail = node.module.split(".") if node.module else []
                modules.append(".".join(anchor + tail))
        for mod in modules:
            parts = mod.split(".")
            yield ImportRecord(
                src_rel=src_rel,
                src_sub=src_sub,
                module=mod,
                tgt_sub=_subpackage(parts),
            )


def _lhassa_root() -> Path:
    root = Path(__file__).resolve().parent.parent / "lhassa"
    assert root.is_dir(), f"could not locate lhassa package at {root}"
    return root


def all_import_records() -> list[ImportRecord]:
    root = _lhassa_root()
    records: list[ImportRecord] = []
    for py_file in sorted(root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        records.extend(iter_import_records(root, py_file))
    return records


# ── The checker (pure, unit-testable in isolation below) ─────────────────────
@dataclass
class Violation:
    src_rel: str
    module: str
    rule: str

    def __str__(self) -> str:  # clear, actionable failure line
        return (
            f"lhassa/{self.src_rel}  imports  {self.module}  "
            f"→ violates rule '{self.rule}'"
        )


def find_violations(
    records: list[ImportRecord],
    rules: tuple[Rule, ...] = RULES,
    exceptions: frozenset[tuple[str, str]] = ALLOWED_EXCEPTIONS,
    keystone_files: tuple[str, ...] = KEYSTONE_FILES,
) -> list[Violation]:
    """Return every forbidden edge, minus the documented allowed exceptions."""
    out: list[Violation] = []
    for rec in records:
        if (rec.src_rel, rec.module) in exceptions:
            continue
        # Keystone leaf rule: registry.py / plugins.py import no lhassa feature.
        if rec.src_rel in keystone_files and rec.tgt_sub != "(non-lhassa)":
            out.append(Violation(rec.src_rel, rec.module, "keystone-leaf"))
            continue
        for rule in rules:
            if rec.src_sub in rule.sources and rec.tgt_sub in rule.forbidden:
                out.append(Violation(rec.src_rel, rec.module, rule.name))
    return out


# ── The guard test ───────────────────────────────────────────────────────────
def test_no_forbidden_layer_crossings() -> None:
    """The whole point: the current tree obeys the layering, and any NEW
    forbidden edge fails this test with a message naming file → import → rule."""
    violations = find_violations(all_import_records())
    assert not violations, "Layer-boundary violations found:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_rules_reference_real_subpackages() -> None:
    """Guard against typos: every subpackage named in a rule must exist on disk
    (so a rule can never silently become a no-op)."""
    root = _lhassa_root()
    existing = {p.name for p in root.iterdir() if p.is_dir() and p.name != "__pycache__"}
    existing.add("dashboard")  # JS shell, lives outside the python package
    named = {sub for rule in RULES for sub in (rule.sources | rule.forbidden)}
    missing = named - existing
    assert not missing, f"rule references non-existent subpackage(s): {sorted(missing)}"


# ── Self-test: prove the checker WOULD catch a violation ─────────────────────
def test_checker_flags_a_synthetic_forbidden_edge() -> None:
    """Feed the checker a synthetic forbidden edge (core → cli) and confirm it is
    flagged. This proves the guard has teeth and is not vacuously passing."""
    synthetic = [
        ImportRecord(
            src_rel="core/step.py",
            src_sub="core",
            module="lhassa.cli.app",
            tgt_sub="cli",
        )
    ]
    violations = find_violations(synthetic)
    assert len(violations) == 1
    assert violations[0].rule == "spine-isolation"
    assert "lhassa.cli.app" in str(violations[0])


def test_checker_honours_allowed_exceptions() -> None:
    """A whitelisted edge is NOT flagged, but the same source importing a
    DIFFERENT module in the forbidden package still is."""
    allowed = ImportRecord(
        src_rel="tools/monitor.py",
        src_sub="tools",
        module="lhassa.voice.notifier",
        tgt_sub="voice",
    )
    not_allowed = ImportRecord(
        src_rel="tools/monitor.py",
        src_sub="tools",
        module="lhassa.voice.pipeline",
        tgt_sub="voice",
    )
    assert find_violations([allowed]) == []
    flagged = find_violations([not_allowed])
    assert len(flagged) == 1
    assert flagged[0].rule == "adapter-isolation"


def test_keystone_files_import_no_feature_module() -> None:
    """registry.py / plugins.py are leaf infrastructure: a synthetic feature
    import from either is flagged as 'keystone-leaf'."""
    synthetic = [
        ImportRecord(
            src_rel="registry.py",
            src_sub="(root)",
            module="lhassa.memory.facts",
            tgt_sub="memory",
        )
    ]
    flagged = find_violations(synthetic)
    assert len(flagged) == 1 and flagged[0].rule == "keystone-leaf"
