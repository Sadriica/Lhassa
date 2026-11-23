# Observers (`lhassa-observe`)

Read-only observers that write to Lhassa's history. They look at the machine and
record what they see; nothing acts on the system, nothing touches the network,
nothing needs sudo, and nothing is observed until you switch it on.

```
system  /proc, /sys        -> resource_sample   every 30 s
sway    $SWAYSOCK (IPC)    -> window_focus, workspace_focus
shell   bash/zsh hook      -> shell_cmd
files   inotify (ctypes)   -> file_event        only folders you declare
```

Every event goes through one door, `ObserveWriter`, which applies the never-keep
policy (`lhassa.record.never_keep`) before the event is written and fails closed:
a path or text that touches `~/.ssh`, `~/.lhassa`, `.env`, `*.kdbx`... is replaced
by `<withheld>` with the rule in `withheld`; an event that cannot be evaluated is
dropped. Logs are `0600` JSONL (`lhassa.record.EventLog`), one per observer and
day: `<records_dir>/<instance>/observe/<observer>/<YYYY-MM-DD>.jsonl`
(`records_dir` defaults to `~/.lhassa/records`).

## Turning it on

Nothing runs by default. Either pass flags or write `~/.lhassa/observe.json`
(`LHASSA_OBSERVE_CONFIG` overrides the path; `lhassa observe example-config`
prints a template):

```json
{
  "enabled": ["system", "sway", "shell", "files"],
  "system": {"interval": 30},
  "sway":   {"titles": false},
  "shell":  {"full_command": false},
  "files":  {"folders": [{"path": "~/notes", "recursive": true, "max_depth": 2}],
             "max_watches": 256}
}
```

```bash
lhassa observe run --system --sway          # enable by flag
lhassa observe run                          # whatever the file enables
lhassa observe status [--json]              # what is alive, when each last wrote
```

Flags can enable observers and tune them; folders to watch can only be declared in
the file. A missing or malformed file means "observe nothing".

## The observers

**system.** CPU busy percent (delta between samples), memory and swap use, load
average, temperatures (thermal zones and hwmon, maximum plus a small per-sensor
map), battery percent / status / watts and AC state. Interval 5 s to 3600 s,
default 30 s. Parsers are pure functions over `/proc` and `/sys` text.

**sway.** Subscribes to `window` and `workspace` events on `$SWAYSOCK` (stdlib
socket, `i3-ipc` framing) and records `window_focus` with `app_id` or X11 `class`
and the workspace, plus `workspace_focus`. Window titles are dropped by default,
since they carry private data (mail subjects, file names, URLs); `"titles": true`
or `--sway-titles` keeps them after the privacy scrubber and never-keep. It is the
input for a future power-by-context policy. No `$SWAYSOCK` means inactive, not an
error; a lost connection is retried with backoff (1 s up to 60 s).

**shell.** See below.

**files.** inotify through `ctypes` on the declared folders only (no fanotify, no
eBPF). Events: `create`, `modify` (coalesced to one per 2 s per file), `delete`,
`move`, `overflow`, with the path relative to the declared folder. Bounds: a cap
on watches shared by all folders (`max_watches`, default 256, hard limit 4096);
recursion only when a folder asks for it, up to `max_depth` (default 2, hard limit
8), never through symlinks, skipping `.git`, `node_modules`, `__pycache__`,
`.venv`, `.cache`. A folder or sub-folder covered by never-keep is not watched. A
file whose path is covered keeps its event with `path: "<withheld>"`.

## The shell hook

Technique. A hook cannot start Python for every command, and bash cannot speak
`AF_UNIX` datagrams with builtins alone. So the receiver (inside `observe run`)
listens on two private doors in `$XDG_RUNTIME_DIR/lhassa-observe/` (else
`/tmp/lhassa-observe-<uid>/`; `LHASSA_OBSERVE_DIR` overrides), a directory it
refuses to use unless it is yours and mode `0700`:

