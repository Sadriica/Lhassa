import { useCallback, useEffect, useMemo, useState } from 'react'
import OrgTree from './OrgTree'
import OrgFlow from './OrgFlow'
import TemplatesPicker from './TemplatesPicker'

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
  // The backend REPLACES limits with whatever object we send (a None/absent field
  // means "leave unchanged", so we always send a full object — never null). A key
  // is CLEARED by simply omitting it: removing a row drops it here, and an empty
  // value drops it too, so clearing the field is also a removal. Sending `{}`
  // clears every limit.
  const out = {}
  rows.forEach(({ key, value }) => {
    const k = key.trim()
    if (!k) return
    const v = parseLimitValue(value)
    if (v === '' || v === null || v === undefined) return // empty → remove the key
    out[k] = v
  })
  return out
}

// ── tree helper ───────────────────────────────────────────────────────────────
// Descendants of `rootId` (so the Move dropdown can exclude them — re-parenting a
// node under its own descendant would create a cycle).
function descendantIds(rootId, byId) {
  const out = new Set()
  const walk = (id) => (byId[id]?.children || []).forEach((c) => {
    if (!out.has(c)) { out.add(c); walk(c) }
  })
  walk(rootId)
  return out
}

// ── department field ──────────────────────────────────────────────────────────
// A dropdown over the EXISTING departments (distinct non-empty values across the
// chart) plus a "+ New department…" option that reveals a text input to coin a new
// one. Creating a department is just assigning a fresh string — no backend change.
const NEW_DEPT = '__new_dept__'
function DepartmentField({ value, options, onChange }) {
  // We're in "type a new one" mode when adding, or when the current value isn't a
  // known department (e.g. mid-typing a brand new name).
  const known = options.includes(value)
  const [adding, setAdding] = useState(value !== '' && !known)

  if (adding) {
    return (
      <div className="flex gap-1.5">
        <input
          className="lh-input" autoFocus placeholder="new department name"
          value={value} onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button" className="lh-btn" title="pick from existing"
          onClick={() => { setAdding(false); onChange('') }}
        >
          ↩
        </button>
      </div>
    )
  }
  return (
    <select
      className="lh-select"
      value={known ? value : ''}
      onChange={(e) => {
        if (e.target.value === NEW_DEPT) { setAdding(true); onChange('') }
        else onChange(e.target.value)
      }}
    >
      <option value="">(none)</option>
      {options.map((d) => <option key={d} value={d}>{d}</option>)}
      <option value={NEW_DEPT}>+ New department…</option>
    </select>
  )
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

// ── carried-skills install status ───────────────────────────────────────────
// A brain can carry a skill NAME that has no installed SKILL.md yet — in which
// case the name does nothing. We compare each carried skill against the installed
// set: an installed one is a normal gold tag; a not-installed one is a dashed,
// muted tag with an Install action (→ POST /api/skills/{name}/install).
function CarriedSkills({ skills, installedSet, onInstall }) {
  const [installing, setInstalling] = useState('')
  if (!skills.length) {
    return <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>No skills carried yet.</p>
  }
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5 items-center">
        {skills.map((s) => {
          const ok = installedSet.has(String(s).toLowerCase())
          if (ok) {
            return <span key={s} className="lh-tag lh-tag-accent" title="installed SKILL.md">{s}</span>
          }
          return (
            <span key={s} className="inline-flex items-center gap-1">
              <span
                className="lh-tag"
                style={{ borderStyle: 'dashed', color: 'var(--lh-text-faint)', background: 'transparent' }}
                title="referenced but not installed"
              >
                {s}
              </span>
              <button
                type="button"
                className="lh-btn"
                style={{ padding: '0.18rem 0.5rem', fontSize: '0.6875rem' }}
                disabled={installing === s}
                onClick={async () => { setInstalling(s); await onInstall(s); setInstalling('') }}
              >
                {installing === s ? '…' : 'Install'}
              </button>
            </span>
          )
        })}
      </div>
      <p className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
        A gold tag is an installed SKILL.md. A dashed tag is only a name — it does nothing until its skill is installed.
      </p>
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
      <p className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
        ✕ or an empty value removes a limit. Click Save changes to persist.
      </p>
    </div>
  )
}

