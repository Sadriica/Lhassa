# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only collectors behind the Waybar widget (v2 state file).

Every function here is a *read*: the agent ledger and the review log are opened
read-only, ``systemctl`` and ``gh`` are only listed, and nothing is written. All
of them are blocking and cheap, so the writer (``waybar_state``) runs the batch
in a worker thread, never on the event loop. Each degrades to an empty value
when its source is missing, so a module simply stays empty.

Privacy (hard rule): what leaves this module is numbers and *names* of a repo or
workspace. Never prompt text, never a path, never a window title. A working
directory that the never-keep policy withholds (``~/.ssh``, the private
``lhassa/personal`` folder, ...) or that has a ``personal`` path component is
dropped before any name is derived from it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Hosts shown in the Agents module, in display order.
AGENT_HOSTS = ("claude-code", "codex", "orca", "hermes", "omp")

#: Sessions with an event in this window count as active.
AGENT_WINDOW_S = 300

#: Findings and pushes older than this leave the Attention module.
ATTENTION_WINDOW_S = 24 * 3600

#: Cap on how much of the review log tail is read (it only grows).
REVIEW_TAIL_BYTES = 512 * 1024

#: Directory names too generic to identify a repo; the parent name is used.
_GENERIC = frozenset({"app", "src", "web", "repo", "code", "main", "master"})

_GITHUB = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

CI_RED = frozenset({"failure", "timed_out", "startup_failure", "cancelled_by_failure"})
CI_TTL_S = 300.0
CI_MAX_REPOS = 8


# -- names and privacy ---------------------------------------------------------


def _private(path: str) -> bool:
    """True when ``path`` must never feed a name into the widget."""
    if not path or not isinstance(path, str):
        return True
    if "personal" in Path(path).parts:
        return True
    try:
        from lhassa.record.never_keep import withheld_rule

        return withheld_rule(path) is not None
    except Exception:
        return True  # fail closed


def repo_root(cwd: str) -> "Path | None":
    """The git work tree root holding ``cwd``, found without running git."""
    try:
        here = Path(cwd)
        for cand in (here, *here.parents):
            marker = cand / ".git"
            if marker.is_dir():
                return cand
            if marker.is_file():  # a linked worktree: follow it to the main tree
                text = marker.read_text(encoding="utf-8", errors="replace")
                m = re.match(r"gitdir:\s*(.+)", text.strip())
                if m:
                    gitdir = Path(m.group(1).strip())
                    if "worktrees" in gitdir.parts:
                        main = gitdir.parents[1].parent  # <main>/.git/worktrees/<n>
                        if (main / ".git").is_dir():
                            return main
                return cand
    except OSError:
        return None
    return None


def repo_name(cwd: str) -> str:
    """The repo or workspace name for a working directory, or ``""``.

    Private directories give ``""``. Otherwise: the basename of the git root
    (the main tree for a linked worktree), or of the directory itself when it is
    not in a repo; a generic name such as ``app`` borrows its parent's.
    """
    if _private(cwd):
        return ""
    root = repo_root(cwd) or Path(cwd)
    if _private(str(root)):
        return ""
    name = root.name
    if name.lower() in _GENERIC and root.parent.name:
        name = root.parent.name
    if name in ("", "/") or name == Path.home().name:
        return ""
    return name[:40]


# -- the agent ledger ----------------------------------------------------------


