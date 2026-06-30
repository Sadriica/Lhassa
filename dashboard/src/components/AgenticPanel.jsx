import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

// AGENTIC panel — the dashboard face of `lhassa code`. Hand a task to a local AI
// CLI (claude/codex/opencode) and watch it run live.
//
//   GET  /api/code/clis  → {clis:[names], default}  populate the selector
//   POST /api/code  {task, cli?}  → {ok, run_id, cli}  (400 {error}/{detail})
//   GET  /api/code/runs  → recent runs [{id,name,status,task,output,started_at,finished_at}]
//
// The launch streams over the EXISTING /ws fan-out keyed by run_id:
//   agent_started → many agent_token → agent_done | agent_error
// App.jsx already folds those into its `agents` map (run_id → {status,output,…}),
// accumulating each agent_token into `output`. We receive that map as a prop and
// render the live run by matching the run_id we got back from POST /api/code.

const STATUS = {
  running: { label: 'running', dot: 'lh-dot-accent', live: true },
  done:    { label: 'done',    dot: 'lh-dot-ok' },
  error:   { label: 'error',   dot: 'lh-dot-alert' },
}

// Pull a readable message from an error body — the route returns {error};
// the documented contract mentions {detail}. Honor both.
async function errText(r, fallback) {
  const d = await r.json().catch(() => ({}))
  return d.error || d.detail || fallback
}

// "ai-cli (claude)" → "claude"; falls back to the whole name.
function cliOf(name) {
  const m = /\(([^)]+)\)/.exec(name || '')
  return m ? m[1] : name || '?'
}

