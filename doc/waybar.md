# Waybar widget

A single status-bar icon that answers three questions, in this order:

1. **Does anything need me?** The icon colour (green, yellow, red) and a short
   counter next to it (`⚠ 2`) say so.
2. **What are my agents doing?** Active sessions of Claude Code, Codex, Orca and
   Hermes, from the agent ledger, with the repo or workspace they work in.
3. **How is the machine?** Power profile, package temperature, CPU and battery.

At rest only the icon shows. Hover it and a Waybar `drawer` opens three modules
(Agents, Attention, System). Attention hides itself when nothing needs you. It
changes nothing on your system by itself; the right-click menu is the only place
that acts, and only after a confirmation.

## How it works

```
lhassa (always-up process)  --writes when something relevant changes-->  $XDG_RUNTIME_DIR/lhassa/waybar.json
                                                                               |
        Waybar  --exec-->  scripts/waybar/lhassa-waybar  (reads the file only; menu and scroll act on request)
```

* **Writer**: `lhassa/views/waybar_state.py` (state, quiet-bar logic) and
  `lhassa/views/waybar_collect.py` (read-only collectors). The API server task
  (`lhassa serve`, `main.py`, `docker/run_api.py`) runs it in the background and
  refreshes every 15 s and right after an approval is resolved. Writes are atomic
  (temp file plus rename), the file is mode `0600` in a `0700` directory, and it is
  removed on a clean shutdown.
* **Reader**: `scripts/waybar/lhassa-waybar`, Python standard library only. It never
  imports Lhassa and never touches the network, so it starts in a few milliseconds
  and keeps working when Lhassa is down.

State file location: `$XDG_RUNTIME_DIR/lhassa/waybar.json`; without a runtime
directory, `<tmpdir>/lhassa-<uid>/waybar.json`; `LHASSA_WAYBAR_STATE_FILE`
overrides both (writer and script read the same variable).

### Quiet bar

The bar changes only when something that matters changes. The writer does not
rewrite the file for noise: temperature is bucketed to 5 C, CPU to 10 points,
battery to 5, spend to a cent and the event count to 25, and the heartbeat age is
ignored. The temperature class is latched with hysteresis (warn from 85 C until
it drops below 80 C, alert from 100 C until it drops below 95 C), so a package
hovering at the edge does not flip the icon colour. An unchanged payload is
rewritten at most every 30 s, only to refresh its timestamp so the script does not
call it stale (60 s).

### Schema

`SCHEMA_VERSION` is `2`. Every v1 key is kept, so an old reader keeps working, and
a v1 file (no `agents`/`attention`) still renders the old widget (spend and
approvals) in the new script.