* `shell.fifo` (`0600`): the hook writes one line per command with the `printf`
  builtin into a FIFO it opens read-write (`1<>fifo`, which never blocks on a
  missing reader). A line under `PIPE_BUF` is written atomically, so a record acts
  as a datagram. **This is what the bash and zsh hooks use**: no fork, no exec.
* `shell.sock` (`0600`): a real `AF_UNIX` datagram socket carrying the same record,
  for any client that can use one (`socat`, `nc -U -u`, Python).

Record (fields joined by `0x1f`, ended by a newline):
`L1 <shell> <cwd> <exit> <duration_ms> <first-word> <full-line>`. The hook sends
the first word only; `FOO=secret cmd` arrives as `<assign>`, paths keep their base
name, and anything unusual becomes `<other>`. The full line is empty unless you opt
in at both ends: `LHASSA_OBSERVE_FULL=1` in the shell and `"full_command": true`
(or `--shell-full`) in the observer; it is then scrubbed by the privacy module and
withheld if any token touches a never-keep path. The hook stays silent when the
receiver is not running (it checks the receiver's pid file, one `read` and one
`stat` per prompt), so a stopped observer cannot make a prompt block.

Install by hand (this project never edits your rc files). In `~/.bashrc`:

```bash
# lhassa observe (interactive shells only; silent when the observer is not running)
source ~/Code/witara/products/lhassa/app/packages/lhassa-observe/src/lhassa/observe/hooks/observe.bash
```

`lhassa observe hook bash` prints the same text. For zsh, source `observe.zsh`
(`lhassa observe hook zsh`) from `~/.zshrc`; the zsh hook follows the zsh manual
but was not exercised on the development machine (no zsh installed), so test it
before relying on it. Notes: the bash hook installs a `DEBUG` trap (it replaces an
existing one) and prepends/appends to `PROMPT_COMMAND`; bash 5 is recommended
(`EPOCHREALTIME`). Measured cost: about 80 microseconds per command at a burst of
3000 commands per second; measured with 3000 commands piped into an interactive bash.

## Running it as a service

`scripts/systemd/lhassa-observe.service` is an example user unit (not installed
by anything). Paths use the Witara layout (`%h/Code/witara/products/lhassa/app`).

```bash
mkdir -p ~/.config/systemd/user
cp scripts/systemd/lhassa-observe.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lhassa-observe.service
lhassa observe status
```

For the sway observer the user manager needs `SWAYSOCK`
(`systemctl --user import-environment SWAYSOCK` from your sway config).

## Events

| `t` | Fields |
| --- | --- |
| `resource_sample` | `cpu_pct`, `mem_used_pct`, `mem_avail_mb`, `swap_used_mb`, `swap_pct`, `load1/5/15`, `temp_max_c`, `temps`, `bat_pct`, `bat_status`, `power_w`, `ac` (each present only when readable) |
| `window_focus` | `app_id` and/or `class`, `workspace`, `title` only when enabled |
| `workspace_focus` | `workspace` |
| `shell_cmd` | `shell`, `cwd`, `rc`, `dur_ms`, `cmd`, `line` only when enabled |
| `file_event` | `type`, `root`, `path` (relative), `dir`, `from` (moves) |

Common envelope (from `lhassa.record`): `v`, `run`, `source` (`observe.<name>`),
`seq`, `ts`, `t`.

## Limits and known risks

* The FIFO holds 64 KiB. If the receiver dies and its pid is reused by another
  process, a shell could fill the FIFO and block on its next prompt. The pid file
  is removed on a clean stop and re-checked on every prompt; a hard crash plus pid
  reuse is the residual case.
* The never-keep verdict is cached for 30 s per string (keyed by `HOME` and the
  extra-rules file), so a rule added while running applies within half a minute.
* The system sampler spends most of its time reading `thermal`/`hwmon` files
  (tens of milliseconds of wall time per sample on the development laptop); it is
  negligible at one sample per 30 s but not free at 5 s.
* Only Linux paths are implemented; elsewhere the observers report `inactive`.
