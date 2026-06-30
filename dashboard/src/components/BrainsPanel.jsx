import { useCallback, useEffect, useMemo, useState } from 'react'
import OrgTree from './OrgTree'
import OrgFlow from './OrgFlow'

// The Brains management panel — the centerpiece. Drives the slice-J org API:
//   GET    /api/org/topology              read the chart
//   GET    /api/org/skills                populate the skills picker
//   GET    /api/org/route?goal=…          preview a goal's root→leaf path
//   POST   /api/org/brains                create
//   PATCH  /api/org/brains/{id}           edit skills/limits/role/department
//   POST   /api/org/brains/{id}/move      re-parent (who reports to whom)
//   POST   /api/org/brains/{id}/promote   promote to root (commands everyone)
//   DELETE /api/org/brains/{id}?force=    delete (force removes the subtree)
// Every mutating call returns the updated chart, so we adopt the response
// directly (optimistic refresh) and fall back to a refetch on error.

// ── limits helpers ──────────────────────────────────────────────────────────
function parseLimitValue(raw) {
  const s = String(raw).trim()
  if (s === '') return ''
  if (s.includes(',')) return s.split(',').map((x) => x.trim()).filter(Boolean)
  const n = Number(s)
  if (!Number.isNaN(n) && /^-?\d*\.?\d+$/.test(s)) return n
  if (s === 'true') return true
  if (s === 'false') return false
  return s
}
function limitsToRows(limits) {
  return Object.entries(limits || {}).map(([key, value]) => ({
    key,
    value: Array.isArray(value) ? value.join(',') : String(value),
  }))
}
function rowsToLimits(rows) {
  const out = {}
  rows.forEach(({ key, value }) => {
    const k = key.trim()
    if (k) out[k] = parseLimitValue(value)
  })
  return out
}

// ── skills picker ───────────────────────────────────────────────────────────
function SkillPicker({ available, selected, onToggle }) {
  if (available.length === 0) {
    return <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>No skills registered.</p>
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {available.map((s) => {
        const on = selected.includes(s.domain)
        return (
          <button
            key={s.domain}
            type="button"
            onClick={() => onToggle(s.domain)}
            className={`lh-tag ${on ? 'lh-tag-accent' : ''}`}
            style={{ cursor: 'pointer' }}
            title={s.name ? `${s.name}${s.tools?.length ? ` · ${s.tools.length} tools` : ''}` : s.domain}
          >
            {on ? '✓ ' : ''}{s.domain}
          </button>
        )
      })}
    </div>
  )
}

// ── limits editor ───────────────────────────────────────────────────────────
const LIMIT_PRESETS = ['max_risk', 'budget_usd', 'allowed_actions']
function LimitsEditor({ rows, setRows }) {
  const update = (i, field, val) => setRows(rows.map((r, j) => (j === i ? { ...r, [field]: val } : r)))
  const remove = (i) => setRows(rows.filter((_, j) => j !== i))
  const add = (key = '') => setRows([...rows, { key, value: '' }])
  const presetsLeft = LIMIT_PRESETS.filter((p) => !rows.some((r) => r.key === p))
  return (
    <div className="space-y-1.5">
      {rows.map((r, i) => (
        <div key={i} className="flex gap-1.5 items-center">
          <input
            className="lh-input" style={{ flex: '0 0 42%' }} placeholder="key"
            value={r.key} onChange={(e) => update(i, 'key', e.target.value)}
          />
          <input
            className="lh-input" placeholder="value (num, true/false, or a,b,c)"
            value={r.value} onChange={(e) => update(i, 'value', e.target.value)}
          />
          <button type="button" className="lh-btn" onClick={() => remove(i)} title="Remove">✕</button>
        </div>
      ))}
      <div className="flex flex-wrap gap-1.5">
        <button type="button" className="lh-btn" onClick={() => add()}>+ limit</button>
        {presetsLeft.map((p) => (
          <button key={p} type="button" className="lh-btn" onClick={() => add(p)}>+ {p}</button>
        ))}
      </div>
    </div>
  )
}

