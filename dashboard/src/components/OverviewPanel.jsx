import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from '../lib/toast'

// The Overview home — a live COMMAND CENTER, top-to-bottom:
//   now running → do-and-sleep pulse → key stats (clickable) → approvals → smart activity.
//
//   GET /api/overview → {
//     brains, skills, approvals:{pending,items[]}, cost, health, tools, errors,
//     activity:{recent[], total, last_24h, recent_limit, window},
//     now_running:[{id,name,cli,task,status,started_at}],   // agentic runs in flight
//     pulse:{last_beat, mode:'online'|'offline', alive},    // do-and-sleep pulse
//     flows:{count, last_status}, runs_today:{done,stopped,failed},
//   }
// Polled every ~10s. In addition we open a lightweight /ws listener (the same
// fan-out App.jsx uses) so agent_started/token/done/stopped/error events refresh
// the "Now running" strip immediately — a poll would lag by up to 10s. A 1s local
// ticker drives the live elapsed timers. Approvals resolve inline against the same
// endpoint the ApprovalQueuePanel uses (POST /api/approvals/{id} {approved});
// a per-run Stop hits POST /api/code/{id}/stop. Defensive throughout: a partial/failed
// section surfaces subtly under `errors` and never blocks the rest of the home.

const POLL_MS = 10000
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`

function fmtUsd(n) {
  const v = Number(n || 0)
  if (v === 0) return '$0.00'
  if (v < 0.01) return `$${v.toFixed(4)}`
  return `$${v.toFixed(2)}`
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString()
}

function clampPct(n, total) {
  const value = Number(n || 0)
  const max = Number(total || 0)
  if (!max) return 0
  return Math.max(0, Math.min(100, Math.round((value / max) * 100)))
}

// "ai-cli (claude)" → "claude"; falls back to the whole name.
function cliOf(name) {
  const m = /\(([^)]+)\)/.exec(name || '')
  return m ? m[1] : name || '?'
}

// Live elapsed since an epoch-seconds start, e.g. "1m 12s" / "8s".
function fmtElapsed(startSec, nowMs) {
  if (!startSec) return null
  const s = Math.max(0, Math.floor(nowMs / 1000 - startSec))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s - m * 60}s`
}

// Parse a stored timestamp (ISO string or epoch seconds) → ms, or null.
function toMs(ts) {
  if (ts == null) return null
  if (typeof ts === 'number') return ts * 1000
  const t = Date.parse(ts)
  return Number.isNaN(t) ? null : t
}

// A relative "2m ago" for the pulse's last beat.
function fmtRelative(ts, nowMs) {
  const ms = toMs(ts)
  if (ms == null) return null
  const s = Math.round((nowMs - ms) / 1000)
  if (s < 0) return 'just now'
  if (s < 45) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

// worst_level → a dot class + a label tone.
function healthDot(level, ok) {
  const l = String(level || '').toLowerCase()
  if (l === 'critical' || l === 'alert' || l === 'error') return 'lh-dot-alert'
  if (l === 'warn' || l === 'warning') return 'lh-dot-warn'
  if (ok || l === 'ok' || l === 'info' || l === '') return 'lh-dot-ok'
  return 'lh-dot-warn'
}

// ── a compact stat card — clickable navigates to `hash` (the app's hash router) ──
function StatCard({ label, value, sub, children, accent, pct, hash }) {
  const clickable = Boolean(hash)
  const go = () => { if (hash) window.location.hash = hash }
  return (
    <div
      className="lh-card"
      style={{ padding: '0.85rem 0.95rem', ...(clickable ? { cursor: 'pointer' } : {}) }}
      {...(clickable
        ? {
            role: 'button',
            tabIndex: 0,
            onClick: go,
            onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go() } },
            title: `Open ${label}`,
          }
        : {})}
    >
      <div className="lh-eyebrow flex items-center justify-between">
        <span>{label}</span>
        {clickable && <span style={{ color: 'var(--lh-text-faint)' }} aria-hidden="true">→</span>}
      </div>
      <div
        className="mt-1.5 font-bold lh-mono"
        style={{ fontSize: 26, lineHeight: 1, color: accent || 'var(--lh-text)' }}
      >
        {value}
      </div>
      {sub && (
        <div className="text-[11px] mt-1.5" style={{ color: 'var(--lh-text-faint)' }}>{sub}</div>
      )}
      {typeof pct === 'number' && (
        <div className="lh-metric-bar mt-3" aria-hidden="true"><span style={{ width: `${pct}%` }} /></div>
      )}
      {children}
    </div>
  )
}