| Key | Content |
| --- | --- |
| `v`, `written_at`, `instance_id`, `cost_usd`, `budget`, `pct_used`, `soft_pct`, `approvals`, `tasks`, `beat_age`, `health_failing`, `health_alert`, `dashboard_url` | As in v1. |
| `class` | The icon verdict: `ok`, `warn`, `alert`. |
| `agents` | `window_s`, `total`, `hosts` (`{"claude-code": 2}`), `sessions` (`{host, repo}` per active session). |
| `attention` | `count` and `items` (`kind`, `label`, `source`, and `id` for approvals). |
| `system` | `power_mode`, `temp_c`, `temp_level`, `cpu_pct`, `battery_pct`, `charging`, `suggestion` (reserved, always `null`: Lhassa's profile suggestion will fill it). |
| `today` | `events`, `commits`, `pushes`, `api_usd`, `top_repo`. |
| `lhassa` | `health` (worst level of the last digest), `digest_age_s`, `model` (only when the local seat is Ollama). |

## What each part shows

**Icon and counter.** `class` colours the icon: `ok` green, `warn` yellow (something
needs you, or the package is warm, or the budget is at its soft limit), `alert` red
(a failing health check at alert level, a critical review finding, a failed Lhassa
service, an exhausted budget or a hot package). `idle` (no file) and `stale` (file
older than 60 s) are grey and read "no brain". The mouse wheel changes what the
counter beside the icon shows: attention (`⚠ 2`), agents (`● 3`), temperature
(`62°`) or spend (`$0.42`). The choice lives in `waybar-mode`, a 0600 file next to
the state file.

**Agents module.** Sessions with an event in the last 5 minutes, per host
(`claude 2  codex 1`). The hosts are `claude-code`, `codex`, `orca`, `hermes` and
`omp`. Window-focus and resource-sample notes are not agent work and are ignored.

**Attention module** (hidden when empty). In priority order: pending approvals of
the brain, undecided decisions in the agent ledger, critical findings from
`lhassa review` (last 24 h), pushes with findings, GitHub Actions in red, failed
`lhassa-*` user services (`systemctl --user --failed`) and failing health checks.
Each item carries its origin.

**System module.** Power profile (from the marker `power-mode` writes), package
temperature, CPU and battery.

**Tooltip of the icon**, four sections: *Now* (agents by repo, machine),
*Needs your attention* (each item with its origin), *Today* (events, commits,
pushes, API spend, most active repo) and *Lhassa* (health, last digest, local
model; omitted when there is nothing to say). Spanish when `LHASSA_WAYBAR_LANG`
or `$LANG` starts with `es`.

### Privacy

The bar and the tooltips show only numbers and names of repos or workspaces. Never
prompt text, never a path, never a window title. A name comes from the basename of
the working directory's git root (the main tree for a linked worktree). A working
directory that the never-keep policy withholds (`~/.ssh`, `~/.lhassa`, the private
`lhassa/personal` folder, ...) or that has a `personal` path component is dropped
before any name is derived from it: the session still counts, but without a name,
and its repo is never offered for CI lookups. The same filter applies to the repos
named in the review log.

## Actions

| Gesture | Effect |
| --- | --- |
| Left click | `lhassa-waybar open`: the dashboard on `#approvals` when something waits, `#fleet` when only a check or a service is failing. |
| Right click | `lhassa-waybar menu`, a `wofi --dmenu` menu (below). |
| Mouse wheel | `lhassa-waybar scroll up|down`: cycles the counter mode and refreshes only the icon (`pkill -RTMIN+14 waybar`). |

The menu, all read-only unless it says otherwise:

* **Today's summary**: a Dunst notification (`dunstify`, else `notify-send`).
* **Approve / Deny a pending item** (only when approvals wait): pick one, confirm in
  wofi, then it runs `lhassa approve <id>` or `lhassa deny <id>`. Ids are validated
  and passed as a list, never through a shell, and the script never touches a database.
* **Open the dashboard...**: `approvals`, `fleet`, `integrity`, `overview`,
  `activity` or `settings`.
* **Pause the non-vital queue**: a visible placeholder, marked "not available yet".
  The queue and energy policy it needs does not exist; it only shows a notice.
* **Power profile...**: `saver`, `balanced` or `performance`, with a confirmation,
  through `lhassa action run power_mode --param mode=<mode>`.

### `lhassa action run`

`lhassa action run NAME [--param key=value ...] [--json]` runs one registered action
through the same gate as PTC scripts and remote tasks: it builds a signed task, the
allowlist decides (action listed, trust high enough, signature valid), an action
that needs human approval is refused here, and only then does the dispatcher run it.
`power_mode` is in the allowlist at internal trust without approval; the action
validates the mode (`^[a-z-]{1,20}$`) and calls the `power-mode` script, which
has its own passwordless sudo rule. Nothing in the widget calls that script
directly. The command exits 1 when the action is denied or reports a failure.

The command signs as the owner, so it is deliberately not a general door: only
`power_mode` is runnable from it by default. `LHASSA_ACTION_RUN_ALLOW` (comma
separated action names) widens that list; the allowlist still decides after it.

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `LHASSA_WAYBAR_STATE` | `1` | `0`/`off`/`false`/`no` stops the writer. |
| `LHASSA_WAYBAR_STATE_FILE` | unset | Explicit state file path (writer and script). |
| `LHASSA_WAYBAR_STALE_S` | `60` | Seconds before the script reports `stale`. |
| `LHASSA_WAYBAR_LANG` | `$LANG` | `es` or `en` for the widget texts. |
| `LHASSA_WAYBAR_MODE_FILE` | next to the state file | Where the counter mode is kept. |
| `LHASSA_WAYBAR_CI_REPOS` | unset | Comma list of `owner/repo` whose GitHub Actions to watch (at most 8). |
| `LHASSA_WAYBAR_CI_AUTO` | `1` | Without a list, watch the GitHub repos of recent ledger sessions; `0` turns that off. |
| `AGENT_LEDGER_DB` | `~/.agent-ledger/ledger.db` | The ledger, opened read-only. |
| `LHASSA_REVIEW_DIR` | `~/.lhassa/review` | Where `review.jsonl` lives, read-only. |
| `LHASSA_BIN` | `lhassa` on `PATH` | The command the menu calls. |

**CI** is looked up by the writer, never by the script: `gh run list` per repo,
cached for 5 minutes, in a worker thread with a timeout, so the loop never waits for
the network. Without `gh`, or without network, the CI items are simply absent. `gh`
is found on `PATH` or in `~/.local/bin`.

## Install

1. Copy or symlink the script, for example to `~/.local/bin/lhassa-waybar`
   (it is `scripts/waybar/lhassa-waybar` in the repo). `lhassa` must be on `PATH`
   for the menu, and `wofi` (and `dunstify` or `notify-send`) for the menu and
   the summary.
2. Add the modules to `modules.json`. Waybar real-time signals in use here: `14` icon,
   `15` attention, `16` system. Pick numbers that your other modules do not use.

```json
"custom/lhassa": {
  "exec": "~/.local/bin/lhassa-waybar status",
  "return-type": "json",
  "interval": 15,
  "signal": 14,
  "on-click": "~/.local/bin/lhassa-waybar open",
  "on-click-right": "~/.local/bin/lhassa-waybar menu",
  "on-scroll-up": "~/.local/bin/lhassa-waybar scroll up",
  "on-scroll-down": "~/.local/bin/lhassa-waybar scroll down",
  "tooltip": true
},
"custom/lhassa-agents": {
  "exec": "~/.local/bin/lhassa-waybar agents",
  "return-type": "json",
  "interval": 15,
  "on-click": "~/.local/bin/lhassa-waybar open activity",
  "tooltip": true
},
"custom/lhassa-attention": {
  "exec": "~/.local/bin/lhassa-waybar attention",
  "return-type": "json",
  "interval": 15,
  "signal": 15,
  "on-click": "~/.local/bin/lhassa-waybar open",
  "tooltip": true
},
"custom/lhassa-system": {
  "exec": "~/.local/bin/lhassa-waybar system",
  "return-type": "json",
  "interval": 15,
  "signal": 16,
  "on-click": "~/.local/bin/lhassa-waybar open fleet",
  "tooltip": true
},
"group/lhassa": {
  "orientation": "inherit",
  "drawer": {
    "transition-duration": 300,
    "children-class": "lhassa-child",
    "transition-left-to-right": false
  },
  "modules": [
    "custom/lhassa",
    "custom/lhassa-agents",
    "custom/lhassa-attention",
    "custom/lhassa-system"
  ]
}
```

3. `config.jsonc`: nothing changes if `"group/lhassa"` is already in one of the
   `modules-left`/`modules-center`/`modules-right` lists. Otherwise add it there:

```jsonc
"modules-right": [
  "group/hardware",
  "group/lhassa",
  "group/net"
]
```

4. `style.css`: the group is `#lhassa`; the icon is `#custom-lhassa` and the children are
   `#custom-lhassa-agents`, `#custom-lhassa-attention` and `#custom-lhassa-system`.
   Colours below use the Catppuccin names from `colors.css`; swap in your own.

```css
#lhassa #custom-lhassa {
  background-color: transparent;
  padding: 5px 8px;
}
#lhassa #custom-lhassa.ok     { color: @green; }
#lhassa #custom-lhassa.warn   { color: @yellow; }
#lhassa #custom-lhassa.alert  { color: @red; }
#lhassa #custom-lhassa.idle,
#lhassa #custom-lhassa.stale  { color: @overlay1; }

#lhassa #custom-lhassa-agents,
#lhassa #custom-lhassa-attention,
#lhassa #custom-lhassa-system {
  background-color: transparent;
  padding: 5px 8px;
  color: @subtext0;
}
#lhassa #custom-lhassa-agents.idle { color: @overlay1; }
#lhassa #custom-lhassa-attention.warn,
#lhassa #custom-lhassa-system.warn  { color: @yellow; }
#lhassa #custom-lhassa-attention.alert,
#lhassa #custom-lhassa-system.alert { color: @red; }
```

5. Reload Waybar (`pkill -SIGUSR2 waybar`). `pkill -RTMIN+14 waybar` forces an
   immediate redraw of the icon alone.

## Reverting

1. In `modules.json` restore the single-module group: delete `custom/lhassa-agents`,
   `custom/lhassa-attention` and `custom/lhassa-system`, delete the `drawer` block and
   the three extra names from `group/lhassa`, and set the icon's `on-click-right` back
   to `open fleet` (or drop it) and remove its `on-scroll-up`/`on-scroll-down`.
2. In `style.css` delete the `#custom-lhassa-agents`/`-attention`/`-system` rules.
   `config.jsonc` needs no change. Reload Waybar.
3. To remove the widget altogether, also delete `custom/lhassa` and `group/lhassa`,
   remove `"group/lhassa"` from the modules list and stop the writer with
   `LHASSA_WAYBAR_STATE=0` plus a restart of Lhassa; remove
   `~/.local/bin/lhassa-waybar` if you copied it.

The widget keeps no database table, service or setting. The only files are the state
file and `waybar-mode` in the runtime directory (gone at logout), and
`lhassa action run` is an ordinary subcommand that does nothing unless called.

## Tests

`tests/test_views_waybar_state.py` (payload v1 and v2, atomic 0600 write, verdict,
hysteresis, bucketing, quiet bar, heartbeat, privacy end to end),
`tests/test_views_waybar_collect.py` (a synthetic ledger and review log, systemctl and
`gh` faked, CI cache, read-only access, private directories),
`tests/test_waybar_script.py` (JSON per state, v1 compatibility, modules, scroll
modes, the menu with wofi and `lhassa` faked) and `tests/test_cli_action.py` (the
governed action). All offline and hermetic: temporary directories only, never the
real ledger, state file or network.
