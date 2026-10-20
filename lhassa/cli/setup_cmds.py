# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lhassa setup``: ONE COMMAND, one wizard: ``git clone`` to a working brain.

The verified problem (see ``doc/plan-4.md``, Stream 3): installing Lhassa does
**not** produce a working system. Seeds are never applied automatically
(:func:`lhassa.seeds.apply_all` is only ever called from ``lhassa seed
apply-all``); without it there are no departments, no digest, no maintenance
and no flows, just a bare chat. It is eight manual steps from a fresh clone.

This module closes that gap with six deterministic steps, all $0 and none of
them requiring a model call:

  1. **Detect the host**: reuses :mod:`lhassa.host` (already tiers Linux/macOS);
     no new detection is written here.
  2. **Detect or configure a brain**: a subscription CLI on PATH (``claude``/
     ``codex``/``opencode``, $0), a reachable local Ollama ($0), or an existing
     API key in the environment/``.env`` (metered). :func:`detect_brain` is pure
     over injected inputs so it needs no network/TTY to test.
  3. **Permissions by scope, explained in plain language**: :data:`SCOPES` is
     the small, fixed catalog (read/commands/network/notify); each grant writes
     a REAL config knob through :mod:`lhassa.settings` (never ``.env``, never a
     bare on/off flag) via the pure :func:`plan_scope_effects`. Declining a
     scope always leaves a working system with that capability off; nothing is
     all-or-nothing.
  4. **Choose a profile and apply the matching seeds**: the actual fix for the
     verified problem: :func:`apply_profile_seeds` calls
     :func:`lhassa.seeds.apply_seed` for real, not just documents that you
     could.
  5. **Install services**: systemd user units on Linux, launchd user agents on
     macOS (:func:`install_services`), both reusing the units that already ship
     under ``scripts/systemd`` / ``scripts/launchd``.
  6. **Smoke check**: :func:`smoke_check` counts what actually exists
     (departments, flows) and reports ONE honest sentence about what is alive.

Every step degrades on its own: a missing systemd session, a network probe
that times out, an unknown seed; none of it raises out of ``lhassa setup``.
Re-running is idempotent (the same guarantee every seed and every settings
write already carries). ``--yes`` (optionally with ``--profile``) is the
non-interactive path a script can run unattended; without it the command asks
a short, skippable set of questions on a real TTY.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import typer

from lhassa.cli.theme import LHASSA_THEME

__all__ = [
    "PROFILES",
    "SCOPES",
    "BrainDetection",
    "Scope",
    "ScopeEffect",
    "detect_brain",
    "seeds_for_profile",
    "plan_scope_effects",
    "apply_scope_effects",
    "apply_profile_seeds",
    "install_services",
    "smoke_check",
    "setup_cmd",
]


def _out():
    from rich.console import Console

    return Console(theme=LHASSA_THEME)


def _err():
    from rich.console import Console

    return Console(stderr=True, theme=LHASSA_THEME)


# ── step 4: profiles → seeds ─────────────────────────────────────────────────
# The baseline is applied under EVERY profile: it alone already satisfies the
# DoD ("departments, digest, maintenance and flows"); `temple` provisions the
# inner department, `digest`/`maintenance` schedule the recurring hygiene, and
# `personal` (the owner's own morning/evening secretary flows) guarantees a
# real work-flow exists even when no domain-specific seed applies. A profile
# then layers ONE extra domain seed on top, chosen from what's actually
# registered today (see `lhassa seed list`) rather than inventing new seeds.
BASE_SEEDS: list[str] = ["temple", "digest", "maintenance", "personal"]

PROFILE_EXTRA_SEEDS: dict[str, list[str]] = {
    "personal": [],
    "research": ["research"],
    "dev": ["cv"],
    "knowledge": [],
}

PROFILE_DESCRIPTIONS: dict[str, str] = {
    "personal": "A personal secretary; morning/evening briefings, reminders, memory.",
    "research": "The research line; the geshe's aides and the idea-intake flow.",
    "dev": "A coding companion; the CV pipeline as the reference work-flow "
    "(agentic runs go through `lhassa code`, not a seed).",
    "knowledge": "Document perception; point it at your own directories in the "
    "permissions step; no extra seed beyond the baseline.",
}

