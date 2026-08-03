# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runtime import-boundary scanner used by the integrity manifest."""

from __future__ import annotations

import ast
from pathlib import Path

SHELLS = {"cli", "api", "voice", "notify", "org", "dashboard", "actionpack", "flows"}
RULES = (
    ("spine-isolation", {"core"}, SHELLS),
    ("interface-isolation", {"memory", "security", "llm"}, SHELLS),
    ("personal-isolation", {"personal"}, SHELLS),
    (
        "adapter-isolation",
        {"harness", "tools", "agents", "remote", "health", "bench"},
        SHELLS,
    ),
    ("swap-axis-independence", {"harness"}, {"llm"}),
    ("leaf-infra-isolation", {"cv", "calendar"}, SHELLS),
)
EXCEPTIONS = {
    ("tools/monitor.py", "lhassa.voice.notifier"),
    ("tools/handlers/agents.py", "lhassa.voice.notifier"),
    ("health/checks.py", "lhassa.notify.dispatch"),
    ("personal/secretary.py", "lhassa.org"),
    ("personal/secretary.py", "lhassa.org.management"),
    ("personal/secretary.py", "lhassa.org.topology"),
    ("security/approval.py", "lhassa.flows.durable"),
    ("security/approval.py", "lhassa.flows.projection"),
    ("personal/secretary.py", "lhassa.flows.model"),
    ("personal/secretary.py", "lhassa.flows.store"),
}


def _target_subpackage(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "lhassa" else ""


def _modules(tree: ast.AST, package: list[str]) -> list[str]:
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    modules.append(node.module)
            else:
                anchor = package[: len(package) - (node.level - 1)]
                tail = node.module.split(".") if node.module else []
                modules.append(".".join([*anchor, *tail]))
    return modules


def scan(root: Path | None = None) -> list[dict]:
    """Return forbidden import edges from the installed/source package."""
    root = root or Path(__file__).resolve().parents[1]
    violations = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        source_sub = path.relative_to(root).parts[0]
        package = ["lhassa", *path.relative_to(root).parent.parts]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        except (OSError, SyntaxError) as exc:
            violations.append(
                {"file": rel, "module": "", "rule": "parseable", "error": str(exc)}
            )
            continue
        for module in _modules(tree, package):
            if (rel, module) in EXCEPTIONS:
                continue
            target = _target_subpackage(module)
            for rule, sources, forbidden in RULES:
                if source_sub in sources and target in forbidden:
                    violations.append(
                        {"file": rel, "module": module, "rule": rule}
                    )
    return violations