def ledger_path(env: "dict | None" = None) -> Path:
    env = os.environ if env is None else env
    raw = (env.get("AGENT_LEDGER_DB") or env.get("LHASSA_LEDGER_DB") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".agent-ledger" / "ledger.db"


def _connect_ro(path: Path) -> "sqlite3.Connection | None":
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        conn.execute("PRAGMA query_only=1")
        return conn
    except sqlite3.Error:
        return None


#: Detail types that are not agent work (a window title must never be read).
_NOT_WORK = ("window_focus", "resource_sample")


def _local_midnight(now: float) -> float:
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def collect_ledger(path: "Path | None" = None, now: "float | None" = None,
                   window: int = AGENT_WINDOW_S) -> dict:
    """Active sessions and today's totals from the agent ledger.

    Returns ``{"agents": {...}, "today": {...}, "cwds": [...]}``; ``cwds`` is for
    the writer's own use (CI repo discovery) and must not be written out.
    Empty values when the ledger is missing or unreadable.
    """
    now = time.time() if now is None else now
    empty = {
        "agents": {"window_s": window, "total": 0, "hosts": {}, "sessions": []},
        "today": {"events": 0, "api_usd": 0.0, "top_repo": ""},
        "cwds": [],
    }
    conn = _connect_ro(Path(path) if path else ledger_path())
    if conn is None:
        return empty
    try:
        ph = ",".join("?" for _ in AGENT_HOSTS)
        skip = ",".join("?" for _ in _NOT_WORK)
        rows = conn.execute(
            f"SELECT host, session, MAX(ts) FROM events WHERE ts >= ? AND host IN ({ph}) "
            f"AND COALESCE(json_extract(detail, '$.type'), '') NOT IN ({skip}) "
            "GROUP BY host, session",
            (now - window, *AGENT_HOSTS, *_NOT_WORK),
        ).fetchall()
        sessions, hosts, cwds = [], {}, []
        for host, session, _ts in rows:
            row = conn.execute(
                "SELECT json_extract(detail, '$.cwd') FROM events WHERE host = ? AND session = ? "
                "AND ts >= ? AND json_extract(detail, '$.cwd') IS NOT NULL ORDER BY ts DESC LIMIT 1",
                (host, session, now - window),
            ).fetchone()
            cwd = row[0] if row and isinstance(row[0], str) else ""
            name = repo_name(cwd) if cwd else ""
            if cwd and name:
                cwds.append(cwd)
            hosts[host] = hosts.get(host, 0) + 1
            sessions.append({"host": host, "repo": name})
        sessions.sort(key=lambda s: (AGENT_HOSTS.index(s["host"]), s["repo"]))
        midnight = _local_midnight(now)
        events, cost = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0.0) FROM events WHERE ts >= ? "
            f"AND COALESCE(json_extract(detail, '$.type'), '') NOT IN ({skip})",
            (midnight, *_NOT_WORK),
        ).fetchone()
        counts: dict = {}
        for cwd, n in conn.execute(
            "SELECT json_extract(detail, '$.cwd') AS c, COUNT(*) FROM events WHERE ts >= ? "
            "AND c IS NOT NULL GROUP BY c ORDER BY 2 DESC LIMIT 300",
            (midnight,),
        ):
            name = repo_name(cwd) if isinstance(cwd, str) else ""
            if name:
                counts[name] = counts.get(name, 0) + n
        top = max(counts, key=lambda k: (counts[k], k)) if counts else ""
        return {
            "agents": {
                "window_s": window,
                "total": len(sessions),
                "hosts": {h: hosts[h] for h in AGENT_HOSTS if h in hosts},
                "sessions": sessions[:20],
            },
            "today": {"events": int(events or 0), "api_usd": round(float(cost or 0.0), 4), "top_repo": top},
            "cwds": cwds,
        }
    except sqlite3.Error as exc:
        logger.debug("ledger read failed: %s", exc)
        return empty
    finally:
        conn.close()


def collect_pending(path: "Path | None" = None, now: "float | None" = None) -> list[dict]:
    """Ledger decisions waiting on a human (``outcome = pending``, unresolved).

    Counted separately from the brain's own approval queue: this is what the
    external agents (Claude Code, Orca, ...) asked and nobody answered.
    """
    now = time.time() if now is None else now
    conn = _connect_ro(Path(path) if path else ledger_path())
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT host FROM events WHERE outcome = 'pending' AND ts >= ? AND id NOT IN "
            "(SELECT json_extract(detail, '$.resolves') FROM events "
            "WHERE json_extract(detail, '$.resolves') IS NOT NULL) ORDER BY ts",
            (now - ATTENTION_WINDOW_S,),
        ).fetchall()
        return [{"kind": "ledger", "label": str(h or "agent"), "source": "ledger"} for (h,) in rows[:20]]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# -- the review log ------------------------------------------------------------