PROFILES: tuple[str, ...] = tuple(PROFILE_DESCRIPTIONS)
DEFAULT_PROFILE = "personal"


def seeds_for_profile(profile: str) -> list[str]:
    """The ordered, de-duplicated seed names a profile applies (baseline first)."""
    ordered = list(BASE_SEEDS)
    for name in PROFILE_EXTRA_SEEDS.get(profile, []):
        if name not in ordered:
            ordered.append(name)
    return ordered


# ── step 2: detect or configure a brain ──────────────────────────────────────

API_KEY_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "CEREBRAS_API_KEY",
)
SUBSCRIPTION_CLIS: tuple[str, ...] = ("claude", "codex", "opencode")
DEFAULT_OLLAMA_URL = "http://localhost:11434"


@dataclass(frozen=True)
class BrainDetection:
    """What :func:`detect_brain` found, and which option it recommends.

    ``chosen`` is one of ``"cli"`` / ``"local"`` / ``"api"`` / ``"none"``;
    picked in $0-first order (a subscription CLI beats a local model beats a
    metered API key), matching the router's own "CLIs first" philosophy
    (``doc/architecture.md``: "Two model seats").
    """

    api_key_found: Optional[str]
    cli_found: Optional[str]
    ollama_up: bool
    ollama_url: str
    chosen: str
    explanation: list[str] = field(default_factory=list)


