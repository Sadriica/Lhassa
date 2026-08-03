import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from '../lib/toast'
import { fmtDuration, fmtPct } from '../lib/format'
import { requireOk } from '../lib/api'
import { EmptyState, ErrorState, SectionHeader } from './ui/common'

// The React-Flow canvas is heavy (React Flow + its styles). Lazy-load it so it is
// code-split out of the initial dashboard bundle and only fetched when a flow opens.
const WorkflowEditor = lazy(() => import('./WorkflowEditor'))

// A small, matte placeholder shown while the editor chunk loads.
function EditorFallback() {
  return (
    <div className="lh-card">
      <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading the flow editor…</p>
    </div>
  )
}

// The "Flows" section: an n8n-style work-flow surface. It lists the stored flows
// (GET /api/flows) and opens the canvas editor for one. A flow wires
// trigger → task → gate → output (with branches) and is executable
// (POST /api/flows/{id}/run). This panel owns the API calls; WorkflowEditor owns
// the canvas. Task nodes need the org's brains (to pin) + skills (to auto-route by),
// loaded once and handed to the editor.

function starterFlow() {
  // A fresh flow opens with its single, required trigger already placed.
  const id = `flow-${Math.random().toString(36).slice(2, 8)}`
  return {
    id,
    name: 'New flow',
    description: '',
    nodes: [{ id: 'trigger-1', kind: 'trigger', config: { type: 'manual' }, label: 'Start' }],
    edges: [],
  }
}

function kindCount(flow, kind) {
  return (flow.nodes || []).filter((n) => n.kind === kind).length
}

// ── one flow row in the list ────────────────────────────────────────────────────
function FlowRow({ flow, stats, onOpen, onDelete, busy }) {
  const [confirm, setConfirm] = useState(false)
  const n = (flow.nodes || []).length
  return (
    <div className="lh-row" style={{ background: 'var(--lh-surface-2)', display: 'flex', alignItems: 'center', gap: 10 }}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-semibold" style={{ color: 'var(--lh-text)' }}>{flow.name || flow.id}</span>
          <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{flow.id}</span>
        </div>
        {flow.description && (
          <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>{flow.description}</div>
        )}
        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
          <span className="lh-badge" title="nodes">{n} node{n === 1 ? '' : 's'}</span>
          {kindCount(flow, 'task') > 0 && <span className="lh-badge">{kindCount(flow, 'task')} task</span>}
          {kindCount(flow, 'gate') > 0 && <span className="lh-badge">{kindCount(flow, 'gate')} gate</span>}
          {kindCount(flow, 'output') > 0 && <span className="lh-badge">{kindCount(flow, 'output')} output</span>}
          {kindCount(flow, 'branch') > 0 && <span className="lh-badge">{kindCount(flow, 'branch')} branch</span>}
          {stats && (
            <>
              <span className="lh-badge">{stats.runs || 0} runs</span>
              <span className="lh-badge">{fmtPct(stats.success_rate)} success</span>
              <span className="lh-badge">avg {fmtDuration(stats.avg_duration_ms)}</span>
              {stats.last_status && <span className="lh-badge">last {stats.last_status}</span>}
            </>
          )}
        </div>
      </div>
      <div className="flex gap-1.5 items-center shrink-0">
        <button type="button" className="lh-btn lh-btn-primary" onClick={() => onOpen(flow.id)} disabled={busy}>Open</button>
        {confirm ? (
          <>
            <button type="button" className="lh-btn lh-btn-danger" onClick={() => onDelete(flow.id)} disabled={busy}>Confirm</button>
            <button type="button" className="lh-btn" onClick={() => setConfirm(false)}>Cancel</button>
          </>
        ) : (
          <button type="button" className="lh-btn lh-btn-danger" onClick={() => setConfirm(true)} disabled={busy}>Delete</button>
        )}
      </div>
    </div>
  )
}