def review_log_path(env: "dict | None" = None) -> Path:
    env = os.environ if env is None else env
    raw = (env.get("LHASSA_REVIEW_DIR") or "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / ".lhassa" / "review"
    return base / "review.jsonl"


def _parse_ts(stamp: object) -> "float | None":
    from datetime import datetime

    try:
        return datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        try:
            return datetime.fromisoformat(str(stamp)).timestamp()
        except ValueError:
            return None


def collect_review(path: "Path | None" = None, now: "float | None" = None) -> dict:
    """Recent critical findings, pushes with findings, and today's commit and push counts."""
    now = time.time() if now is None else now
    out: dict = {"critical": [], "pushes": [], "commits": 0, "push_count": 0}
    path = Path(path) if path else review_log_path()
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > REVIEW_TAIL_BYTES:
                fh.seek(size - REVIEW_TAIL_BYTES)
                fh.readline()  # drop the partial first line
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return out
    midnight = _local_midnight(now)
    seen_crit: set = set()
    seen_push: set = set()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            continue
        event = str(rec.get("event") or "")
        counts = rec.get("counts") if isinstance(rec.get("counts"), dict) else {}
        crit = int(counts.get("critical") or 0)
        warn = int(counts.get("warn") or 0)
        repo = repo_name(str(rec.get("repo") or "")) if rec.get("repo") else ""
        if ts >= midnight and not rec.get("error"):
            if event == "commit-msg":
                out["commits"] += 1
            elif event == "pre-push":
                out["push_count"] += 1
        if now - ts > ATTENTION_WINDOW_S:
            break  # the log is chronological: nothing older matters
        if rec.get("repo") and not repo:
            continue  # a private repo: not even its findings are surfaced by name
        if crit and (event, repo) not in seen_crit:
            seen_crit.add((event, repo))
            out["critical"].append({"kind": "review", "label": repo or "repo", "n": crit,
                                    "source": "lhassa review"})
        if event == "pre-push" and (crit or warn) and repo not in seen_push:
            seen_push.add(repo)
            out["pushes"].append({"kind": "push", "label": repo or "repo", "n": crit + warn,
                                  "source": "pre-push review"})
    return out


# -- systemd, power, machine ---------------------------------------------------


def failed_services(run=None) -> list[dict]:
    """Failed ``lhassa-*`` user units (``systemctl --user --failed``)."""
    run = run or subprocess.run
    try:
        proc = run(["systemctl", "--user", "--failed", "--no-legend", "--plain", "--no-pager"],
                   capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (getattr(proc, "stdout", "") or "").splitlines():
        parts = line.split()
        if parts and parts[0].startswith("lhassa-") and _ID.match(parts[0]):
            out.append({"kind": "service", "label": parts[0].removesuffix(".service"),
                        "source": "systemd"})
    return out[:8]


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def power_mode(conf: str = "/etc/tlp.d/99-power-mode.conf") -> str:
    """The profile written by ``power-mode``: ``saver``, ``balanced``, ``performance`` or ``""``."""
    m = re.search(r'^#\s*mode:\s*"([a-z-]+)"', _read(conf), re.MULTILINE)
    mode = m.group(1) if m else ""
    return {"ahorro": "saver", "equilibrado": "balanced", "rendimiento": "performance"}.get(mode, mode)


def package_temp(root: str = "/sys/class/hwmon") -> "float | None":
    """CPU package temperature in Celsius (the ``coretemp`` hwmon), or ``None``."""
    try:
        for entry in sorted(os.listdir(root)):
            base = os.path.join(root, entry)
            if _read(os.path.join(base, "name")) == "coretemp":
                raw = _read(os.path.join(base, "temp1_input"))
                if raw:
                    return round(int(raw) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    return None


def battery(root: str = "/sys/class/power_supply") -> "tuple[int | None, bool]":
    """``(percent, charging)`` of the first battery, ``(None, False)`` without one."""
    try:
        for entry in sorted(os.listdir(root)):
            if entry.startswith("BAT"):
                pct = _read(os.path.join(root, entry, "capacity"))
                status = _read(os.path.join(root, entry, "status")).lower()
                return (int(pct) if pct.isdigit() else None), status in ("charging", "full")
    except OSError:
        pass
    return None, False


class CpuMeter:
    """CPU busy percent between two calls (``/proc/stat``)."""

    def __init__(self, stat: str = "/proc/stat") -> None:
        self.stat = stat
        self._last: "tuple[int, int] | None" = None

    def sample(self) -> "int | None":
        try:
            fields = [int(x) for x in _read(self.stat).splitlines()[0].split()[1:]]
        except (IndexError, ValueError):
            return None
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total = sum(fields)
        last, self._last = self._last, (idle, total)
        if last is None or total <= last[1]:
            return None
        busy = 1.0 - (idle - last[0]) / (total - last[1])
        return max(0, min(100, int(round(busy * 100))))


# -- CI (gh), cached ------------------------------------------------------------


def gh_binary() -> "str | None":
    found = shutil.which("gh")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "gh"
    return str(local) if local.is_file() else None


def parse_remote(url: str) -> str:
    """``owner/repo`` from a GitHub remote URL (credentials dropped), else ``""``."""
    m = _GITHUB.search((url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def remote_slug(root: Path, run=None) -> str:
    run = run or subprocess.run
    try:
        proc = run(["git", "-C", str(root), "remote", "get-url", "origin"],
                   capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return ""
    return parse_remote(getattr(proc, "stdout", "") or "") if getattr(proc, "returncode", 1) == 0 else ""


def configured_repos(env: "dict | None" = None, cwds: "list[str] | None" = None, run=None) -> list[str]:
    """Repos to watch: ``LHASSA_WAYBAR_CI_REPOS`` (``owner/repo`` list), else the
    GitHub repos of recent ledger sessions (off with ``LHASSA_WAYBAR_CI_AUTO=0``)."""
    env = os.environ if env is None else env
    repos = [r.strip() for r in (env.get("LHASSA_WAYBAR_CI_REPOS") or "").split(",") if r.strip()]
    repos = [r for r in repos if _SLUG.match(r)]
    auto = str(env.get("LHASSA_WAYBAR_CI_AUTO", "1")).strip().lower() not in ("0", "false", "off", "no")
    if not repos and auto:
        for cwd in cwds or []:
            root = repo_root(cwd)
            if root is None or _private(str(root)):
                continue
            slug = remote_slug(root, run)
            if slug and slug not in repos:
                repos.append(slug)
    seen: list[str] = []
    for r in repos:
        if r not in seen:
            seen.append(r)
    return seen[:CI_MAX_REPOS]


def ci_red(repo: str, gh: str, run=None) -> "bool | None":
    """Is the latest completed run of ``repo`` red? ``None`` when unknown."""
    run = run or subprocess.run
    try:
        proc = run([gh, "run", "list", "--repo", repo, "--limit", "1", "--status", "completed",
                    "--json", "conclusion"], capture_output=True, text=True, timeout=15)
        if getattr(proc, "returncode", 1) != 0:
            return None
        data = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return False if isinstance(data, list) else None
    return str(data[0].get("conclusion") or "") in CI_RED


class CiWatcher:
    """CI status for a few repos, cached for :data:`CI_TTL_S` inside the writer.

    :meth:`failing` returns the cache instantly and starts a background refresh
    when it is stale, so the writer loop never waits on ``gh`` or the network.
    A missing ``gh`` or no network leaves the cache empty (no CI items).
    """

    def __init__(self, ttl: float = CI_TTL_S, clock=time.monotonic, run=None) -> None:
        self.ttl = ttl
        self.clock = clock
        self.run = run
        self._at: "float | None" = None
        self._red: list[str] = []
        self._task: "asyncio.Task | None" = None

    def stale(self) -> bool:
        return self._at is None or self.clock() - self._at >= self.ttl

    def _poll(self, repos: list[str]) -> list[str]:
        gh = gh_binary()
        if not gh:
            return []
        return [r for r in repos if ci_red(r, gh, self.run)]

    async def refresh(self, repos: list[str]) -> list[str]:
        """Poll now (in a worker thread) and update the cache."""
        self._at = self.clock()  # set first: a slow poll is not retried in a loop
        try:
            self._red = await asyncio.to_thread(self._poll, repos)
        except Exception as exc:
            logger.debug("ci poll failed: %s", exc)
            self._red = []
        return self._red

    def failing(self, repos: list[str]) -> list[dict]:
        if self.stale() and (self._task is None or self._task.done()):
            try:
                self._task = asyncio.get_running_loop().create_task(self.refresh(repos))
            except RuntimeError:
                pass
        return [{"kind": "ci", "label": r.split("/")[-1], "source": "GitHub Actions"}
                for r in self._red if r in repos]