// ── inspector for the selected brain ────────────────────────────────────────
function Inspector({ node, nodes, departments, skills, installedSet, onInstall, onAddSkill, onPatch, onMove, onPromote, onDelete, busy }) {
  const [role, setRole] = useState(node.role || '')
  const [department, setDepartment] = useState(node.department || '')
  const [pickedSkills, setPickedSkills] = useState(node.skills || [])
  const [limitRows, setLimitRows] = useState(limitsToRows(node.limits))
  // The Move dropdown pre-selects the CURRENT parent — if it already reports to
  // someone, that relationship is shown selected. After a successful move the node
  // prop updates but this component is NOT remounted (key is node.id), so `moveTo`
  // keeps reflecting the chosen parent rather than snapping back to a placeholder.
  const [moveTo, setMoveTo] = useState(node.parent || '')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [recs, setRecs] = useState([])
  const [addingSkill, setAddingSkill] = useState('')

  // EVERY field is derived from the SELECTED node. The parent keys this component
  // by node.id so a new selection remounts it fresh; this effect is the explicit
  // belt-and-suspenders re-init so no field can ever show the previously-selected
  // brain's data (stale skills/limits/department/role/parent). It keys on node.id
  // only, so it fires on SELECTION change — not on a save of the same node (which
  // must keep in-progress edits).
  useEffect(() => {
    setRole(node.role || '')
    setDepartment(node.department || '')
    setPickedSkills(node.skills || [])
    setLimitRows(limitsToRows(node.limits))
    setMoveTo(node.parent || '')
    setConfirmDelete(false)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id])

  // Recommended skills for THIS brain, ranked by its department (or, if it has
  // none, its id/role) — the recommend endpoint scores catalog skills by those
  // words. Refetched whenever the selected brain or its department changes.
  useEffect(() => {
    const ctx = (node.department || node.id || node.role || '').trim()
    let cancelled = false
    fetch(`/api/skills/recommend?context=${encodeURIComponent(ctx)}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => { if (!cancelled) setRecs(Array.isArray(d) ? d.slice(0, 5) : []) })
      .catch(() => { if (!cancelled) setRecs([]) })
    return () => { cancelled = true }
  }, [node.id, node.department, node.role])

  const toggleSkill = (d) =>
    setPickedSkills((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d]))

  const byId = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes])
  const descendants = useMemo(() => descendantIds(node.id, byId), [node.id, byId])
  // Valid parents: every OTHER node that isn't a descendant (would form a cycle).
  const moveTargets = nodes.map((n) => n.id).filter((id) => id !== node.id && !descendants.has(id))
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
          <DepartmentField value={department} options={departments} onChange={setDepartment} />
        </label>
      </div>

      <div className="space-y-1">
        <span className="lh-label">skills</span>
        <SkillPicker available={skills} selected={pickedSkills} onToggle={toggleSkill} />
      </div>

      <div className="space-y-1">
        <span className="lh-label">carried — install status</span>
        <CarriedSkills skills={pickedSkills} installedSet={installedSet} onInstall={onInstall} />
      </div>

      <div className="space-y-1">
        <span className="lh-label">recommended for this brain</span>
        {recs.filter((r) => !pickedSkills.includes(r.name)).length === 0 ? (
          <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
            No suggestions{node.department ? ` for ${node.department}` : ''}.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {recs.filter((r) => !pickedSkills.includes(r.name)).map((r) => (
              <button
                key={r.name}
                type="button"
                className="lh-tag"
                style={{ cursor: 'pointer', borderStyle: 'dashed' }}
                disabled={busy || addingSkill === r.name}
                title={r.reason ? `${r.description || r.name} · ${r.reason}` : (r.description || r.name)}
                onClick={async () => {
                  setAddingSkill(r.name)
                  const ok = await onAddSkill(node.id, r.name)
                  if (ok) {
                    setPickedSkills((prev) => (prev.includes(r.name) ? prev : [...prev, r.name]))
                    setRecs((prev) => prev.filter((x) => x.name !== r.name))
                  }
                  setAddingSkill('')
                }}
              >
                {addingSkill === r.name ? '… ' : '+ '}{r.name}
              </button>
            ))}
          </div>
        )}
        <p className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
          Suggested from this brain&apos;s {node.department ? 'department' : 'name'}. Click to install and assign it.
        </p>
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
                type="button" className="lh-btn" disabled={busy || !moveTo || moveTo === node.parent}
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
function CreateForm({ allIds, departments, defaultParent, skills, onCreate, onClose, busy }) {
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
            <DepartmentField value={department} options={departments} onChange={setDepartment} />
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
  const [installed, setInstalled] = useState([]) // installed SKILL.md skills
  const [skillNotice, setSkillNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [goal, setGoal] = useState('')
  const [route, setRoute] = useState(null)
  const [routeErr, setRouteErr] = useState('')
  const [view, setView] = useState('flow') // 'flow' (n8n-style canvas) | 'tree' | 'templates'

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

  const loadInstalled = useCallback(async () => {
    try {
      const r = await fetch('/api/skills')
      const d = r.ok ? await r.json() : []
      setInstalled(Array.isArray(d) ? d : [])
    } catch {
      setInstalled([])
    }
  }, [])

  useEffect(() => {
    loadTopology()
    loadInstalled()
    fetch('/api/org/skills')
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setSkills(Array.isArray(d) ? d : []))
      .catch(() => setSkills([]))
  }, [loadTopology, loadInstalled])

  // The set a carried skill is matched against — installed skill names AND their
  // domains (the org picker keys by domain, SKILL.md by name; accept either).
  const installedSet = useMemo(() => {
    const s = new Set()
    for (const sk of installed) {
      if (sk?.name) s.add(String(sk.name).toLowerCase())
      const dom = sk?.metadata?.domain
      if (dom) s.add(String(dom).toLowerCase())
    }
    return s
  }, [installed])

  const installSkill = useCallback(async (name) => {
    setSkillNotice('')
    try {
      const r = await fetch(`/api/skills/${encodeURIComponent(name)}/install`, { method: 'POST' })
      if (r.status === 404) {
        setSkillNotice(`"${name}" isn't in the catalog — open Skills to create it with AI.`)
        return
      }
      if (!r.ok) throw new Error('install failed')
      await loadInstalled()
      setSkillNotice(`Installed "${name}".`)
    } catch {
      setSkillNotice(`Could not install "${name}".`)
    }
  }, [loadInstalled])

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
  // Add a recommended skill to a brain: install it from the catalog if needed
  // (ignore 404 — a recommendation may not be a catalog skill), then assign it.
  // The assign endpoint returns {ok}, not a chart, so we refetch the topology (and
  // the installed set) to refresh the carried-skills section.
  const addSkillToBrain = useCallback(async (brainId, name) => {
    setBusy(true)
    setError('')
    try {
      await fetch(`/api/skills/${encodeURIComponent(name)}/install`, { method: 'POST' }).catch(() => {})
      const r = await fetch(`/api/skills/${encodeURIComponent(name)}/assign`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ brain_id: brainId }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Assign failed')
      }
      await loadTopology()
      await loadInstalled()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Assign failed')
      return false
    } finally {
      setBusy(false)
    }
  }, [loadTopology, loadInstalled])

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
  // Existing departments = distinct non-empty `department` values across the chart.
  const departments = useMemo(() => {
    const set = new Set()
    for (const n of topology?.nodes || []) {
      const d = (n.department || '').trim()
      if (d) set.add(d)
    }
    return [...set].sort((a, b) => a.localeCompare(b))
  }, [topology])
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
            <button
              type="button"
              className={`lh-btn ${view === 'templates' ? 'lh-btn-primary' : ''}`}
              onClick={() => setView('templates')}
            >
              Templates
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
            allIds={allIds} departments={departments}
            defaultParent={selectedId || topology?.admin || topology?.root} skills={skills}
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
      ) : view === 'templates' ? (
        <TemplatesPicker
          onApplied={(chart) => {
            if (chart && chart.nodes) setTopology(chart)
            setView('flow')
          }}
        />
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
              <>
                <Inspector
                  key={selected.id} node={selected} nodes={nodes} departments={departments} skills={skills}
                  installedSet={installedSet} onInstall={installSkill} onAddSkill={addSkillToBrain}
                  onPatch={patchBrain} onMove={moveBrain} onPromote={promoteBrain}
                  onDelete={deleteBrain} busy={busy}
                />
                {skillNotice && (
                  <div className="text-[11px] mt-2 lh-row" style={{ color: 'var(--lh-accent-text)' }}>
                    {skillNotice}
                  </div>
                )}
              </>
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