// ── inspector for the selected brain ────────────────────────────────────────
function Inspector({ node, allIds, skills, onPatch, onMove, onPromote, onDelete, busy }) {
  const [role, setRole] = useState(node.role || '')
  const [department, setDepartment] = useState(node.department || '')
  const [pickedSkills, setPickedSkills] = useState(node.skills || [])
  const [limitRows, setLimitRows] = useState(limitsToRows(node.limits))
  const [moveTo, setMoveTo] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  // The parent mounts this with key={node.id}, so a new selection remounts the
  // component with fresh initial state — no reset effect needed.

  const toggleSkill = (d) =>
    setPickedSkills((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d]))

  const moveTargets = allIds.filter((id) => id !== node.id)
  const isAdmin = Boolean(node.is_admin)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="lh-mono text-sm font-semibold" style={{ color: 'var(--lh-text)' }}>{node.id}</span>
        <span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
          {node.parent ? `reports to ${node.parent}` : 'root'}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-1">
          <span className="lh-label">role</span>
          <input className="lh-input" value={role} onChange={(e) => setRole(e.target.value)} />
        </label>
        <label className="space-y-1">
          <span className="lh-label">department</span>
          <input className="lh-input" value={department} onChange={(e) => setDepartment(e.target.value)} />
        </label>
      </div>

      <div className="space-y-1">
        <span className="lh-label">skills</span>
        <SkillPicker available={skills} selected={pickedSkills} onToggle={toggleSkill} />
      </div>

      <div className="space-y-1">
        <span className="lh-label">limits</span>
        <LimitsEditor rows={limitRows} setRows={setLimitRows} />
      </div>

      <button
        type="button" className="lh-btn lh-btn-primary" disabled={busy}
        onClick={() => onPatch(node.id, {
          role: role.trim() || undefined,
          department: department.trim() || undefined,
          skills: pickedSkills,
          limits: rowsToLimits(limitRows),
        })}
      >
        Save changes
      </button>

      {isAdmin ? (
        <div className="pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
          <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
            Lhassa is the permanent administrator — it orbits everything and is anchored at the root.
            It can't be moved, promoted, or deleted.
          </p>
        </div>
      ) : (
        <>
          <div className="pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
            <span className="lh-label">move — change who it reports to</span>
            <div className="flex gap-1.5 mt-1">
              <select className="lh-select" value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
                <option value="">choose new parent…</option>
                {moveTargets.map((id) => <option key={id} value={id}>{id}</option>)}
              </select>
              <button
                type="button" className="lh-btn" disabled={busy || !moveTo}
                onClick={() => onMove(node.id, moveTo)}
              >
                Move
              </button>
            </div>
          </div>

          <div className="flex gap-2 pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
            <button
              type="button" className="lh-btn" disabled={busy || !node.parent}
              onClick={() => onPromote(node.id)} title="Make it a top-level child of Lhassa"
            >
              ↑ Promote to root
            </button>
            {confirmDelete ? (
              <div className="flex gap-1.5 flex-1">
                <button
                  type="button" className="lh-btn lh-btn-danger" disabled={busy}
                  onClick={() => onDelete(node.id, (node.children?.length || 0) > 0)}
                >
                  {(node.children?.length || 0) > 0 ? 'Delete subtree' : 'Confirm delete'}
                </button>
                <button type="button" className="lh-btn" onClick={() => setConfirmDelete(false)}>Cancel</button>
              </div>
            ) : (
              <button
                type="button" className="lh-btn lh-btn-danger" disabled={busy}
                onClick={() => setConfirmDelete(true)}
              >
                Delete
              </button>
            )}
          </div>
        </>
      )}
      {confirmDelete && (node.children?.length || 0) > 0 && (
        <p className="text-[11px]" style={{ color: 'var(--lh-warn)' }}>
          Warning: this brain has {node.children.length} child{node.children.length === 1 ? '' : 'ren'}.
          Deleting removes the whole subtree (force).
        </p>
      )}
    </div>
  )
}

