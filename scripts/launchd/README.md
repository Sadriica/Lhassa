# launchd agents — Lhassa on macOS (tier 2)

The macOS counterparts of `scripts/systemd/`. Install as **user agents** (never
system-wide daemons), exactly like the systemd units are user units:

```bash
cp scripts/launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.lhassa.heartbeat.plist
launchctl list | grep com.lhassa
```

Edit `WorkingDirectory` / `ProgramArguments` if your checkout is not at
`~/Code/lhassa`. `launchctl unload <plist>` stops an agent.

## The mapping is approximate, not equivalent

`StartInterval` is the closest thing launchd has to a systemd timer, and it is
**not the same contract**:

| systemd | launchd | difference |
|---|---|---|
| `OnUnitActiveSec=2min` | `StartInterval: 120` | systemd measures from the END of the last run; launchd fires every N seconds regardless of whether the previous run finished |
| `Persistent=true` | *(no equivalent)* | systemd replays a run missed while the machine was off; launchd does not |
| `OnBootSec=1min` | `RunAtLoad: true` | close enough — launchd runs once at load |
| `AccuracySec=15s` | *(no equivalent)* | launchd gives no coalescing window |

A sleeping Mac makes the second row matter: an interval job fires on wake, so
every overdue schedule lands at once. Lhassa's own recurrence arithmetic
(`core/step.py:next_recurrence_due`) already advances in whole intervals and
keeps a schedule on its wall-clock slot across suspend, so this is safe — but
the label on the tin is "approximate", and it is written down here rather than
assumed.

The `com.lhassa.*` label prefix is load-bearing: `health/checks.py`'s failed
service check only considers labels under it, so an unrelated launchd job is
never reported as Lhassa's problem.
