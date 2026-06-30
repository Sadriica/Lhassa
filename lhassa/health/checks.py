"""
LLM-free PC-state checks.

Every check is a small, deterministic function that returns a result dict::

    {"name": str, "level": str, "ok": bool, "summary": str, "detail": dict|str}

where ``level`` is one of ``"info"``, ``"notice"``, ``"warn"``, ``"alert"``.

Anything that touches the outside world (systemctl, git, notify-send) is split
into a PURE parser/helper plus a thin collector that shells out, so the logic is
testable without those binaries present. Nothing here imports or calls an LLM,
provider, or cloud service — pure standard library + subprocess.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from loguru import logger

from lhassa.persona import format_alert

# Severity ordering — used to pick the "worst" level across checks.
_LEVELS = ("info", "notice", "warn", "alert")


def _level_rank(level: str) -> int:
    """Rank a severity level; unknown levels sort as the lowest (info)."""
    try:
        return _LEVELS.index(level)
    except ValueError:
        return 0


def worst_level(levels: list[str]) -> str:
    """Return the most severe level in ``levels`` (``"info"`` if empty)."""
    if not levels:
        return "info"
    return max(levels, key=_level_rank)


def _result(name: str, level: str, summary: str, detail: object) -> dict:
    """Build a normalised check result dict."""
    return {
        "name": name,
        "level": level,
        "ok": level in ("info", "notice"),
        "summary": summary,
        "detail": detail,
    }


# ── disk ────────────────────────────────────────────────────────────────────


def disk_level(percent: float, threshold: float = 90.0, critical: float = 97.0) -> str:
    """Map a disk-usage percentage to a severity level (pure threshold logic)."""
    if percent >= critical:
        return "alert"
    if percent >= threshold:
        return "warn"
    return "info"


def _default_mounts() -> list[str]:
    """Candidate mount points to check; only existing ones are kept."""
    candidates = ["/", "/home", str(Path.home())]
    seen: list[str] = []
    for mount in candidates:
        if mount not in seen and Path(mount).exists():
            seen.append(mount)
    return seen


def check_disk(
    mounts: list[str] | None = None,
    threshold: float = 90.0,
    critical: float = 97.0,
) -> dict:
    """Report disk usage per mount via :func:`shutil.disk_usage`.

    Warns once any mount crosses ``threshold`` percent used; alerts at
    ``critical``. Degrades gracefully if a mount cannot be read.
    """
    mounts = mounts if mounts is not None else _default_mounts()
    per_mount: dict[str, dict] = {}
    levels: list[str] = []

    for mount in mounts:
        try:
            usage = shutil.disk_usage(mount)
        except OSError as exc:
            logger.debug("disk usage unavailable for {}: {}", mount, exc)
            continue
        percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
        level = disk_level(percent, threshold, critical)
        levels.append(level)
        per_mount[mount] = {
            "percent": round(percent, 1),
            "free_gb": round(usage.free / 1024**3, 1),
            "total_gb": round(usage.total / 1024**3, 1),
            "level": level,
        }

    level = worst_level(levels)
    hot = [
        f"{m} {info['percent']:.0f}%"
        for m, info in per_mount.items()
        if info["level"] in ("warn", "alert")
    ]
    if not per_mount:
        summary = "No mounts could be read."
    elif hot:
        summary = "Disk filling up: " + ", ".join(hot) + "."
    else:
        summary = f"Disk usage healthy across {len(per_mount)} mount(s)."
    return _result("disk", level, summary, per_mount)


# ── failed_units ─────────────────────────────────────────────────────────────


def parse_failed_units(text: str) -> list[str]:
    """Parse ``systemctl --failed --no-legend`` output into a list of unit names.

    Each non-empty line looks like::

        nginx.service loaded failed failed A high performance web server

    The unit name is the first whitespace-separated token. Bullet markers that
    ``systemctl`` sometimes prefixes (``●``) are ignored.
    """
    units: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("●").strip()
        if not line:
            continue
        unit = line.split()[0]
        if unit:
            units.append(unit)
    return units


def check_failed_units() -> dict:
    """Count failed systemd units (thin collector around the pure parser)."""
    if not shutil.which("systemctl"):
        return _result("failed_units", "info", "systemctl not available.", [])
    try:
        proc = subprocess.run(
            ["systemctl", "--failed", "--no-legend", "--plain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("systemctl --failed failed: {}", exc)
        return _result("failed_units", "info", "Could not query systemd.", [])

    units = parse_failed_units(proc.stdout)
    if not units:
        return _result("failed_units", "info", "No failed systemd units.", [])
    level = "alert" if len(units) >= 3 else "warn"
    summary = f"{len(units)} failed systemd unit(s): " + ", ".join(units[:5])
    return _result("failed_units", level, summary, units)


# ── git_status ───────────────────────────────────────────────────────────────

_AHEAD_RE = re.compile(r"ahead (\d+)")
_BEHIND_RE = re.compile(r"behind (\d+)")


def parse_git_status(text: str) -> dict:
    """Interpret ``git status --branch --porcelain`` output (pure function).

    Returns ``{"branch", "uncommitted", "ahead", "behind"}``. The first line
    (``## branch...remote [ahead N, behind M]``) carries branch/tracking info;
    every other non-empty line is one changed/untracked path.
    """
    branch = ""
    ahead = 0
    behind = 0
    uncommitted = 0

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("##"):
            header = line[2:].strip()
            ahead_match = _AHEAD_RE.search(header)
            behind_match = _BEHIND_RE.search(header)
            if ahead_match:
                ahead = int(ahead_match.group(1))
            if behind_match:
                behind = int(behind_match.group(1))
            # Branch name is the token before "..." (tracking) or any whitespace.
            name = header.split("...", 1)[0].split()[0] if header.split() else ""
            branch = name
        else:
            uncommitted += 1

    return {
        "branch": branch,
        "uncommitted": uncommitted,
        "ahead": ahead,
        "behind": behind,
    }


def git_repos_under(base: Path) -> list[Path]:
    """Find git repos: ``base`` itself if it is one, else its direct children."""
    repos: list[Path] = []
    if (base / ".git").exists():
        repos.append(base)
        return repos
    try:
        children = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return repos
    for child in children:
        if (child / ".git").exists():
            repos.append(child)
    return repos


def _default_project_dirs() -> list[Path]:
    """Default roots to scan for repos; non-existent ones are skipped."""
    home = Path.home()
    return [d for d in (home / "Code", home / "per", home / "Vento") if d.exists()]


def check_git_status(project_dirs: list[Path] | None = None) -> dict:
    """Report uncommitted/unpushed work across project repos (thin collector)."""
    if not shutil.which("git"):
        return _result("git_status", "info", "git not available.", {})

    dirs = project_dirs if project_dirs is not None else _default_project_dirs()
    repos_info: dict[str, dict] = {}
    dirty = 0
    unpushed = 0

    for base in dirs:
        for repo in git_repos_under(Path(base)):
            try:
                proc = subprocess.run(
                    ["git", "-C", str(repo), "status", "--branch", "--porcelain"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("git status failed for {}: {}", repo, exc)
                continue
            if proc.returncode != 0:
                continue
            info = parse_git_status(proc.stdout)
            if info["uncommitted"] or info["ahead"] or info["behind"]:
                repos_info[str(repo)] = info
                if info["uncommitted"]:
                    dirty += 1
                if info["ahead"] or info["behind"]:
                    unpushed += 1

    if not repos_info:
        return _result("git_status", "info", "All tracked repos are clean.", {})
    parts = []
    if dirty:
        parts.append(f"{dirty} repo(s) with uncommitted changes")
    if unpushed:
        parts.append(f"{unpushed} repo(s) ahead/behind their remote")
    summary = "; ".join(parts) + "."
    return _result("git_status", "notice", summary, repos_info)


# ── mem_pressure ─────────────────────────────────────────────────────────────


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse ``/proc/meminfo`` into a ``{field: kB}`` int mapping (pure)."""
    fields: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        tokens = rest.split()
        if not tokens:
            continue
        try:
            fields[key.strip()] = int(tokens[0])
        except ValueError:
            continue
    return fields


def meminfo_usage(fields: dict[str, int]) -> dict[str, float]:
    """Derive RAM/swap usage percentages from parsed meminfo fields (pure)."""
    mem_total = fields.get("MemTotal", 0)
    mem_available = fields.get("MemAvailable", 0)
    swap_total = fields.get("SwapTotal", 0)
    swap_free = fields.get("SwapFree", 0)

    mem_used_pct = (
        (mem_total - mem_available) / mem_total * 100.0 if mem_total else 0.0
    )
    swap_used_pct = (
        (swap_total - swap_free) / swap_total * 100.0 if swap_total else 0.0
    )
    return {
        "mem_total_gb": round(mem_total / 1024**2, 1),
        "mem_used_pct": round(mem_used_pct, 1),
        "swap_total_gb": round(swap_total / 1024**2, 1),
        "swap_used_pct": round(swap_used_pct, 1),
    }


def check_mem_pressure(
    meminfo_path: str | Path = "/proc/meminfo",
    threshold: float = 90.0,
    swap_threshold: float = 50.0,
) -> dict:
    """Report RAM/swap pressure from ``/proc/meminfo`` (thin collector)."""
    path = Path(meminfo_path)
    try:
        text = path.read_text()
    except OSError as exc:
        logger.debug("meminfo unavailable at {}: {}", path, exc)
        return _result("mem_pressure", "info", "Memory info unavailable.", {})

    usage = meminfo_usage(parse_meminfo(text))
    levels = ["info"]
    if usage["mem_used_pct"] >= threshold:
        levels.append("warn")
    if usage["swap_total_gb"] and usage["swap_used_pct"] >= swap_threshold:
        levels.append("warn")
    level = worst_level(levels)

    if level == "info":
        summary = (
            f"Memory healthy — {usage['mem_used_pct']:.0f}% RAM, "
            f"{usage['swap_used_pct']:.0f}% swap."
        )
    else:
        summary = (
            f"Memory under pressure — {usage['mem_used_pct']:.0f}% RAM, "
            f"{usage['swap_used_pct']:.0f}% swap."
        )
    return _result("mem_pressure", level, summary, usage)


# ── aggregator ───────────────────────────────────────────────────────────────

# Registry of (name -> collector). Keep this the single source of truth.
CHECKS = {
    "disk": check_disk,
    "failed_units": check_failed_units,
    "git_status": check_git_status,
    "mem_pressure": check_mem_pressure,
}


def _default_out_dir() -> Path:
    """Where JSON snapshots are written by default."""
    return Path.home() / ".lhassa" / "health"


def _notify(result: dict) -> bool:
    """Fire ``notify-send`` for a single result; no-op if the binary is absent.

    Returns ``True`` if a notification was actually sent.
    """
    if not shutil.which("notify-send"):
        return False
    suggestion = ""
    if result["name"] == "disk":
        suggestion = "Maybe clear some space when you get a chance."
    elif result["name"] == "failed_units":
        suggestion = "Worth a quick `systemctl --failed` to see what's up."
    elif result["name"] == "mem_pressure":
        suggestion = "Closing a few heavy apps should ease it."
    body = format_alert(result["summary"], level=result["level"], suggestion=suggestion)
    try:
        urgency = "critical" if result["level"] == "alert" else "normal"
        subprocess.run(
            ["notify-send", "-u", urgency, "Lhassa", body],
            capture_output=True,
            timeout=10,
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("notify-send failed: {}", exc)
        return False


def run_all(out_dir: str | Path | None = None, notify: bool = True) -> dict:
    """Run every check, snapshot results to JSON, and notify on warn/alert.

    Parameters
    ----------
    out_dir:
        Directory for ``<name>.json`` snapshots. Defaults to ``~/.lhassa/health``;
        override it (e.g. with a tmp dir) so tests never touch the real home.
    notify:
        When ``True`` (default), fire ``notify-send`` for warn/alert results.
        Silently skipped if the binary is missing.

    Returns
    -------
    dict
        Combined digest::

            {
              "ts": float,                 # unix timestamp
              "results": {name: result},   # per-check result dicts
              "worst_level": str,          # most severe level seen
              "ok": bool,                  # True if nothing at warn/alert
              "alerted": [name, ...],      # checks that triggered a notification
            }
    """
    out_path = Path(out_dir) if out_dir is not None else _default_out_dir()
    snapshots_enabled = True
    try:
        out_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("health snapshot directory unavailable: {}", exc)
        snapshots_enabled = False

    results: dict[str, dict] = {}
    alerted: list[str] = []

    for name, collector in CHECKS.items():
        try:
            result = collector()
        except Exception as exc:  # a check must never take the whole run down
            logger.warning("health check {} raised: {}", name, exc)
            result = _result(name, "info", f"Check '{name}' errored.", str(exc))
        results[name] = result

        if snapshots_enabled:
            try:
                (out_path / f"{name}.json").write_text(
                    json.dumps(result, ensure_ascii=True, indent=2)
                )
            except OSError as exc:
                logger.debug("could not write snapshot for {}: {}", name, exc)

        if notify and result["level"] in ("warn", "alert"):
            if _notify(result):
                alerted.append(name)

    levels = [r["level"] for r in results.values()]
    overall = worst_level(levels)
    digest = {
        "ts": time.time(),
        "results": results,
        "worst_level": overall,
        "ok": overall in ("info", "notice"),
        "alerted": alerted,
    }
    if snapshots_enabled:
        try:
            (out_path / "digest.json").write_text(
                json.dumps(digest, ensure_ascii=True, indent=2)
            )
        except OSError as exc:
            logger.debug("could not write digest: {}", exc)
    return digest


def format_digest(digest: dict) -> str:
    """Render a short, templated, LLM-free summary of a digest for the terminal."""
    lines = ["Lhassa — PC state check"]
    for name, result in digest["results"].items():
        marker = {"info": "ok", "notice": "··", "warn": "!!", "alert": "XX"}.get(
            result["level"], "··"
        )
        lines.append(f"  [{marker}] {name}: {result['summary']}")
    worst = digest["worst_level"]
    if digest["ok"]:
        lines.append("All clear.")
    else:
        lines.append(f"Worst level: {worst}.")
    return "\n".join(lines)