def _probe_ollama(url: str, *, timeout: float = 1.0) -> bool:
    """Best-effort reachability check. Never raises; returns ``False`` on any
    failure (offline, DNS, refused, timeout, malformed URL)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return 200 <= getattr(resp, "status", 200) < 500
    except (OSError, socket.timeout, ValueError):
        return False
    except Exception:
        return False


def detect_brain(
    *,
    env: Optional[dict] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    probe_ollama: Optional[Callable[[str], bool]] = None,
) -> BrainDetection:
    """Detect a usable brain from the host; pure over injected inputs.

    No TTY and no real network call is required to exercise this: pass a fake
    ``env`` mapping, a stub ``which``, and a stub ``probe_ollama`` and the
    decision logic is fully exercised offline.
    """
    env = os.environ if env is None else env
    which_fn = which or (lambda _name: None)
    probe = probe_ollama or _probe_ollama

    api_key_found = next(
        (name for name in API_KEY_ENV_VARS if (env.get(name) or "").strip()), None
    )
    cli_found = next((cli for cli in SUBSCRIPTION_CLIS if which_fn(cli)), None)
    ollama_url = (env.get("LHASSA_LOCAL_MODEL_URL") or "").strip() or DEFAULT_OLLAMA_URL
    ollama_up = bool(probe(ollama_url))

    explanation: list[str] = []
    if cli_found:
        chosen = "cli"
        explanation.append(
            f"found the '{cli_found}' CLI on PATH; $0, subscription-backed, no key needed."
        )
    elif ollama_up:
        chosen = "local"
        explanation.append(f"a local model answered at {ollama_url}; $0, fully local.")
    elif api_key_found:
        chosen = "api"
        explanation.append(
            f"found {api_key_found} in the environment/.env; metered, pay-per-token."
        )
    else:
        chosen = "none"
        explanation.append(
            "no brain found yet. Cheapest paths: install a subscription CLI "
            "(claude / codex / opencode) on PATH, or run Ollama locally "
            "(https://ollama.com, then `ollama pull llama3.2`); both are $0. "
            "An API key (GROQ_API_KEY has a free tier) also works, metered."
        )
    if api_key_found and chosen != "api":
        explanation.append(
            f"({api_key_found} is also set but a $0 option is preferred by default.)"
        )
    return BrainDetection(
        api_key_found=api_key_found,
        cli_found=cli_found,
        ollama_up=ollama_up,
        ollama_url=ollama_url,
        chosen=chosen,
        explanation=explanation,
    )


# ── step 3: permissions by scope ─────────────────────────────────────────────


@dataclass(frozen=True)
class Scope:
    """One permission scope, explained in plain language for the wizard prompt."""

    id: str
    label: str
    explain: str
    default_enabled: bool


SCOPES: tuple[Scope, ...] = (
    Scope(
        id="read",
        label="Read your directories",
        explain=(
            "Lets Lhassa read files under directories you name, for its own "
            "recall and knowledge search. Nothing is indexed until you point it "
            "at a real path; declining leaves knowledge search off."
        ),
        default_enabled=False,
    ),
    Scope(
        id="commands",
        label="Run commands (repo sync)",
        explain=(
            "Lets Lhassa auto-run its own repo-sync command the moment it sees "
            "a new commit on a repo you list. Every OTHER command; including a "
            "raw shell command; still asks your approval every single time, "
            "regardless of this scope."
        ),
        default_enabled=False,
    ),
    Scope(
        id="network",
        label="Reach the network",
        explain=(
            "Lets the brain (heartbeat + chat) call out over the network; a "
            "cloud API provider or a web search. Declining forces the routine "
            "heartbeat onto the local-only provider; a configured cloud API key "
            "still needs this scope to actually be used."
        ),
        default_enabled=True,
    ),
    Scope(
        id="notify",
        label="Notify you",
        explain=(
            "Lets Lhassa send a desktop toast, and a Telegram message if you "
            "later set a bot token; the digest, approvals and alerts ride this."
        ),
        default_enabled=True,
    ),
)

_SCOPES_BY_ID: dict[str, Scope] = {s.id: s for s in SCOPES}


@dataclass(frozen=True)
class ScopeEffect:
    """One concrete config write (or an informational note) a scope decision
    produces. ``kind`` is ``"setting"`` (via :mod:`lhassa.settings`), ``"env"``
    (the advanced ``LHASSA_*`` escape hatch), or ``"note"`` (nothing written,
    surfaced to the owner as-is)."""

    kind: str
    key: str
    value: str
    note: str


def plan_scope_effects(
    enabled: dict[str, bool],
    *,
    knowledge_roots: str = "",
    protected_roots: str = "",
    allowlist_repos: str = "",
    auto_sync_repos: str = "",
) -> list[ScopeEffect]:
    """Pure planning: scope decisions → the config writes they imply.

    Never touches disk; :func:`apply_scope_effects` executes the plan this
    returns. Kept separate so the decision logic is unit-testable with no I/O.
    """
    effects: list[ScopeEffect] = []

    if enabled.get("read"):
        if knowledge_roots:
            effects.append(
                ScopeEffect("setting", "knowledge_roots", knowledge_roots,
                             "Lhassa may read and index these directories.")
            )
        if protected_roots:
            effects.append(
                ScopeEffect("setting", "protected_roots", protected_roots,
                             "Reads under these directories will each ask your "
                             "approval first.")
            )
        if not knowledge_roots and not protected_roots:
            effects.append(
                ScopeEffect("note", "read", "",
                             "granted, but no directory was named yet; nothing "
                             "to read until you add one (`lhassa knowledge` or "
                             "the Settings panel).")
            )
    else:
        effects.append(
            ScopeEffect("setting", "knowledge_roots", "",
                         "declined; knowledge roots left empty.")
        )
        effects.append(
            ScopeEffect("setting", "protected_roots", "",
                         "declined; protected roots left empty.")
        )

    if enabled.get("commands"):
        if auto_sync_repos:
            effects.append(
                ScopeEffect("setting", "auto_sync_repos", auto_sync_repos,
                             "these repos reconcile themselves on a new commit, "
                             "no approval needed for that one action.")
            )
        if allowlist_repos:
            effects.append(
                ScopeEffect("env", "LHASSA_ALLOWLIST_REPOS", allowlist_repos,
                             "sync_repo is restricted to exactly these repos.")
            )
        effects.append(
            ScopeEffect("note", "commands", "",
                         "granted; remember `run_command` and everything else "
                         "still needs your tap; for OS-level privileged commands "
                         "(systemctl/package managers) see "
                         "scripts/install-privileged.sh, run separately with sudo.")
        )
    else:
        effects.append(
            ScopeEffect("setting", "auto_sync_repos", "",
                         "declined; nothing auto-runs; a repo change still "
                         "reaches judgment, just never a fixed action.")
        )

    if not enabled.get("network"):
        effects.append(
            ScopeEffect("setting", "heartbeat_provider", "local",
                         "declined; the heartbeat is pinned to the local-only "
                         "provider so routine thinking never reaches the network.")
        )
        effects.append(
            ScopeEffect("note", "network", "",
                         "declined; a cloud API provider or a web search will "
                         "fail closed until you re-run setup and grant this.")
        )

    if enabled.get("notify"):
        effects.append(
            ScopeEffect("setting", "notify_channels", "telegram,desktop",
                         "desktop toasts are on now; Telegram activates once you "
                         "set LHASSA_TELEGRAM_BOT_TOKEN in .env.")
        )
    else:
        effects.append(
            ScopeEffect("setting", "notify_channels", "",
                         "declined; nothing will announce itself; check "
                         "`lhassa approvals`/`lhassa report` yourself.")
        )

    return effects


def apply_scope_effects(effects: list[ScopeEffect], *, settings_mod=None) -> dict:
    """Execute a :func:`plan_scope_effects` plan through :mod:`lhassa.settings`.

    Never raises: a bad key/value is captured per-effect, the rest still apply.
    """
    if settings_mod is None:
        from lhassa import settings as settings_mod
    applied: list[dict] = []
    notes: list[str] = []
    errors: list[str] = []
    for effect in effects:
        if effect.kind == "note":
            notes.append(effect.note)
            continue
        try:
            if effect.kind == "setting":
                result = settings_mod.set(effect.key, effect.value)
            elif effect.kind == "env":
                result = settings_mod.set_env(effect.key, effect.value)
            else:
                errors.append(f"{effect.key}: unknown effect kind {effect.kind!r}")
                continue
        except Exception as exc:  # settings.* already degrades, this is a last resort
            errors.append(f"{effect.key}: {type(exc).__name__}: {exc}")
            continue
        if result.get("error"):
            errors.append(f"{effect.key}: {result['error']}")
        else:
            applied.append({"key": effect.key, "value": effect.value, "note": effect.note})
    return {"applied": applied, "notes": notes, "errors": errors}


# ── step 4: apply the matching seeds ─────────────────────────────────────────


def apply_profile_seeds(profile: str, config=None, *, apply_seed_fn=None) -> dict:
    """Apply every seed :func:`seeds_for_profile` names for ``profile``.

    This is the actual fix for the verified problem: seeds are applied FOR
    REAL here (not merely documented as available). Idempotent; re-running is
    safe, mirroring every seed's own contract. A single seed raising is
    captured under its own result rather than aborting the rest.
    """
    registered = None
    if apply_seed_fn is None:
        from lhassa.seeds import SEEDS as registered
        from lhassa.seeds import apply_seed as apply_seed_fn
    names = seeds_for_profile(profile)
    results: dict[str, dict] = {}
    for name in names:
        if registered is not None and name not in registered:
            # A profile's extra seed may ship in a capability pack (e.g. ``cv``)
            # that is not installed: skip it honestly instead of failing.
            results[name] = {"skipped": "not installed"}
            continue
        try:
            results[name] = apply_seed_fn(name, config)
        except Exception as exc:  # a seed must never abort the whole wizard
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return {"profile": profile, "seeds": names, "results": results}


# ── step 5: install services ─────────────────────────────────────────────────

_SYSTEMD_UNITS: tuple[str, ...] = (
    "lhassa-app.service",
    "lhassa-heartbeat.service",
    "lhassa-heartbeat.timer",
    "lhassa-health.service",
    "lhassa-health.timer",
    "lhassa-monitor.service",
)
_SYSTEMD_ENABLE_NOW: tuple[str, ...] = (
    "lhassa-app.service",
    "lhassa-heartbeat.timer",
    "lhassa-health.timer",
    "lhassa-monitor.service",
)
_REPO_PLACEHOLDER = "%h/Code/lhassa"

_LAUNCHD_PLISTS: tuple[str, ...] = (
    "com.lhassa.app.plist",
    "com.lhassa.heartbeat.plist",
    "com.lhassa.health.plist",
    "com.lhassa.monitor.plist",
)
_LAUNCHD_PATH_PLACEHOLDER = "/Users/USERNAME/Code/lhassa"
_LAUNCHD_BIN_PLACEHOLDER = "/Users/USERNAME/.local/bin"


def _install_systemd(repo_root: Path, home: Path, *, run, which, dry_run: bool) -> dict:
    result: dict = {"kind": "systemd", "installed": [], "enabled": [], "skipped": [], "errors": []}
    if which("systemctl") is None:
        result["errors"].append(
            "systemctl not found; no systemd user session on this host; "
            "start Lhassa manually (`make run`) instead."
        )
        return result

    src_dir = repo_root / "scripts" / "systemd"
    dst_dir = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))) / "systemd" / "user"

    if dry_run:
        result["skipped"] = [u for u in _SYSTEMD_UNITS if (src_dir / u).exists()]
        return result

    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"could not create {dst_dir}: {exc}")
        return result

    for unit in _SYSTEMD_UNITS:
        src_file = src_dir / unit
        if not src_file.exists():
            result["skipped"].append(unit)
            continue
        try:
            text = src_file.read_text().replace(_REPO_PLACEHOLDER, str(repo_root))
            (dst_dir / unit).write_text(text)
            result["installed"].append(unit)
        except OSError as exc:
            result["errors"].append(f"{unit}: {exc}")

    try:
        run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
    except Exception as exc:
        result["errors"].append(f"daemon-reload: {exc}")

    for unit in _SYSTEMD_ENABLE_NOW:
        if unit not in result["installed"]:
            continue
        try:
            proc = run(["systemctl", "--user", "enable", "--now", unit], check=False, capture_output=True)
            if getattr(proc, "returncode", 1) == 0:
                result["enabled"].append(unit)
            else:
                result["errors"].append(f"enable --now {unit} failed (exit {getattr(proc, 'returncode', '?')})")
        except Exception as exc:
            result["errors"].append(f"{unit}: {exc}")

    return result


def _install_launchd(repo_root: Path, home: Path, *, run, which, dry_run: bool) -> dict:
    result: dict = {"kind": "launchd", "installed": [], "enabled": [], "skipped": [], "errors": []}
    if which("launchctl") is None:
        result["errors"].append(
            "launchctl not found; cannot install user agents on this host; "
            "start Lhassa manually (`make run`) instead."
        )
        return result

    src_dir = repo_root / "scripts" / "launchd"
    dst_dir = home / "Library" / "LaunchAgents"

    if dry_run:
        result["skipped"] = [p for p in _LAUNCHD_PLISTS if (src_dir / p).exists()]
        return result

    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"could not create {dst_dir}: {exc}")
        return result

    local_bin = str(home / ".local" / "bin")
    for name in _LAUNCHD_PLISTS:
        src_file = src_dir / name
        if not src_file.exists():
            result["skipped"].append(name)
            continue
        try:
            text = src_file.read_text()
            text = text.replace(_LAUNCHD_PATH_PLACEHOLDER, str(repo_root))
            text = text.replace(_LAUNCHD_BIN_PLACEHOLDER, local_bin)
            dst_file = dst_dir / name
            dst_file.write_text(text)
            result["installed"].append(name)
        except OSError as exc:
            result["errors"].append(f"{name}: {exc}")

    for name in result["installed"]:
        try:
            proc = run(["launchctl", "load", str(dst_dir / name)], check=False, capture_output=True)
            if getattr(proc, "returncode", 1) == 0:
                result["enabled"].append(name)
            else:
                result["errors"].append(f"load {name} failed (exit {getattr(proc, 'returncode', '?')})")
        except Exception as exc:
            result["errors"].append(f"{name}: {exc}")

    return result


def install_services(
    platform_name: str,
    repo_root: Path,
    *,
    home: Optional[Path] = None,
    run: Callable = subprocess.run,
    which: Callable[[str], Optional[str]] = shutil.which,
    dry_run: bool = False,
) -> dict:
    """Install the always-on services for ``platform_name`` ("linux"/"darwin").

    Reuses the existing unit files under ``scripts/systemd``/``scripts/launchd``
    verbatim (only the checkout path is templated in, mirroring
    ``scripts/install.sh``'s own ``sed`` step); no new unit is authored here.
    An unsupported platform, or a missing ``systemctl``/``launchctl``, degrades
    to an honest error entry rather than raising.
    """
    home = home or Path.home()
    if platform_name == "linux":
        return _install_systemd(repo_root, home, run=run, which=which, dry_run=dry_run)
    if platform_name == "darwin":
        return _install_launchd(repo_root, home, run=run, which=which, dry_run=dry_run)
    return {
        "kind": platform_name,
        "installed": [],
        "enabled": [],
        "skipped": [],
        "errors": [
            f"no service installer for platform {platform_name!r}; "
            "start Lhassa manually (`make run`)."
        ],
    }


# ── step 6: smoke check ───────────────────────────────────────────────────────


def smoke_check(config=None, *, load_topology_fn=None, flow_store_cls=None) -> dict:
    """Count what actually exists after setup and render ONE honest sentence.

    Reads through the same seams the CLI/dashboard already use (org topology,
    the flow store); no new storage, no model call. Never raises: a broken
    read is recorded under ``errors`` and folded into an honest sentence.
    """
    if load_topology_fn is None:
        from lhassa.org.topology import load_topology as load_topology_fn
    if flow_store_cls is None:
        from lhassa.flows.store import FlowStore as flow_store_cls

    departments = 0
    flows = 0
    errors: list[str] = []

    try:
        topo = load_topology_fn(config)
        # The root (the permanent administrator) is not itself "a department";
        # count everything else the topology carries.
        departments = max(0, len(topo.nodes()) - 1)
    except Exception as exc:
        errors.append(f"topology: {type(exc).__name__}: {exc}")

    try:
        store = flow_store_cls(config)
        flows = len(store.list_flows())
    except Exception as exc:
        errors.append(f"flows: {type(exc).__name__}: {exc}")

    ok = departments > 0 and flows > 0
    if ok:
        sentence = (
            f"Lhassa is alive: {departments} brain(s) beyond the root and "
            f"{flows} work-flow(s) are wired; the heartbeat can run them from here."
        )
    elif errors:
        sentence = (
            "Setup ran, but the smoke check could not read the org/flow state; "
            "see the errors above."
        )
    else:
        sentence = (
            "Setup ran, but no departments or flows exist yet; re-run "
            "`lhassa setup` or `lhassa seed apply-all` to finish bootstrapping."
        )
    return {"departments": departments, "flows": flows, "ok": ok, "errors": errors, "sentence": sentence}


# ── the wizard itself (interactive + non-interactive) ────────────────────────


def _ask_yes_no(prompt: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def _ask_text(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{hint} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


def _ask_profile(default: str) -> str:
    out = _out()
    out.print("[title]Choose a profile[/]")
    for name in PROFILES:
        out.print(f"  [model]{name}[/]; {PROFILE_DESCRIPTIONS[name]}")
    choice = _ask_text("Profile", default)
    return choice if choice in PROFILE_DESCRIPTIONS else default


def setup_cmd(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Non-interactive: accept every default, never prompt (script-safe).",
    ),
    profile: Optional[str] = typer.Option(
        None, "--profile",
        help=f"Profile to apply ({'/'.join(PROFILES)}); prompted if omitted and interactive.",
    ),
    scopes: Optional[str] = typer.Option(
        None, "--scopes",
        help="CSV of scopes to GRANT (read,commands,network,notify). "
        "Omitted scopes stay at their default; use '' for none.",
    ),
    knowledge_roots: str = typer.Option(
        "", "--knowledge-root", help="Directory the 'read' scope may index (repeatable via CSV)."
    ),
    auto_sync_repos: str = typer.Option(
        "", "--auto-sync-repo", help="Repo path the 'commands' scope may auto-sync (CSV)."
    ),
    no_services: bool = typer.Option(
        False, "--no-services", help="Skip installing systemd/launchd services."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Plan every step but write nothing (services/seeds/settings)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the full result as one JSON object."
    ),
) -> None:
    """One command, one wizard; from a fresh clone to a working brain.

    Detects the host and a brain, asks (or accepts flags) for permissions by
    scope, applies the matching seeds for a chosen profile, installs the
    always-on services, and reports in one sentence what is now alive.
    Non-interactive with ``--yes``; every step degrades honestly, never raises.
    """
    out = _out()
    interactive = sys.stdin.isatty() and not yes
    report: dict = {}

    # ── 1. host ───────────────────────────────────────────────────────────────
    from lhassa.host import current_platform_name, get_platform

    platform_name = current_platform_name()
    platform_impl = get_platform(platform_name)
    report["host"] = {"platform": platform_name, "supported": platform_impl is not None}
    out.print(f"[brand]ᕮ setup[/] [dim]host:[/] {platform_name or 'unknown'}"
               f"{' (supported)' if platform_impl is not None else ' (best-effort, no platform driver)'}")

    # ── 2. brain ─────────────────────────────────────────────────────────────
    detection = detect_brain()
    report["brain"] = {
        "chosen": detection.chosen,
        "api_key_found": detection.api_key_found,
        "cli_found": detection.cli_found,
        "ollama_up": detection.ollama_up,
        "explanation": detection.explanation,
    }
    out.print("[title]Brain[/]")
    for line in detection.explanation:
        out.print(f"  {line}")
    if detection.chosen == "none" and interactive:
        _ask_yes_no("Continue without a configured brain? (you can add one later)", True)

    # ── 3. permissions by scope ─────────────────────────────────────────────
    enabled: dict[str, bool] = {s.id: s.default_enabled for s in SCOPES}
    if scopes is not None:
        granted = {s.strip() for s in scopes.split(",") if s.strip()}
        enabled = {s.id: (s.id in granted) for s in SCOPES}
    elif interactive:
        out.print("[title]Permissions[/] [dim](explained plainly, never all-or-nothing).[/]")
        for scope in SCOPES:
            out.print(f"  [model]{scope.label}[/]: {scope.explain}")
            enabled[scope.id] = _ask_yes_no(f"  Grant '{scope.label}'?", scope.default_enabled)

    effects = plan_scope_effects(
        enabled,
        knowledge_roots=knowledge_roots,
        auto_sync_repos=auto_sync_repos,
    )
    if dry_run:
        report["scopes"] = {"enabled": enabled, "planned": [e.__dict__ for e in effects]}
    else:
        report["scopes"] = {"enabled": enabled, **apply_scope_effects(effects)}
    out.print("[title]Permissions applied[/]" if not dry_run else "[title]Permissions planned (dry run)[/]")
    for scope in SCOPES:
        state = "[success]granted[/]" if enabled.get(scope.id) else "[dim]declined[/]"
        out.print(f"  {scope.label}: {state}")

    # ── 4. profile → seeds ──────────────────────────────────────────────────
    if profile is None:
        profile = DEFAULT_PROFILE
        if interactive:
            profile = _ask_profile(DEFAULT_PROFILE)
    if profile not in PROFILE_DESCRIPTIONS:
        out.print(f"[warn]unknown profile {profile!r}; using '{DEFAULT_PROFILE}'[/]")
        profile = DEFAULT_PROFILE

    from lhassa.config import Config

    config = Config()
    if dry_run:
        report["seeds"] = {"profile": profile, "seeds": seeds_for_profile(profile), "planned": True}
    else:
        report["seeds"] = apply_profile_seeds(profile, config)
    out.print(f"[title]Profile[/] [model]{profile}[/]; {PROFILE_DESCRIPTIONS[profile]}")
    for name in seeds_for_profile(profile):
        result = (report["seeds"].get("results") or {}).get(name, {})
        errs = result.get("errors") or ([result["error"]] if result.get("error") else [])
        if result.get("skipped"):
            out.print(f"  [dim]- seed {name}: skipped ({result['skipped']})[/]")
            continue
        mark = "[warn]✗[/]" if errs else "[success]✓[/]"
        out.print(f"  {mark} seed [model]{name}[/]" + (f": {'; '.join(errs)}" if errs else ""))

    # ── 5. services ──────────────────────────────────────────────────────────
    if no_services:
        report["services"] = {"skipped": "requested via --no-services"}
        out.print("[dim]Services: skipped (--no-services)[/]")
    else:
        repo_root = Path(__file__).resolve().parents[2]
        report["services"] = install_services(platform_name, repo_root, dry_run=dry_run)
        installed = report["services"].get("installed") or []
        enabled_units = report["services"].get("enabled") or []
        svc_errors = report["services"].get("errors") or []
        out.print(f"[title]Services[/] installed: {len(installed)}, enabled: {len(enabled_units)}")
        for e in svc_errors:
            out.print(f"  [warn]![/] {e}")

    # ── 6. smoke check ───────────────────────────────────────────────────────
    if dry_run:
        report["smoke"] = {"skipped": "dry run"}
        out.print("[dim]Smoke check: skipped (dry run)[/]")
    else:
        smoke = smoke_check(config)
        report["smoke"] = smoke
        out.print(f"[title]{smoke['sentence']}[/]")

    if json_out:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, default=str) + "\n")


def register(app: "typer.Typer") -> None:
    """Attach ``lhassa setup`` to the main app."""
    app.command("setup")(setup_cmd)