// ── create-brain form ───────────────────────────────────────────────────────
function CreateForm({ allIds, defaultParent, skills, onCreate, onClose, busy }) {
  const [id, setId] = useState('')
  const [role, setRole] = useState('department')
  const [parent, setParent] = useState(defaultParent || '')
  const [department, setDepartment] = useState('')
  const [pickedSkills, setPickedSkills] = useState([])
  const [limitRows, setLimitRows] = useState([])

  const toggleSkill = (d) =>
    setPickedSkills((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d]))

  const submit = () => {
    onCreate({
      id: id.trim(),
      role: role.trim() || undefined,
      parent: parent || undefined,
      department: department.trim() || undefined,
      skills: pickedSkills,
      limits: rowsToLimits(limitRows),
    })
  }

  return (
    <div className="lh-card" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="lh-card-title">New brain</h3>
        <button type="button" className="lh-btn" onClick={onClose}>✕</button>
      </div>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1">
            <span className="lh-label">id *</span>
            <input className="lh-input" value={id} onChange={(e) => setId(e.target.value)} placeholder="e.g. finanzas" />
          </label>
          <label className="space-y-1">
            <span className="lh-label">role</span>
            <input className="lh-input" value={role} onChange={(e) => setRole(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="lh-label">parent</span>
            <select className="lh-select" value={parent} onChange={(e) => setParent(e.target.value)}>
              <option value="">— (root, if none yet)</option>
              {allIds.map((pid) => <option key={pid} value={pid}>{pid}</option>)}
            </select>
          </label>
          <label className="space-y-1">
            <span className="lh-label">department</span>
            <input className="lh-input" value={department} onChange={(e) => setDepartment(e.target.value)} />
          </label>
        </div>
        <div className="space-y-1">
          <span className="lh-label">skills</span>
          <SkillPicker available={skills} selected={pickedSkills} onToggle={toggleSkill} />
        </div>
        <div className="space-y-1">
          <span className="lh-label">limits</span>
          <LimitsEditor rows={limitRows} setRows={setLimitRows} />
        </div>
        <button type="button" className="lh-btn lh-btn-primary" disabled={busy || !id.trim()} onClick={submit}>
          Create brain
        </button>
      </div>
    </div>
  )
}

// ── panel ───────────────────────────────────────────────────────────────────
export default function BrainsPanel() {
  const [topology, setTopology] = useState(null)
  const [skills, setSkills] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [goal, setGoal] = useState('')
  const [route, setRoute] = useState(null)
  const [routeErr, setRouteErr] = useState('')
  const [view, setView] = useState('flow') // 'flow' (n8n-style canvas) | 'tree'

  const loadTopology = useCallback(async () => {
    try {
      const r = await fetch('/api/org/topology')
      if (!r.ok) throw new Error('topology fetch failed')
      const d = await r.json()
      setTopology(d)
      setError('')
    } catch {
      setError('Could not load the org chart.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadTopology()
    fetch('/api/org/skills')
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setSkills(Array.isArray(d) ? d : []))
      .catch(() => setSkills([]))
  }, [loadTopology])

  // A mutating call returns the updated chart — adopt it (optimistic refresh).
  const mutate = useCallback(async (label, doFetch) => {
    setBusy(true)
    setError('')
    try {
      const r = await doFetch()
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `${label} failed`)
      }
      const chart = await r.json()
      if (chart && chart.nodes) setTopology(chart)
      else await loadTopology()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : `${label} failed`)
      return false
    } finally {
      setBusy(false)
    }
  }, [loadTopology])

  const createBrain = async (payload) => {
    const ok = await mutate('Create', () =>
      fetch('/api/org/brains', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }))
    if (ok) { setShowCreate(false); setSelectedId(payload.id) }
  }
  const patchBrain = (id, payload) =>
    mutate('Save', () =>
      fetch(`/api/org/brains/${encodeURIComponent(id)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }))
  const moveBrain = (id, parent) =>
    mutate('Move', () =>
      fetch(`/api/org/brains/${encodeURIComponent(id)}/move`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parent }),
      }))
  const promoteBrain = (id) =>
    mutate('Promote', () =>
      fetch(`/api/org/brains/${encodeURIComponent(id)}/promote`, { method: 'POST' }))
  const deleteBrain = async (id, force) => {
    const ok = await mutate('Delete', () =>
      fetch(`/api/org/brains/${encodeURIComponent(id)}?force=${force ? 'true' : 'false'}`, { method: 'DELETE' }))
    if (ok && selectedId === id) setSelectedId(null)
  }

  const previewRoute = async () => {
    const g = goal.trim()
    if (!g) return
    setRouteErr('')
    setRoute(null)
    try {
      const r = await fetch(`/api/org/route?goal=${encodeURIComponent(g)}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Route preview failed')
      }
      setRoute(await r.json())
    } catch (e) {
      setRouteErr(e instanceof Error ? e.message : 'Route preview failed')
    }
  }

  const nodes = topology?.nodes || []
  const allIds = useMemo(() => (topology?.nodes || []).map((n) => n.id), [topology])
  const selected = nodes.find((n) => n.id === selectedId) || null
  const routePath = route?.path || []
  const isStandalone = topology?.standalone

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <h2 className="lh-card-title">Brains</h2>
          <span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
            {nodes.length} brain{nodes.length === 1 ? '' : 's'}
            {isStandalone ? ' · standalone' : ''}
          </span>
        </div>
        <div className="flex gap-2 items-center">
          <div className="flex gap-1" role="group" aria-label="view">
            <button
              type="button"
              className={`lh-btn ${view === 'flow' ? 'lh-btn-primary' : ''}`}
              onClick={() => setView('flow')}
            >
              Flow
            </button>
            <button
              type="button"
              className={`lh-btn ${view === 'tree' ? 'lh-btn-primary' : ''}`}
              onClick={() => setView('tree')}
            >
              Tree
            </button>
          </div>
          <button type="button" className="lh-btn" onClick={loadTopology} disabled={busy}>Refresh</button>
          <button type="button" className="lh-btn lh-btn-primary" onClick={() => setShowCreate((v) => !v)}>
            + Create brain
          </button>
        </div>
      </div>

      {/* Route preview */}
      <div className="lh-row mb-3" style={{ background: 'var(--lh-surface-2)' }}>
        <span className="lh-label">route preview — where would a goal land?</span>
        <div className="flex gap-1.5 mt-1.5">
          <input
            className="lh-input" placeholder="e.g. optimize the database query"
            value={goal} onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') previewRoute() }}
          />
          <button type="button" className="lh-btn" onClick={previewRoute} disabled={!goal.trim()}>Preview</button>
        </div>
        {routeErr && <div className="text-[11px] mt-1.5" style={{ color: 'var(--lh-alert)' }}>{routeErr}</div>}
        {route && (
          <div className="text-[12px] mt-2 lh-mono flex items-center flex-wrap gap-1">
            {route.path.map((id, i) => (
              <span key={`${id}-${i}`}>
                <span style={{ color: i === route.path.length - 1 ? 'var(--lh-warn)' : 'var(--lh-accent-text)' }}>{id}</span>
                {i < route.path.length - 1 && <span style={{ color: 'var(--lh-text-faint)' }}> → </span>}
              </span>
            ))}
            <span style={{ color: 'var(--lh-text-faint)' }}> · dept {route.department}</span>
          </div>
        )}
      </div>

      {showCreate && (
        <div className="mb-3">
          <CreateForm
            allIds={allIds} defaultParent={selectedId || topology?.admin || topology?.root} skills={skills}
            onCreate={createBrain} onClose={() => setShowCreate(false)} busy={busy}
          />
        </div>
      )}

      {error && (
        <div className="lh-row mb-3" style={{ borderColor: 'rgba(162,62,52,0.45)', color: 'var(--lh-alert)' }}>
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading the org chart…</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
          {/* Canvas */}
          <div className="lg:col-span-3">
            <div className="lh-label mb-2">
              {view === 'flow'
                ? 'flow — drag a node onto another to re-parent it'
                : 'hierarchy — who commands whom'}
            </div>
            {view === 'flow' ? (
              <OrgFlow
                topology={topology} selectedId={selectedId} routePath={routePath}
                onSelect={(id) => setSelectedId(id)} onMove={moveBrain}
              />
            ) : (
              <OrgTree
                topology={topology} selectedId={selectedId} routePath={routePath}
                onSelect={(id) => setSelectedId(id)}
              />
            )}
          </div>
          {/* Inspector */}
          <div className="lg:col-span-2">
            <div className="lh-label mb-2">inspector</div>
            {selected ? (
              <Inspector
                key={selected.id} node={selected} allIds={allIds} skills={skills}
                onPatch={patchBrain} onMove={moveBrain} onPromote={promoteBrain}
                onDelete={deleteBrain} busy={busy}
              />
            ) : (
              <p className="text-xs lh-row" style={{ color: 'var(--lh-text-faint)' }}>
                Select a brain in the tree to edit its role, skills, limits, move it, promote it, or delete it.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
