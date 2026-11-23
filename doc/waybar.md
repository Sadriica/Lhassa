# Waybar widget

A small status-bar widget that shows Lhassa at a glance: today's spend as the
main number, and a colour that turns when something needs you (approvals
waiting, a high budget, a failing health check). Clicking it opens the local
dashboard. It changes nothing on your system.

## How it works

```
lhassa (always-up process)  --writes every ~15 s-->  $XDG_RUNTIME_DIR/lhassa/waybar.json
                                                              |
                          Waybar  --exec-->  scripts/waybar/lhassa-waybar  (reads the file only)
```

* **Writer**: `lhassa/views/waybar_state.py`. The API server task
  (`lhassa serve`, `main.py`, `docker/run_api.py`) runs it in the background.
  It refreshes every 15 s and right after an approval is resolved. Writes are
  atomic (temp file plus rename), the file is mode `0600` in a `0700`
  directory, and the file is removed on a clean shutdown.
* **Reader**: `scripts/waybar/lhassa-waybar`, Python standard library only.
  It never imports Lhassa and never touches the network, so it starts in a few
  milliseconds and keeps working when Lhassa is down.

State file location: `$XDG_RUNTIME_DIR/lhassa/waybar.json`; without a runtime
directory, `<tmpdir>/lhassa-<uid>/waybar.json`; `LHASSA_WAYBAR_STATE_FILE`
overrides both (writer and script read the same variable).

## States

| `class` | Meaning |
| --- | --- |
| `ok` | Running, nothing needs you. |
| `warn` | Approvals are waiting, or today's budget is at its soft limit. |
| `alert` | A health check is failing, or the daily budget is used up. |
| `stale` | The file is older than 60 s: Lhassa stopped updating it. Text: "no brain". |
| `idle` | No file, or an unreadable one: Lhassa is not running. Text: "no brain". |

The text is `<wheel> $0.42`, with ` · N` appended when `N` approvals wait. The
tooltip lists spend, budget used, approvals, queued tasks and failing checks.

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `LHASSA_WAYBAR_STATE` | `1` | `0`/`off`/`false`/`no` stops the writer. |
| `LHASSA_WAYBAR_STATE_FILE` | unset | Explicit state file path (writer and script). |
| `LHASSA_WAYBAR_STALE_S` | `60` | Seconds before the script reports `stale`. |
| `LHASSA_WAYBAR_LANG` | `$LANG` | `es` or `en` for the widget texts. |

## Install

1. Copy or symlink the script somewhere on disk, for example
   `~/.local/bin/lhassa-waybar` (it is `scripts/waybar/lhassa-waybar` in the repo).
2. Add the module to your Waybar `modules.json`, in its own group so it does not
   crowd other modules:

```json
"custom/lhassa": {
  "exec": "~/.local/bin/lhassa-waybar status",
  "return-type": "json",
  "interval": 15,
  "signal": 14,
  "on-click": "~/.local/bin/lhassa-waybar open",
  "tooltip": true
},
"group/lhassa": {
  "orientation": "inherit",
  "modules": ["custom/lhassa"]
}
```

3. Put `"group/lhassa"` in the `modules-left`/`modules-center`/`modules-right`
   list you want, then add the styles to `style.css`:

```css
#custom-lhassa {
  padding: 0 8px;
}
#custom-lhassa.ok     { color: #a3be8c; }
#custom-lhassa.warn   { color: #ebcb8b; }
#custom-lhassa.alert  { color: #bf616a; }
#custom-lhassa.idle,
#custom-lhassa.stale  { color: #6c7086; }
```

4. Reload Waybar (`pkill -SIGUSR2 waybar`). `signal` is optional: with it,
   `pkill -RTMIN+14 waybar` forces an immediate redraw. Pick a number not used by
   your other modules.

The click action runs `xdg-open` on the dashboard URL the writer recorded
(`http://127.0.0.1:7766` by default, from `LHASSA_API_HOST`/`LHASSA_API_PORT`).
Only loopback URLs are honoured; anything else falls back to the default.

## Reverting

1. Delete the `custom/lhassa` and `group/lhassa` entries from `modules.json`,
   remove `"group/lhassa"` from the modules list, and delete the `#custom-lhassa`
   rules from `style.css`. Reload Waybar.
2. Optionally stop the writer with `LHASSA_WAYBAR_STATE=0` and restart Lhassa;
   remove `~/.local/bin/lhassa-waybar` if you copied it.

Nothing else is touched: there is no database table, no service and no setting
to undo.

## Tests

`tests/test_views_waybar_state.py` (payload, location, atomic 0600 write, loop,
refresh on request) and `tests/test_waybar_script.py` (JSON per state, stale,
corrupt and missing file, budget, approvals, escaping, speed). All offline and
hermetic: they use temporary directories and never read the real state file.