function FlowInsights({ items = [], onOpen }) {
  const active = items.filter((item) => item.runs > 0)
  return (
    <div className="lh-card">
      <SectionHeader
        eyebrow="Flow analytics"
        title="Usage, reliability and speed"
        meta={<span className="lh-tag">30d</span>}
      />
      {active.length === 0 ? (
        <EmptyState>
          No workflow runs recorded yet. Run a flow and this will show run count, success rate, duration and latest status.
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
          {active.slice(0, 6).map((item) => (
            <button
              key={item.entity_id}
              type="button"
              className="lh-row text-left"
              style={{ background: 'var(--lh-surface-2)', cursor: 'pointer' }}
              onClick={() => onOpen(item.entity_id)}
              title={`Open ${item.label || item.entity_id}`}
            >
              <div className="text-[13px] font-medium truncate" style={{ color: 'var(--lh-text)' }}>
                {item.label || item.entity_id}
              </div>
              <div className="flex gap-1 flex-wrap mt-2">
                <span className="lh-tag">{item.runs} runs</span>
                <span className="lh-tag">{fmtPct(item.success_rate)} success</span>
                <span className="lh-tag">avg {fmtDuration(item.avg_duration_ms)}</span>
                {item.last_status && <span className="lh-tag">last {item.last_status}</span>}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Props (all optional so this works before App.jsx wires the hash sub-route):
//   route     — a flow id to auto-open (string), or null/undefined for the list
//   onRoute   — onRoute(idOrNull): update the URL hash (id opens a flow, null → list)
export default function WorkflowsPanel({ route, onRoute } = {}) {
  const [flows, setFlows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(null) // the open flow dict (or null = list)
  const [isNew, setIsNew] = useState(false)
  const [brains, setBrains] = useState([])
  const [skills, setSkills] = useState([])
  const [flowAnalytics, setFlowAnalytics] = useState({})
  const [flowAnalyticsItems, setFlowAnalyticsItems] = useState([])
  const [flowRuns, setFlowRuns] = useState([])

  const loadFlows = useCallback(async () => {
    try {
      const r = await fetch('/api/flows')
      await requireOk(r, 'Flows')
      const d = await r.json()
      setFlows(Array.isArray(d) ? d : [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load flows.')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadAnalytics = useCallback(async () => {
    try {
      const r = await fetch('/api/analytics/flows?range=30d')
      await requireOk(r, 'Flow analytics')
      const d = await r.json()
      const byId = {}
      for (const item of d.items || []) byId[item.entity_id] = item
      setFlowAnalytics(byId)
      setFlowAnalyticsItems(d.items || [])
    } catch {
      setFlowAnalytics({})
      setFlowAnalyticsItems([])
    }
  }, [])

  const loadFlowRuns = useCallback(async (id) => {
    if (!id) { setFlowRuns([]); return }
    try {
      const r = await fetch(`/api/analytics/runs?kind=flow&entity_id=${encodeURIComponent(id)}&limit=25`)
      await requireOk(r, 'Flow run history')
      const d = await r.json()
      setFlowRuns(Array.isArray(d.items) ? d.items : [])
    } catch {
      setFlowRuns([])
    }
  }, [])

  useEffect(() => {
    loadFlows()
    loadAnalytics()
    // The org context task nodes route against: brains to pin, skills to auto-route.
    fetch('/api/org/topology')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setBrains(Array.isArray(d?.nodes) ? d.nodes : []))
      .catch(() => setBrains([]))
    fetch('/api/org/skills')
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setSkills(Array.isArray(d) ? d : []))
      .catch(() => setSkills([]))
  }, [loadFlows, loadAnalytics])

  const openFlow = useCallback(async (id) => {
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`/api/flows/${encodeURIComponent(id)}`)
      if (!r.ok) throw new Error('open failed')
      const d = await r.json()
      setEditing(d)
      setIsNew(false)
      await loadFlowRuns(d.id)
      onRoute?.(d.id) // deep-link: mirror the open flow into the URL hash
    } catch {
      setError('Could not open that flow.')
    } finally {
      setBusy(false)
    }
  }, [onRoute, loadFlowRuns])

  // Deep-link: when the `route` prop names a flow id (on mount or a hash change),
  // auto-open it. Guarded so re-opening the same flow doesn't refetch in a loop.
  useEffect(() => {
    if (!route || editing?.id === route) return
    openFlow(route)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route])

  const newFlow = useCallback(() => {
    setEditing(starterFlow())
    setIsNew(true)
    setFlowRuns([])
    onRoute?.(null) // a new flow has no persistent id yet — keep the URL on the list
  }, [onRoute])

  // #10 — recreate a just-deleted flow from its cached JSON (Undo). PUT re-creates the
  // flow at its id (replace-or-create), then refresh the list so it reappears.
  const recreateFlow = useCallback(async (flow) => {
    try {
      const r = await fetch(`/api/flows/${encodeURIComponent(flow.id)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(flow),
      })
      if (!r.ok) throw new Error('restore failed')
      await loadFlows()
      toast.success(`Flow "${flow.name || flow.id}" restored.`)
    } catch {
      toast.error('Could not restore that flow.')
    }
  }, [loadFlows])

  // #10 — DELETE is optimistic + undoable. The flow leaves the list the instant Confirm
  // is clicked; on success the toast offers Undo, which re-creates it from the cached
  // JSON. On failure the optimistic removal is rolled back and an error toast fires.
  const deleteFlow = useCallback(async (id) => {
    const prevFlows = flows
    const cached = flows.find((f) => f.id === id) // full flow dict (nodes/edges) for Undo
    setFlows((prev) => prev.filter((f) => f.id !== id)) // optimistic removal
    if (editing?.id === id) { setEditing(null); setIsNew(false); onRoute?.(null) }
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`/api/flows/${encodeURIComponent(id)}`, { method: 'DELETE' })
      if (!r.ok && r.status !== 404) throw new Error('delete failed')
      await loadFlows() // reconcile with the server (preserves existing refetch behavior)
      toast.show({
        type: 'success',
        message: `Flow "${cached?.name || id}" deleted`,
        action: cached ? { label: 'Undo', onClick: () => recreateFlow(cached) } : null,
      })
    } catch {
      setFlows(prevFlows) // revert the optimistic removal
      setError('Could not delete that flow.')
      toast.error('Could not delete that flow.')
    } finally {
      setBusy(false)
    }
  }, [flows, editing, loadFlows, onRoute, recreateFlow])

  // Save the canvas: POST a new flow, PUT an existing one. Returns {ok, flow|detail}
  // so the editor can show a 400 {detail} inline.
  const saveFlow = useCallback(async (flowDict) => {
    try {
      const url = isNew ? '/api/flows' : `/api/flows/${encodeURIComponent(flowDict.id)}`
      const method = isNew ? 'POST' : 'PUT'
      const r = await fetch(url, {
        method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(flowDict),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) return { ok: false, detail: d.detail || `Save failed (${r.status})` }
      setIsNew(false)
      setEditing(d) // adopt the canonical stored flow (same id → editor keeps the canvas)
      onRoute?.(d.id) // a freshly-created flow now has a persistent id — deep-link it
      await loadFlows()
      return { ok: true, flow: d }
    } catch {
      return { ok: false, detail: 'Save failed — network error.' }
    }
  }, [isNew, loadFlows, onRoute])

  const runFlow = useCallback(async (id, inputs = {}) => {
    try {
      const r = await fetch(`/api/flows/${encodeURIComponent(id)}/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ inputs }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) return { ok: false, detail: d.detail || `Run failed (${r.status})` }
      await loadAnalytics()
      await loadFlowRuns(id)
      return { ok: true, run: d }
    } catch {
      return { ok: false, detail: 'Run failed — network error.' }
    }
  }, [loadAnalytics, loadFlowRuns])

  const closeEditor = useCallback(() => { setEditing(null); setIsNew(false); setFlowRuns([]); onRoute?.(null) }, [onRoute])

  const sorted = useMemo(
    () => [...flows].sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id)),
    [flows],
  )

  if (editing) {
    return (
      <div className="space-y-4">
        <Suspense fallback={<EditorFallback />}>
          <div className="lh-card">
            <WorkflowEditor
              flow={editing}
              isNew={isNew}
              brains={brains}
              skills={skills}
              flowStats={flowAnalytics[editing.id]}
              flowRuns={flowRuns}
              onSave={saveFlow}
              onRun={runFlow}
              onClose={closeEditor}
            />
          </div>
        </Suspense>
        <FlowInsights items={flowAnalyticsItems} onOpen={openFlow} />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <h2 className="lh-card-title">Flows</h2>
            <span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
              {flows.length} flow{flows.length === 1 ? '' : 's'} · trigger → task → gate → output
            </span>
          </div>
          <div className="flex gap-2 items-center">
            <button type="button" className="lh-btn" onClick={() => { loadFlows(); loadAnalytics() }} disabled={busy}>Refresh</button>
            <button type="button" className="lh-btn lh-btn-primary" onClick={newFlow}>+ New flow</button>
          </div>
        </div>

        {error && <ErrorState>{error}</ErrorState>}

        {loading ? (
          <EmptyState>Loading flows…</EmptyState>
        ) : sorted.length === 0 ? (
          <div className="lh-row" style={{ textAlign: 'center' }}>
            <p className="text-xs mb-2" style={{ color: 'var(--lh-text-faint)' }}>
              No flows yet. A flow starts with a single trigger you can wire into tasks, gates, and outputs.
            </p>
            <button type="button" className="lh-btn lh-btn-primary" onClick={newFlow}>+ New flow</button>
          </div>
        ) : (
          <div className="space-y-2">
            {sorted.map((f) => (
              <FlowRow key={f.id} flow={f} stats={flowAnalytics[f.id]} onOpen={openFlow} onDelete={deleteFlow} busy={busy} />
            ))}
          </div>
        )}
      </div>
      <FlowInsights items={flowAnalyticsItems} onOpen={openFlow} />
    </div>
  )
}