// ── smart activity: collapse routine offline ticks, flag important events ────────
// An event is "important" if it looks like an approval, error, completed flow, or an
// agentic/stopped run — anything the owner would actually want to notice. Everything
// else (mostly repetitive "[offline] … department handled" beats) is routine noise.
const IMPORTANT_RE = /(approv|error|fail|denied|blocked|escalat|stopped|flow|agent|graduat|kill.?switch)/i

function isImportant(ep) {
  const hay = `${ep.despertar_por || ''} ${ep.decision || ''} ${ep.resultado || ''}`
  return IMPORTANT_RE.test(hay)
}

// A stable signature for "the same routine tick" — group consecutive identical ones.
function routineKey(ep) {
  return `${ep.despertar_por || ''}|${ep.decision || ''}`
}

// Collapse runs of consecutive near-identical routine (non-important) episodes into
// one grouped row carrying a count; important events always stand alone.
function groupActivity(recent) {
  const out = []
  for (const ep of recent) {
    const important = isImportant(ep)
    const prev = out[out.length - 1]
    if (!important && prev && !prev.important && prev.key === routineKey(ep)) {
      prev.count += 1
      continue
    }
    out.push({ ...ep, important, key: routineKey(ep), count: 1 })
  }
  return out
}

export default function OverviewPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(null) // approval id being resolved
  const [resolveErr, setResolveErr] = useState('')
  const [stopping, setStopping] = useState(null) // run id being stopped
  const [now, setNow] = useState(Date.now())
  const [importantOnly, setImportantOnly] = useState(true)
  const mounted = useRef(true)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/overview')
      if (!r.ok) throw new Error('overview fetch failed')
      const d = await r.json()
      if (mounted.current) { setData(d); setError('') }
    } catch {
      if (mounted.current) setError('Could not load the overview.')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    load()
    const t = setInterval(load, POLL_MS)
    return () => { mounted.current = false; clearInterval(t) }
  }, [load])

  // Live refresh: subscribe to the same /ws fan-out the app uses. Any agent lifecycle
  // event means the "Now running" strip changed, so refetch (debounced) at once —
  // otherwise the strip would lag behind the run by up to a full poll interval. This
  // is best-effort: if the socket never opens, the 10s poll still keeps things fresh.
  useEffect(() => {
    let ws
    let debounce
    const refetch = () => { clearTimeout(debounce); debounce = setTimeout(() => load(), 300) }
    try {
      ws = new WebSocket(WS_URL)
      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data)
          if (typeof ev?.type === 'string' && ev.type.startsWith('agent_')) refetch()
        } catch { /* ignore malformed frames */ }
      }
      ws.onerror = () => { /* poll fallback covers it */ }
    } catch { /* WS unavailable — poll fallback covers it */ }
    return () => { clearTimeout(debounce); ws?.close() }
  }, [load])

  const resolve = async (id, approved) => {
    setBusy(id)
    setResolveErr('')
    try {
      const r = await fetch(`/api/approvals/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Resolve failed')
      }
      await load() // refetch so the home reflects the resolved queue
    } catch (e) {
      setResolveErr(e instanceof Error ? e.message : 'Resolve failed')
    } finally {
      setBusy(null)
    }
  }

  // Stop a running agentic run inline (same endpoint the Agentic panel uses).
  const stopRun = async (id) => {
    if (stopping) return
    setStopping(id)
    try {
      const r = await fetch(`/api/code/${id}/stop`, { method: 'POST' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.error || d.detail || 'Stop failed')
      }
      toast.success('Stopping run…')
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Stop failed')
    } finally {
      setStopping(null)
    }
  }

  const nowRunning = data?.now_running || []
  const pulse = data?.pulse || {}

  // Tick the elapsed/relative clocks once a second while something is in flight or
  // the pulse card is showing a "last beat" — otherwise stay idle (no churn).
  const needsClock = nowRunning.length > 0 || pulse.last_beat != null
  useEffect(() => {
    if (!needsClock) return undefined
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [needsClock])

  const recent = data?.activity?.recent
  const grouped = useMemo(() => groupActivity(recent || []), [recent])
  const shown = useMemo(
    () => (importantOnly ? grouped.filter((g) => g.important) : grouped),
    [grouped, importantOnly],
  )
  const importantCount = useMemo(() => grouped.filter((g) => g.important).length, [grouped])
  const activityTotal = Number(data?.activity?.total ?? grouped.length)
  const activityWindowCount = Number(data?.activity?.last_24h ?? grouped.length)
  const recentLimit = Number(data?.activity?.recent_limit ?? grouped.length)

  if (loading) {
    return (
      <div className="lh-card lh-command-hero">
        <div className="lh-eyebrow">Admin console</div>
        <h2 className="lh-display-title mt-2">System overview</h2>
        <p className="text-xs mt-3" style={{ color: 'var(--lh-text-faint)' }}>Loading the overview…</p>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="lh-card lh-command-hero">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="lh-eyebrow">Admin console</div>
            <h2 className="lh-display-title mt-2">System overview</h2>
          </div>
          <button type="button" className="lh-btn" onClick={load}>Retry</button>
        </div>
        <div className="lh-row" style={{ borderColor: 'rgba(162,62,52,0.45)', color: 'var(--lh-alert)' }}>{error}</div>
      </div>
    )
  }

  const brains = data?.brains || {}
  const skills = data?.skills || {}
  const approvals = data?.approvals || { pending: 0, items: [] }
  const cost = data?.cost || {}
  const health = data?.health || {}
  const tools = data?.tools || { count: 0, by_estado: {} }
  const flows = data?.flows || { count: 0, last_status: null }
  const runsToday = data?.runs_today || { done: 0, stopped: 0, failed: 0 }
  const errors = data?.errors || {}
  const errorKeys = Object.keys(errors)
  const items = approvals.items || []
  const estados = Object.entries(tools.by_estado || {})

  const budget = cost.daily_token_budget
  const budgetLabel = !budget || budget === 0
    ? 'unlimited budget'
    : `${fmtInt(cost.output_tokens_today)} / ${fmtInt(budget)} out tok`
  const budgetPct = budget ? clampPct(cost.output_tokens_today, budget) : 18

  const lastBeatRel = fmtRelative(pulse.last_beat, now)

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="lh-card lh-command-hero lh-precision-grid">
        <div className="grid grid-cols-1 lg:grid-cols-[1.55fr_0.9fr] gap-4 items-stretch">
          <div className="flex flex-col justify-between min-h-[200px] relative z-10">
            <div>
              <div className="lh-kicker">Lhasa / Lhassa</div>
              <h2 className="lh-display-title mt-3">Admin console</h2>
              <p className="text-[12px] mt-3 max-w-xl" style={{ color: 'var(--lh-text-muted)' }}>
                Decisions, live work, autonomy health, cost, and the latest system changes in one place.
              </p>
            </div>
            <div className="flex items-center gap-2 flex-wrap mt-5">
              {data?.instance_id && (
                <span className="lh-pill lh-mono text-[10px]">
                  {data.instance_id}
                </span>
              )}
              <button type="button" className="lh-btn lh-btn-primary" onClick={() => { window.location.hash = 'inbox' }}>
                Open inbox
              </button>
              <button type="button" className="lh-btn" onClick={() => { window.location.hash = 'watchlist' }}>
                Watchlist
              </button>
              <button type="button" className="lh-btn" onClick={() => { window.location.hash = 'capabilities' }}>
                Capabilities
              </button>
              <button type="button" className="lh-btn" onClick={() => { window.location.hash = 'run' }}>
                Delegate
              </button>
              <button type="button" className="lh-btn" onClick={load}>Refresh</button>
            </div>
            <div className="mt-5 lh-rule" />
          </div>
          <div className="grid grid-cols-2 gap-3 relative z-10">
            <div className="lh-nav-stat">
              <div className="lh-nav-stat-label">Running now</div>
              <div className="lh-nav-stat-value">{nowRunning.length} running</div>
              <div className="lh-nav-stat-label mt-2">agentic runs</div>
              <div className="lh-metric-bar mt-2" aria-hidden="true"><span style={{ width: `${Math.min(100, nowRunning.length * 33)}%` }} /></div>
            </div>
            <div className="lh-nav-stat">
              <div className="lh-nav-stat-label">Readiness</div>
              <div className="lh-nav-stat-value">{health.ok ? 'All clear' : (health.worst_level || 'Check')}</div>
              <div className="lh-nav-stat-label mt-2">worst level</div>
              <div className="lh-metric-bar mt-2" aria-hidden="true"><span style={{ width: `${health.ok ? 100 : 48}%` }} /></div>
            </div>
            <div className="lh-nav-stat">
              <div className="lh-nav-stat-label">Decisions</div>
              <div className="lh-nav-stat-value">{approvals.pending} pending</div>
              <div className="lh-nav-stat-label mt-2">queue pressure</div>
              <div className="lh-metric-bar mt-2" aria-hidden="true"><span style={{ width: `${Math.min(100, approvals.pending * 25)}%` }} /></div>
            </div>
            <div className="lh-nav-stat">
              <div className="lh-nav-stat-label">Budget</div>
              <div className="lh-nav-stat-value">{budgetLabel}</div>
              <div className="lh-nav-stat-label mt-2">spent today</div>
              <div className="lh-metric-bar mt-2" aria-hidden="true"><span style={{ width: `${budgetPct}%` }} /></div>
            </div>
          </div>
        </div>
      </div>

      {/* 1 — Now running strip */}
      <div className="lh-card" style={nowRunning.length > 0 ? { borderColor: 'rgba(217,165,33,0.3)' } : undefined}>
        <div className="lh-card-head">
          <div className="flex items-center gap-2">
            <span className="lh-eyebrow">Now running</span>
            {nowRunning.length > 0 ? (
              <span className="lh-tag lh-tag-accent">{nowRunning.length} in flight</span>
            ) : (
              <span className="lh-tag">idle</span>
            )}
          </div>
        </div>
        {nowRunning.length === 0 ? (
          <div className="lh-row flex items-center gap-2" style={{ color: 'var(--lh-text-faint)' }}>
            <span className="lh-dot lh-dot-ok" />
            <span className="text-xs">Idle — nothing in flight.</span>
          </div>
        ) : (
          <ul className="space-y-2">
            {nowRunning.map((run) => {
              const elapsed = fmtElapsed(run.started_at, now)
              return (
                <li key={run.id} className="lh-row">
                  <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="lh-dot lh-dot-accent animate-pulse" />
                        <span className="lh-mono text-[11px]" style={{ color: 'var(--lh-accent-text)' }}>#{run.id}</span>
                        <span className="lh-tag lh-tag-accent">{cliOf(run.name || run.cli)}</span>
                      </div>
                      {run.task && (
                        <div className="text-[12px] mt-1 truncate max-w-lg" style={{ color: 'var(--lh-text-muted)' }} title={run.task}>
                          {run.task}
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {elapsed && (
                        <span className="lh-mono text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>{elapsed}</span>
                      )}
                      <button
                        type="button"
                        className="lh-btn lh-btn-danger"
                        disabled={stopping === run.id}
                        onClick={() => stopRun(run.id)}
                        title="Terminate this running task"
                      >
                        {stopping === run.id ? 'Stopping…' : '■ Stop'}
                      </button>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* 2 — Autonomy heartbeat + automation summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="lh-card">
          <div className="lh-card-head">
            <span className="lh-eyebrow">Autonomy heartbeat</span>
            <span className="inline-flex items-center gap-1.5">
              <span className={`lh-dot ${pulse.alive ? 'lh-dot-ok animate-pulse' : 'lh-dot-warn'}`} />
              <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
                {pulse.alive ? 'alive' : 'quiet'}
              </span>
            </span>
          </div>
          <div className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
            <div className="flex items-center justify-between gap-2">
              <span className="lh-label">last check-in</span>
              <span className="lh-mono text-[12px]" style={{ color: 'var(--lh-text)' }}>
                {lastBeatRel || '—'}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2 mt-2">
              <span className="lh-label">autonomy mode</span>
              <span className="lh-pill lh-mono text-[10px]" style={pulse.mode === 'online' ? { color: 'var(--lh-green)' } : undefined}>
                <span className={`lh-dot ${pulse.mode === 'online' ? 'lh-dot-ok' : 'lh-dot-warn'}`} />
                {pulse.mode || 'offline'}
              </span>
            </div>
            {/* next_tick is intentionally omitted — the heartbeat cadence lives in the
                systemd timer, not in config, so we never fabricate a time. */}
          </div>
        </div>

        <div className="lh-card">
          <div className="lh-card-head">
            <span className="lh-eyebrow">Automation &amp; runs</span>
            <button type="button" className="lh-tag" onClick={() => { window.location.hash = 'flows' }} style={{ cursor: 'pointer' }}>open flows →</button>
          </div>
          <div className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
            <div className="flex items-center justify-between gap-2">
              <span className="lh-label">flows</span>
              <span className="lh-mono text-[12px]" style={{ color: 'var(--lh-text)' }}>
                {fmtInt(flows.count)} defined
                {flows.last_status ? ` · last ${flows.last_status}` : ''}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2 mt-2">
              <span className="lh-label">runs today</span>
              <span className="flex items-center gap-1.5 flex-wrap justify-end">
                <span className="lh-tag"><span className="lh-dot lh-dot-ok" /> {runsToday.done} done</span>
                <span className="lh-tag"><span className="lh-dot lh-dot-warn" /> {runsToday.stopped} stopped</span>
                <span className="lh-tag"><span className="lh-dot lh-dot-alert" /> {runsToday.failed} failed</span>
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* 3 — Stat cards (clickable → the app's hash router switches section) */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
        <StatCard
          label="Brains"
          hash="brains"
          value={fmtInt(brains.count)}
          sub={brains.admin ? `admin · ${brains.admin}` : (brains.standalone ? 'standalone' : '—')}
          pct={Math.min(100, Math.max(12, Number(brains.count || 0) * 16))}
        />
        <StatCard
          label="Skills"
          hash="skills"
          value={fmtInt(skills.installed)}
          sub={`${fmtInt(skills.recommended)} recommended`}
          pct={Math.min(100, Math.max(10, Number(skills.installed || 0) * 8))}
        />
        <StatCard
          label="Inbox"
          hash="inbox"
          value={fmtInt(approvals.pending + nowRunning.length)}
          sub={`${approvals.pending} approvals · ${nowRunning.length} running`}
          accent={approvals.pending || nowRunning.length ? 'var(--lh-accent-text)' : undefined}
          pct={Math.min(100, (approvals.pending + nowRunning.length) * 25)}
        />
        <StatCard label="Watchlist" hash="watchlist" pct={pulse.alive ? 100 : 48} value={
          <span className="inline-flex items-center gap-2">
            <span className={`lh-dot ${pulse.alive ? 'lh-dot-ok' : 'lh-dot-warn'}`} style={{ width: 12, height: 12 }} />
            <span style={{ fontSize: 16 }}>{pulse.alive ? 'Alive' : 'Quiet'}</span>
          </span>
        } sub={lastBeatRel ? `last check-in ${lastBeatRel}` : 'heartbeat status'} />
        <StatCard label="Readiness" hash="fleet" pct={health.ok ? 100 : 48} value={
          <span className="inline-flex items-center gap-2">
            <span className={`lh-dot ${healthDot(health.worst_level, health.ok)}`} style={{ width: 12, height: 12 }} />
            <span style={{ fontSize: 16 }}>{health.ok ? 'OK' : (health.worst_level || 'check')}</span>
          </span>
        } sub={health.ok ? 'all checks pass' : `worst: ${health.worst_level || '—'}`} />
        <StatCard label="Cost today" hash="reports" value={fmtUsd(cost.today_cost_usd)} accent="var(--lh-green)" sub={budgetLabel} pct={budgetPct}>
          {cost.kill_switch && (
            <div className="mt-2">
              <span className="lh-pill" style={{ color: 'var(--lh-alert)', borderColor: 'rgba(162,62,52,0.5)' }}>
                <span className="lh-dot lh-dot-alert" /> kill-switch on
              </span>
            </div>
          )}
        </StatCard>
        <StatCard label="Actions" hash="tools" value={fmtInt(tools.count)} sub="deterministic tools" pct={Math.min(100, Math.max(8, Number(tools.count || 0) * 10))}>
          {estados.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {estados.map(([estado, n]) => (
                <span key={estado} className="lh-tag">{estado} · {n}</span>
              ))}
            </div>
          )}
        </StatCard>
        <StatCard
          label="Capabilities"
          hash="capabilities"
          value={fmtInt((skills.installed || 0) + (tools.count || 0))}
          sub="skills + tools"
          pct={Math.min(100, Math.max(12, Number((skills.installed || 0) + (tools.count || 0)) * 5))}
        />
        <StatCard
          label="Runs today"
          hash="run"
          value={fmtInt(runsToday.done + runsToday.stopped + runsToday.failed)}
          sub={`${runsToday.done} done · ${runsToday.stopped} stopped · ${runsToday.failed} failed`}
          pct={Math.min(100, Math.max(8, Number(runsToday.done + runsToday.stopped + runsToday.failed || 0) * 14))}
        />
        <StatCard
          label="Activity 24h"
          hash="activity"
          value={fmtInt(activityWindowCount)}
          sub={`${fmtInt(activityTotal)} total · latest ${Math.min(recentLimit || grouped.length, grouped.length)} shown`}
          accent={importantCount ? 'var(--lh-accent-text)' : undefined}
          pct={Math.min(100, Math.max(8, Number(activityWindowCount || 0) * 8))}
        />
      </div>

      {/* 4 — Decisions — pending + inline approve/deny */}
      <div className="lh-card" style={approvals.pending > 0 ? { borderColor: 'rgba(217,165,33,0.3)' } : undefined}>
        <div className="lh-card-head">
          <div className="flex items-center gap-2">
            <span className="lh-eyebrow">Decisions waiting</span>
            {approvals.pending > 0 ? (
              <span className="lh-tag lh-tag-accent">{approvals.pending} pending</span>
            ) : (
              <span className="lh-tag">clear</span>
            )}
          </div>
        </div>
        {items.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Nothing awaiting approval.</p>
        ) : (
          <ul className="space-y-2">
            {items.map((a) => (
              <li key={a.id} className="lh-row">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[13px] font-medium" style={{ color: 'var(--lh-text)' }}>
                        {a.summary || a.accion || a.tipo || 'action'}
                      </span>
                      {a.tipo && <span className="lh-tag">{a.tipo}</span>}
                    </div>
                    <div className="lh-mono text-[10px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>
                      {a.actor_id ? `who: ${a.actor_id}` : ''}
                      {a.actor_id && a.creado_en ? ' · ' : ''}
                      {a.creado_en || ''}
                    </div>
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    <button
                      type="button" className="lh-btn lh-btn-primary" disabled={busy === a.id}
                      onClick={() => resolve(a.id, true)}
                    >
                      {busy === a.id ? '…' : 'Approve'}
                    </button>
                    <button
                      type="button" className="lh-btn lh-btn-danger" disabled={busy === a.id}
                      onClick={() => resolve(a.id, false)}
                    >
                      {busy === a.id ? '…' : 'Deny'}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        {resolveErr && <div className="text-[11px] mt-2" style={{ color: 'var(--lh-alert)' }}>{resolveErr}</div>}
      </div>

      {/* 5 — Smart activity feed (grouped routine ticks, flagged important events) */}
      <div className="lh-card">
        <div className="lh-card-head">
          <div className="flex items-center gap-2">
            <span className="lh-eyebrow">Decision log</span>
            <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
              {importantCount} important · latest {grouped.length} of {fmtInt(activityTotal)}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="lh-btn"
              onClick={() => setImportantOnly(true)}
              style={importantOnly ? { borderColor: 'var(--lh-gold-deep)', color: 'var(--lh-accent-text)' } : undefined}
              aria-pressed={importantOnly}
            >
              Important
            </button>
            <button
              type="button"
              className="lh-btn"
              onClick={() => setImportantOnly(false)}
              style={!importantOnly ? { borderColor: 'var(--lh-gold-deep)', color: 'var(--lh-accent-text)' } : undefined}
              aria-pressed={!importantOnly}
            >
              All
            </button>
          </div>
        </div>
        {shown.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>
            {grouped.length === 0 ? 'No episodes yet.' : 'No important events — switch to All to see routine beats.'}
          </p>
        ) : (
          <ul className="lh-timeline space-y-2">
            {shown.map((ep) => (
              <li
                key={ep.id}
                className="lh-row lh-timeline-item"
                style={{ padding: '0.55rem 0.75rem', ...(ep.important ? { borderColor: 'rgba(217,165,33,0.4)' } : {}) }}
              >
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <span className="text-[12px]" style={{ color: ep.important ? 'var(--lh-text)' : 'var(--lh-text-muted)' }}>
                    <span
                      className="lh-tag"
                      style={{ marginRight: 6, ...(ep.important ? { color: 'var(--lh-accent-text)', borderColor: 'var(--lh-gold-deep)' } : {}) }}
                    >
                      {ep.despertar_por || 'beat'}
                    </span>
                    {ep.decision || '—'}
                    {ep.count > 1 && (
                      <span className="lh-tag" style={{ marginLeft: 6 }}>×{ep.count}</span>
                    )}
                  </span>
                  <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{ep.creado_en}</span>
                </div>
                {ep.resultado && (
                  <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>{ep.resultado}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Errors — surfaced subtly */}
      {errorKeys.length > 0 && (
        <div className="lh-card" style={{ borderColor: 'rgba(162,62,52,0.25)' }}>
          <div className="lh-eyebrow" style={{ color: 'var(--lh-red-soft)' }}>Degraded sections</div>
          <ul className="mt-1.5 space-y-1">
            {errorKeys.map((k) => (
              <li key={k} className="text-[11px] lh-mono" style={{ color: 'var(--lh-text-faint)' }}>
                <span style={{ color: 'var(--lh-red-soft)' }}>{k}</span>: {errors[k]}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