function fmtDuration(start, end) {
  if (!start || !end || end < start) return null
  const s = end - start
  if (s < 1) return '<1s'
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`
  const m = Math.floor(s / 60)
  return `${m}m ${Math.round(s - m * 60)}s`
}

function StatusPill({ status }) {
  const meta = STATUS[status] || { label: status || 'idle', dot: '' }
  return (
    <span className="lh-pill">
      <span className={`lh-dot ${meta.dot} ${meta.live ? 'animate-pulse' : ''}`} />
      {meta.label}
    </span>
  )
}

// ── live output console ───────────────────────────────────────────────────────
function OutputConsole({ run, elapsed }) {
  const ref = useRef(null)
  const output = run?.output || ''
  const status = run?.status

  // Auto-scroll to the newest token as it streams in.
  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [output])

  if (!run) {
    return (
      <div className="lh-row" style={{ background: 'var(--lh-bg-2)', color: 'var(--lh-text-faint)' }}>
        <p className="text-xs">Launch a task or pick a recent run to watch its output here.</p>
      </div>
    )
  }

  const isRunning = status === 'running'
  const duration = fmtDuration(run.started_at, run.finished_at)

  return (
    <div className="lh-row" style={{ background: 'var(--lh-bg-2)', padding: 0, overflow: 'hidden' }}>
      <div
        className="flex items-center justify-between px-3 py-2"
        style={{ borderBottom: '1px solid var(--lh-border)' }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <StatusPill status={status} />
          <span className="lh-mono text-[11px]" style={{ color: 'var(--lh-accent-text)' }}>
            #{run.id}
          </span>
          <span className="lh-mono text-[11px] truncate" style={{ color: 'var(--lh-text-muted)' }}>
            {cliOf(run.name)}
          </span>
        </div>
        <span className="lh-mono text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
          {isRunning
            ? (elapsed != null ? `${elapsed.toFixed(0)}s` : 'streaming…')
            : (duration ? `done in ${duration}` : null)}
        </span>
      </div>

      {run.task && (
        <div
          className="px-3 py-1.5 text-[11px] lh-mono truncate"
          style={{ color: 'var(--lh-text-faint)', borderBottom: '1px solid var(--lh-border)' }}
          title={run.task}
        >
          $ {run.task}
        </div>
      )}

      <pre
        ref={ref}
        className={`lh-mono text-[11px] px-3 py-2.5 whitespace-pre-wrap leading-relaxed ${isRunning ? 'cursor-blink' : ''}`}
        style={{
          color: status === 'error' ? 'var(--lh-alert)' : 'var(--lh-text)',
          maxHeight: 360,
          minHeight: 120,
          overflowY: 'auto',
          margin: 0,
        }}
      >
        {output || (isRunning ? 'Waiting for output…' : '(no output)')}
      </pre>
    </div>
  )
}

// ── recent-runs list ──────────────────────────────────────────────────────────
function RecentRuns({ runs, loading, selectedId, onSelect }) {
  if (loading) {
    return <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>Loading runs…</p>
  }
  if (runs.length === 0) {
    return (
      <p className="text-[11px] lh-row" style={{ color: 'var(--lh-text-faint)' }}>
        No runs yet. Launch a task above to see it here.
      </p>
    )
  }
  return (
    <div className="space-y-1.5">
      {runs.map((r) => {
        const meta = STATUS[r.status] || { dot: '' }
        const active = r.id === selectedId
        return (
          <button
            key={r.id}
            type="button"
            onClick={() => onSelect(r.id)}
            className="lh-row w-full text-left"
            style={{
              background: active ? 'var(--lh-accent-dim)' : 'var(--lh-surface-2)',
              borderColor: active ? 'var(--lh-gold-deep)' : 'var(--lh-border)',
              cursor: 'pointer',
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`lh-dot ${meta.dot} ${r.status === 'running' ? 'animate-pulse' : ''}`} />
                <span className="lh-mono text-[12px] font-semibold" style={{ color: 'var(--lh-text)' }}>#{r.id}</span>
                <span className="lh-tag lh-tag-accent">{cliOf(r.name)}</span>
              </div>
              <StatusPill status={r.status} />
            </div>
            {r.task && (
              <p className="text-[11px] mt-1 truncate" style={{ color: 'var(--lh-text-muted)' }} title={r.task}>
                {r.task}
              </p>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ── panel ─────────────────────────────────────────────────────────────────────
export default function AgenticPanel({ agents }) {
  const [clis, setClis] = useState([])
  const [cli, setCli] = useState('')
  const [clisError, setClisError] = useState('')
  const [task, setTask] = useState('')
  const [launching, setLaunching] = useState(false)
  const [launchError, setLaunchError] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [launchedRunId, setLaunchedRunId] = useState(null)
  const [runs, setRuns] = useState([])
  const [runsLoading, setRunsLoading] = useState(true)
  const [now, setNow] = useState(Date.now())
  const launchedAtRef = useRef(null)

  // CLIs for the selector + auto-detected default.
  useEffect(() => {
    let alive = true
    fetch('/api/code/clis')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('clis fetch failed'))))
      .then((d) => {
        if (!alive) return
        const list = d.clis || []
        setClis(list)
        setCli(d.default && list.includes(d.default) ? d.default : (list[0] || ''))
        setClisError(list.length === 0 ? 'No local AI CLIs installed (claude / codex / opencode).' : '')
      })
      .catch(() => alive && setClisError('Could not load CLIs.'))
    return () => { alive = false }
  }, [])

  const loadRuns = useCallback(async () => {
    try {
      const r = await fetch('/api/code/runs')
      const d = r.ok ? await r.json() : []
      // Newest first.
      setRuns([...d].sort((a, b) => (b.started_at || 0) - (a.started_at || 0)))
    } catch {
      setRuns([])
    } finally {
      setRunsLoading(false)
    }
  }, [])

  useEffect(() => { loadRuns() }, [loadRuns])

  // Selected run: prefer App's WS-fed live record (it accumulates each agent_token
  // into .output and flips status on done/error), else the recent-runs snapshot.
  const selectedRun = useMemo(() => {
    if (!selectedId) return null
    if (agents[selectedId]) return agents[selectedId]
    return runs.find((r) => r.id === selectedId) || null
  }, [selectedId, agents, runs])

  // While the selected run streams, tick a live elapsed timer and keep runs fresh
  // once it finishes (so timing/output land in the recent list too).
  const liveStatus = selectedId && agents[selectedId]?.status
  useEffect(() => {
    if (liveStatus !== 'running') return undefined
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [liveStatus])

  const prevStatusRef = useRef(null)
  useEffect(() => {
    if (prevStatusRef.current === 'running' && liveStatus && liveStatus !== 'running') {
      loadRuns()
    }
    prevStatusRef.current = liveStatus
  }, [liveStatus, loadRuns])

  const elapsed =
    liveStatus === 'running' && launchedAtRef.current
      ? Math.max(0, (now - launchedAtRef.current) / 1000)
      : null

  const launch = async () => {
    const t = task.trim()
    if (!t || launching) return
    setLaunching(true)
    setLaunchError('')
    try {
      const r = await fetch('/api/code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: t, cli: cli || undefined }),
      })
      if (!r.ok) throw new Error(await errText(r, 'Launch failed'))
      const d = await r.json()
      launchedAtRef.current = Date.now()
      setNow(Date.now())
      setLaunchedRunId(d.run_id)
      setSelectedId(d.run_id)
      setTask('')
      loadRuns()
    } catch (e) {
      setLaunchError(e instanceof Error ? e.message : 'Launch failed')
    } finally {
      setLaunching(false)
    }
  }

  const onSelectRun = (id) => {
    setSelectedId(id)
    // If we pick the run we launched, keep the live timer meaningful.
    if (id !== launchedRunId) launchedAtRef.current = null
  }

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <h2 className="lh-card-title">Agentic run</h2>
          <span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
            the dashboard face of <span className="lh-mono">lhassa code</span>
          </span>
        </div>
        <button type="button" className="lh-btn" onClick={loadRuns} disabled={runsLoading}>
          Refresh
        </button>
      </div>

      {/* launch form */}
      <div className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
        <label className="space-y-1 block">
          <span className="lh-label">task</span>
          <textarea
            className="lh-input"
            rows={3}
            placeholder="Describe the coding task — e.g. add a /health route and a test for it"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') launch() }}
            style={{ resize: 'vertical' }}
            disabled={launching}
          />
        </label>

        <div className="flex flex-wrap items-end gap-2 mt-2">
          <label className="space-y-1">
            <span className="lh-label">cli</span>
            <select
              className="lh-select"
              style={{ width: 'auto', minWidth: 140 }}
              value={cli}
              onChange={(e) => setCli(e.target.value)}
              disabled={launching || clis.length === 0}
            >
              {clis.length === 0 && <option value="">none installed</option>}
              {clis.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <button
            type="button"
            className="lh-btn lh-btn-primary"
            onClick={launch}
            disabled={launching || !task.trim() || clis.length === 0}
          >
            {launching ? 'Launching…' : '▸ Run'}
          </button>
          {launchedRunId && (
            <span className="lh-mono text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
              launched <span style={{ color: 'var(--lh-accent-text)' }}>#{launchedRunId}</span>
              {' '}({cli})
            </span>
          )}
        </div>

        <p className="text-[11px] mt-2" style={{ color: 'var(--lh-text-faint)' }}>
          Runs in the server's working directory via the owner's subscription — no API key.
          It can edit files and run tools there.
        </p>

        {clisError && (
          <div className="text-[11px] mt-2" style={{ color: 'var(--lh-alert)' }}>{clisError}</div>
        )}
        {launchError && (
          <div className="lh-row mt-2" style={{ borderColor: 'rgba(162,62,52,0.45)', color: 'var(--lh-alert)' }}>
            {launchError}
          </div>
        )}
      </div>

      {/* live output */}
      <div className="mt-4">
        <div className="lh-label mb-2">output</div>
        <OutputConsole run={selectedRun} elapsed={elapsed} />
      </div>

      {/* recent runs */}
      <div className="mt-4">
        <div className="lh-label mb-2">recent runs</div>
        <RecentRuns runs={runs} loading={runsLoading} selectedId={selectedId} onSelect={onSelectRun} />
      </div>
    </div>
  )
}
