import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react'
import OrgTree from './OrgTree'
import TemplatesPicker from './TemplatesPicker'
import { RELATION_KIND_ORDER, kindStyle } from './relationshipKinds'
import { toast } from '../lib/toast'

// #12 — code-split the heavy React-Flow canvas out of the initial bundle. OrgFlow
// (and all of @xyflow/react) only loads when the Flow view is actually rendered.
const OrgFlow = lazy(() => import('./OrgFlow'))

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

// ── optimistic-update helpers (#10) ───────────────────────────────────────────
// Pure, immutable transforms of the topology so a mutation can be reflected on the
// canvas INSTANTLY (before the backend confirms) and rolled back exactly on failure.
// Each returns a fresh topology; the caller keeps the pre-mutation value to revert.
function cloneNodes(topology) {
  return (topology?.nodes || []).map((n) => ({ ...n, children: [...(n.children || [])] }))
}
// is_leaf drives a small tag — recompute it so an optimistic frame stays coherent.
function withRecomputedLeaves(nodes) {
  return nodes.map((n) => ({ ...n, is_leaf: (n.children || []).length === 0 }))
}
function reparentTopology(topology, id, newParent) {
  if (!topology?.nodes) return topology
  const nodes = cloneNodes(topology)
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]))
  const node = byId[id]
  if (!node) return topology
  const old = node.parent
  if (old && byId[old]) byId[old].children = byId[old].children.filter((c) => c !== id)
  node.parent = newParent || null
  if (newParent && byId[newParent] && !byId[newParent].children.includes(id)) {
    byId[newParent].children.push(id)
  }
  return { ...topology, nodes: withRecomputedLeaves(nodes) }
}
function removeNodeLocal(topology, id) {
  if (!topology?.nodes) return topology
  const nodes = cloneNodes(topology)
    .filter((n) => n.id !== id)
    .map((n) => ({ ...n, children: (n.children || []).filter((c) => c !== id) }))
  return { ...topology, nodes: withRecomputedLeaves(nodes) }
}
function setNodeSkills(topology, id, skills) {
  if (!topology?.nodes) return topology
  const nodes = (topology.nodes || []).map((n) => (n.id === id ? { ...n, skills: [...skills] } : n))
  return { ...topology, nodes }
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
    // The input is bound LIVE to `value`, so the typed name is already the field's
    // value. Confirming (↵ / Enter) just leaves type-mode and KEEPS that value —
    // it must never be discarded. ↩ goes back to the dropdown and clears.
    return (
      <div className="flex gap-1.5">
        <input
          className="lh-input" autoFocus placeholder="new department name"
          value={value} onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); if (value.trim()) setAdding(false) }
          }}
        />
        <button
          type="button" className="lh-btn" title="Use this department"
          disabled={!value.trim()}
          onClick={() => { if (value.trim()) setAdding(false) }}
        >
          ↵
        </button>
        <button
          type="button" className="lh-btn" title="Pick from existing"
          onClick={() => { setAdding(false); onChange('') }}
        >
          ↩
        </button>
      </div>
    )
  }
  // A confirmed brand-new department isn't in `options` yet — surface it as its own
  // selected option so the field shows the coined name (never snaps back to "(none)").
  const isCustom = value !== '' && !known
  return (
    <select
      className="lh-select"
      value={value}
      onChange={(e) => {
        if (e.target.value === NEW_DEPT) { setAdding(true); onChange('') }
        else onChange(e.target.value)
      }}
    >
      <option value="">(none)</option>
      {isCustom && <option value={value}>{value}</option>}
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

// ── typed relationships editor (overlay edges) ──────────────────────────────
// The selected brain's slice of the relationship OVERLAY (the typed edges that are
// NOT command hierarchy). Directed kinds split into outgoing (this brain → other)
// and incoming (other → this brain); the symmetric `collaborates` is shown as
// two-way "peers". Each edge is removable (× → DELETE); a mini-form adds one
// (pick a kind + a target brain → POST). 400/404 detail is surfaced inline. The
// parent refetches the overlay after a change so the canvas + this list both update.
function RelEdgeRow({ color, from, arrow, to, kindLabel, note, onRemove, busy }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px]" style={{ lineHeight: 1.4 }}>
      <span
        className="lh-tag lh-mono"
        title={note || kindLabel}
        style={{ borderColor: color, color, background: 'transparent' }}
      >
        {from} <span style={{ opacity: 0.7 }}>{arrow}</span> {to}
        <span style={{ opacity: 0.65, marginLeft: 4 }}>· {kindLabel}</span>
      </span>
      <button
        type="button"
        className="lh-btn"
        style={{ padding: '0.06rem 0.4rem', fontSize: '0.6875rem' }}
        title="Remove this relationship"
        disabled={busy}
        onClick={onRemove}
      >
        ✕
      </button>
    </div>
  )
}

function RelationshipsEditor({ node, nodes, relationships, relKinds, onAdd, onRemove, busy }) {
  const kinds = relKinds && relKinds.length
    ? relKinds
    : RELATION_KIND_ORDER.map((k) => ({ kind: k, symmetric: kindStyle(k).symmetric, description: '' }))
  const [kind, setKind] = useState(kinds[0]?.kind || 'reads')
  const [target, setTarget] = useState('')
  const [err, setErr] = useState('')
  const [working, setWorking] = useState(false)

  const id = node.id
  const isSym = (k) => kindStyle(k).symmetric
  // Directed edges from / to this brain, and symmetric peers (either side is this).
  const outgoing = relationships.filter((r) => r.from === id && !isSym(r.kind))
  const incoming = relationships.filter((r) => r.to === id && !isSym(r.kind))
  const peers = relationships
    .filter((r) => isSym(r.kind) && (r.from === id || r.to === id))
    .map((r) => ({ ...r, peer: r.from === id ? r.to : r.from }))
  const others = nodes.map((n) => n.id).filter((x) => x !== id)

  const doAdd = async () => {
    if (!target) return
    setWorking(true)
    setErr('')
    const res = await onAdd(id, target, kind)
    if (!res.ok) setErr(res.detail || 'Add failed')
    else setTarget('')
    setWorking(false)
  }
  const doRemove = async (from, to, k) => {
    setWorking(true)
    setErr('')
    const res = await onRemove(from, to, k)
    if (!res.ok) setErr(res.detail || 'Remove failed')
    setWorking(false)
  }

  const empty = outgoing.length === 0 && incoming.length === 0 && peers.length === 0
  const busyAll = busy || working

  return (
    <div className="pt-2 space-y-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
      <span className="lh-label">relationships — typed overlay (not command)</span>
      {empty ? (
        <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
          No typed relationships yet. Add one below (e.g. who this brain reads, notifies, or reviews).
        </p>
      ) : (
        <div className="space-y-1.5">
          {outgoing.map((r, i) => (
            <RelEdgeRow
              key={`out-${r.kind}-${r.to}-${i}`}
              color={kindStyle(r.kind).color}
              from={id} arrow="→" to={r.to} kindLabel={kindStyle(r.kind).label} note={r.note}
              busy={busyAll} onRemove={() => doRemove(id, r.to, r.kind)}
            />
          ))}
          {incoming.map((r, i) => (
            <RelEdgeRow
              key={`in-${r.kind}-${r.from}-${i}`}
              color={kindStyle(r.kind).color}
              from={r.from} arrow="→" to={id} kindLabel={kindStyle(r.kind).label} note={r.note}
              busy={busyAll} onRemove={() => doRemove(r.from, id, r.kind)}
            />
          ))}
          {peers.map((r, i) => (
            <RelEdgeRow
              key={`peer-${r.kind}-${r.peer}-${i}`}
              color={kindStyle(r.kind).color}
              from={id} arrow="⇄" to={r.peer} kindLabel={kindStyle(r.kind).label} note={r.note}
              busy={busyAll} onRemove={() => doRemove(id, r.peer, r.kind)}
            />
          ))}
        </div>
      )}

      <div className="flex gap-1.5 items-center flex-wrap">
        <select className="lh-select" style={{ flex: '0 0 auto' }} value={kind} onChange={(e) => setKind(e.target.value)}>
          {kinds.map((k) => (
            <option key={k.kind} value={k.kind}>{kindStyle(k.kind).label}{k.symmetric ? ' (⇄)' : ''}</option>
          ))}
        </select>
        <select className="lh-select" value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">target brain…</option>
          {others.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <button type="button" className="lh-btn" disabled={busyAll || !target} onClick={doAdd}>
          + Add
        </button>
      </div>
      {err && <div className="text-[11px]" style={{ color: 'var(--lh-alert)' }}>{err}</div>}
    </div>
  )
}

// ── inspector for the selected brain ────────────────────────────────────────
function Inspector({ node, nodes, departments, installedSet, onInstall, onAddSkill, onRemoveSkill, onPatch, onMove, onPromote, onDelete, relationships, relKinds, onAddRel, onRemoveRel, busy }) {
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

  // #10 — skills are now mutated INSTANTLY (the recommended assign AND the ✕ remove are
  // optimistic backend ops), so keep the assigned-skills view in lockstep with the
  // authoritative topology whenever THIS node's skill set changes — including an
  // optimistic frame, a rollback, or an Undo. Role/department/limits still re-init on
  // selection only, so in-progress edits to those are never disturbed.
  useEffect(() => {
    setPickedSkills(node.skills || [])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id, (node.skills || []).join('')])

  // Auto-disarm the Delete button a few seconds after it's armed, so a stray
  // first click never leaves a live "Confirm" primed indefinitely.
  useEffect(() => {
    if (!confirmDelete) return undefined
    const t = setTimeout(() => setConfirmDelete(false), 4000)
    return () => clearTimeout(t)
  }, [confirmDelete])

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

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
        {pickedSkills.length === 0 ? (
          <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
            No skills assigned yet — add one from “recommended” below.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {pickedSkills.map((s) => (
              <button
                key={s}
                type="button"
                className="lh-tag lh-tag-accent"
                style={{ cursor: 'pointer' }}
                title="Remove this skill (you can undo)"
                disabled={busy}
                onClick={() => onRemoveSkill(node.id, s)}
              >
                {s} ✕
              </button>
            ))}
          </div>
        )}
        <p className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
          The skills this brain carries. ✕ removes one immediately — an Undo toast lets you put it back.
        </p>
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
                    toast.success(`Installed + assigned "${r.name}"`)
                  } else {
                    toast.error(`Could not assign "${r.name}"`)
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
          Suggested from this brain&apos;s {node.department ? 'department' : 'name'}. Click to install and
          assign it — this is saved immediately (no need to press Save changes).
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

      <RelationshipsEditor
        node={node} nodes={nodes} relationships={relationships} relKinds={relKinds}
        onAdd={onAddRel} onRemove={onRemoveRel} busy={busy}
      />

      {isAdmin ? (
        <div className="pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
          <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
            Lhassa is the permanent administrator — it orbits everything and is anchored at the root.
            It can't be moved, promoted, or deleted.
          </p>
        </div>
      ) : (
        <>
          <div
            className="pt-2 mt-1"
            style={{ borderTop: '1px solid var(--lh-border)' }}
          >
            <span className="lh-label">move — change who it reports to</span>
            <p className="text-[10px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>
              Drag a node onto another, right-click → Move under, or pick a parent here.
            </p>
            <div
              className="flex gap-1.5 mt-1.5 p-1.5"
              style={{
                background: 'var(--lh-surface-2)',
                border: '1px solid var(--lh-border)',
                borderRadius: 'var(--lh-radius)',
              }}
            >
              <select
                className="lh-select" style={{ flex: 1 }}
                value={moveTo} onChange={(e) => setMoveTo(e.target.value)}
              >
                <option value="">choose new parent…</option>
                {moveTargets.map((id) => <option key={id} value={id}>{id}</option>)}
              </select>
              <button
                type="button" className="lh-btn lh-btn-primary"
                disabled={busy || !moveTo || moveTo === node.parent}
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
            {(node.children?.length || 0) > 0 ? (
              // Subtree/force delete — effectively irreversible → keep the two-step
              // confirm and offer no Undo (the success toast says the subtree was removed).
              confirmDelete ? (
                <div className="flex gap-1.5 flex-1">
                  <button
                    type="button" className="lh-btn lh-btn-danger" disabled={busy}
                    onClick={() => onDelete(node.id, true)}
                  >
                    {`Delete brain + ${node.children.length} sub-brain${node.children.length === 1 ? '' : 's'}?`}
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
              )
            ) : (
              // Leaf — optimistic + undoable, so a single click is safe (Undo recreates it).
              <button
                type="button" className="lh-btn lh-btn-danger" disabled={busy}
                onClick={() => onDelete(node.id, false)}
                title="Delete this brain (you can undo)"
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

// ── inspector popup shell ───────────────────────────────────────────────────
// The inspector is no longer a permanent column — it appears only when a node is
// selected. On desktop/tablet (≥768px) it docks as a floating overlay on the right
// edge of the canvas (it sits ABOVE the canvas, never reflowing it). On mobile
// (<768px) the same content becomes a bottom sheet with a dimmed backdrop. Esc,
// the ✕, or a backdrop tap dismiss it (→ setSelectedId(null)). The canvas underneath
// keeps working; the dock has a z-index above the React-Flow controls.
function InspectorPopup({ onClose, children }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <>
      {/* Backdrop — only visible (and dimming) on mobile; inert on desktop. */}
      <div className="lh-inspector-scrim" onClick={onClose} aria-hidden="true" />
      <aside className="lh-inspector-pop" role="dialog" aria-modal="true" aria-label="Brain inspector">
        <div className="lh-inspector-pop-head">
          <span className="lh-label" style={{ margin: 0 }}>inspector</span>
          <button
            type="button"
            className="lh-btn"
            style={{ padding: '0.2rem 0.55rem' }}
            onClick={onClose}
            aria-label="Close inspector"
            title="Close (Esc)"
          >
            ✕
          </button>
        </div>
        <div className="lh-inspector-pop-body">{children}</div>
      </aside>
    </>
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

  // `keepOpen` powers "Create & add another": build a team of siblings fast by
  // keeping the form open and PREFILLED with the same parent/department/role/skills,
  // clearing only the ID so the next sibling is one field away.
  const submit = async (keepOpen = false) => {
    const ok = await onCreate({
      id: id.trim(),
      role: role.trim() || undefined,
      parent: parent || undefined,
      department: department.trim() || undefined,
      skills: pickedSkills,
      limits: rowsToLimits(limitRows),
    }, { keepOpen })
    if (ok && keepOpen) setId('')
  }

  return (
    <div className="lh-card" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="lh-card-title">New brain</h3>
        <button type="button" className="lh-btn" onClick={onClose}>✕</button>
      </div>
      <div className="space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <label className="space-y-1">
            <span className="lh-label">id *</span>
            <input className="lh-input" value={id} onChange={(e) => setId(e.target.value)} placeholder="e.g. research" />
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
        <div className="flex gap-2 flex-wrap">
          <button
            type="button" className="lh-btn lh-btn-primary" disabled={busy || !id.trim()}
            onClick={() => submit(false)}
          >
            Create brain
          </button>
          <button
            type="button" className="lh-btn" disabled={busy || !id.trim()}
            title="Create it and keep this form open (same parent + department) to add a sibling"
            onClick={() => submit(true)}
          >
            Create &amp; add another
          </button>
        </div>
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
  // The typed relationship OVERLAY (separate graph from the command tree): a flat
  // edge list for the canvas + inspector, plus the kind vocabulary for the legend.
  const [overlay, setOverlay] = useState({ relationships: [], kinds: [] })
  const [relKinds, setRelKinds] = useState([]) // [{kind, description, symmetric}]

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

  // The relationship overlay (typed edges) — same fetch/adopt pattern as topology.
  const loadOverlay = useCallback(async () => {
    try {
      const r = await fetch('/api/org/relationships')
      const d = r.ok ? await r.json() : null
      setOverlay(d && Array.isArray(d.relationships) ? d : { relationships: [], kinds: [] })
    } catch {
      setOverlay({ relationships: [], kinds: [] })
    }
  }, [])

  useEffect(() => {
    loadTopology()
    loadInstalled()
    loadOverlay()
    fetch('/api/org/skills')
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setSkills(Array.isArray(d) ? d : []))
      .catch(() => setSkills([]))
    // The kind vocabulary (legend descriptions + which kinds are symmetric).
    fetch('/api/org/relationships/kinds')
      .then((r) => (r.ok ? r.json() : { kinds: [] }))
      .then((d) => setRelKinds(Array.isArray(d.kinds) ? d.kinds : []))
      .catch(() => setRelKinds([]))
  }, [loadTopology, loadInstalled, loadOverlay])

  // #6 — the canvas can go stale when the topology/overlay changes elsewhere (another
  // tab, the CLI, a heartbeat). Real-time WS push is out of scope here; a lightweight
  // focus-refetch is enough: re-pull the chart + overlay when the tab/window regains
  // focus, debounced so a focus+visibilitychange burst fires only one fetch. The
  // listeners are cleaned up on unmount.
  useEffect(() => {
    let t = null
    const refetch = () => {
      if (document.visibilityState !== 'visible') return
      clearTimeout(t)
      t = setTimeout(() => { loadTopology(); loadOverlay() }, 300)
    }
    const onVisible = () => { if (document.visibilityState === 'visible') refetch() }
    window.addEventListener('focus', refetch)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearTimeout(t)
      window.removeEventListener('focus', refetch)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [loadTopology, loadOverlay])

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

  const createBrain = async (payload, { keepOpen = false } = {}) => {
    const ok = await mutate('Create', () =>
      fetch('/api/org/brains', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }))
    if (ok) {
      toast.success(`Created "${payload.id}"`)
      // "Create & add another" keeps the form open (and unselected) so the owner can
      // keep adding siblings; the normal path selects the new brain and closes.
      if (!keepOpen) { setShowCreate(false); setSelectedId(payload.id) }
    } else {
      toast.error(`Could not create "${payload.id}"`)
    }
    return ok
  }
  const patchBrain = (id, payload) =>
    mutate('Save', () =>
      fetch(`/api/org/brains/${encodeURIComponent(id)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }))
  // #10 — Undo a move: put the brain back under its PREVIOUS parent (promote if it was
  // a root). Non-optimistic (adopts the canonical chart), so the restore is exact.
  const undoMove = useCallback(async (id, prevParent) => {
    if (prevParent) {
      const ok = await mutate('Move', () =>
        fetch(`/api/org/brains/${encodeURIComponent(id)}/move`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parent: prevParent }),
        }))
      if (ok) toast.success(`Moved "${id}" back under "${prevParent}"`)
      else toast.error(`Could not move "${id}" back`)
    } else {
      const ok = await mutate('Promote', () =>
        fetch(`/api/org/brains/${encodeURIComponent(id)}/promote`, { method: 'POST' }))
      if (ok) toast.success(`Moved "${id}" back to root`)
      else toast.error(`Could not move "${id}" back`)
    }
  }, [mutate])

  // #10 — recreate a just-deleted LEAF from its cached data (Undo). Reuses the create
  // path; on success the restored brain is re-selected so it's visible again.
  const recreateBrain = useCallback(async (cached) => {
    if (!cached) return
    const ok = await mutate('Create', () =>
      fetch('/api/org/brains', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: cached.id,
          role: cached.role || undefined,
          parent: cached.parent || undefined,
          department: cached.department || undefined,
          skills: cached.skills || [],
          limits: cached.limits || {},
        }),
      }))
    if (ok) { setSelectedId(cached.id); toast.success(`Restored "${cached.id}"`) }
    else toast.error(`Could not restore "${cached.id}"`)
  }, [mutate])

  // #10 — Undo a skill removal: restore the EXACT prior skill set via PATCH.
  const reAddSkill = useCallback(async (brainId, skill, prevSkills) => {
    const ok = await mutate('Assign', () =>
      fetch(`/api/org/brains/${encodeURIComponent(brainId)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skills: prevSkills }),
      }))
    if (ok) toast.success(`Re-added "${skill}"`)
    else toast.error(`Could not re-add "${skill}"`)
  }, [mutate])

  // #10 — remove a skill from a brain (the ✕): optimistic removal from the canvas, then
  // persist via PATCH (the update path replaces the skill set). The success toast offers
  // Undo → restore the prior set. On failure the optimistic topology change is reverted.
  const removeSkillFromBrain = useCallback(async (brainId, skill) => {
    const prevTopology = topology
    const node = (topology?.nodes || []).find((n) => n.id === brainId)
    if (!node) return
    const prevSkills = node.skills || []
    const nextSkills = prevSkills.filter((s) => s !== skill)
    setTopology((t) => setNodeSkills(t, brainId, nextSkills))
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`/api/org/brains/${encodeURIComponent(brainId)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skills: nextSkills }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Remove failed')
      }
      const chart = await r.json()
      if (chart && chart.nodes) setTopology(chart)
      toast.show({
        type: 'success',
        message: `Removed "${skill}" from "${brainId}"`,
        action: { label: 'Undo', onClick: () => reAddSkill(brainId, skill, prevSkills) },
      })
    } catch (e) {
      setTopology(prevTopology)
      setError(e instanceof Error ? e.message : 'Remove failed')
      toast.error(`Could not remove "${skill}"`)
    } finally {
      setBusy(false)
    }
  }, [topology, reAddSkill])

  // #10 — MOVE is optimistic + undoable. Re-parent locally the instant the drag/menu/
  // dropdown fires, then commit to the backend: adopt the canonical chart on success,
  // roll back to the exact pre-move topology on failure. The success toast carries an
  // Undo that moves the brain back under its PREVIOUS parent.
  const moveBrain = useCallback(async (id, parent) => {
    const prevTopology = topology
    const node = (topology?.nodes || []).find((n) => n.id === id)
    if (!node) return false
    const prevParent = node.parent || ''
    setTopology((t) => reparentTopology(t, id, parent))
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`/api/org/brains/${encodeURIComponent(id)}/move`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parent }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Move failed')
      }
      const chart = await r.json()
      if (chart && chart.nodes) setTopology(chart)
      toast.show({
        type: 'success',
        message: `Moved "${id}" under "${parent}"`,
        action: { label: 'Undo', onClick: () => undoMove(id, prevParent) },
      })
      return true
    } catch (e) {
      setTopology(prevTopology)
      setError(e instanceof Error ? e.message : 'Move failed')
      toast.error(`Could not move "${id}"`)
      return false
    } finally {
      setBusy(false)
    }
  }, [topology, undoMove])
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

  // #10 — DELETE. A LEAF (no children) is optimistic + undoable: it disappears from the
  // canvas immediately and the success toast offers Undo, which RECREATES it from the
  // cached (id, role, parent, department, skills, limits) via the create path. A brain
  // WITH children is a force/subtree delete — effectively irreversible, so it keeps the
  // (caller's) two-step confirm and gets a plain, non-undoable success toast that says so.
  const deleteBrain = useCallback(async (id, force) => {
    const node = (topology?.nodes || []).find((n) => n.id === id)
    if (force) {
      const ok = await mutate('Delete', () =>
        fetch(`/api/org/brains/${encodeURIComponent(id)}?force=true`, { method: 'DELETE' }))
      if (ok) {
        if (selectedId === id) setSelectedId(null)
        loadOverlay() // a removed subtree may have left dangling overlay edges
        toast.success(`Deleted "${id}" and its sub-brains — the whole subtree was removed.`)
      } else {
        toast.error(`Could not delete "${id}"`)
      }
      return
    }
    // Leaf — optimistic + undoable.
    const prevTopology = topology
    const cached = node
      ? {
          id: node.id,
          role: node.role,
          parent: node.parent,
          department: node.department,
          skills: node.skills || [],
          limits: node.limits || {},
        }
      : null
    setTopology((t) => removeNodeLocal(t, id))
    if (selectedId === id) setSelectedId(null)
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`/api/org/brains/${encodeURIComponent(id)}?force=false`, { method: 'DELETE' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Delete failed')
      }
      const chart = await r.json()
      if (chart && chart.nodes) setTopology(chart)
      loadOverlay() // a removed brain may have left dangling overlay edges
      toast.show({
        type: 'success',
        message: `Deleted "${id}"`,
        action: cached ? { label: 'Undo', onClick: () => recreateBrain(cached) } : null,
      })
    } catch (e) {
      setTopology(prevTopology)
      setError(e instanceof Error ? e.message : 'Delete failed')
      toast.error(`Could not delete "${id}"`)
    } finally {
      setBusy(false)
    }
  }, [topology, selectedId, loadOverlay, mutate, recreateBrain])

  // Relationship overlay mutations. POST/DELETE return the updated overlay (describe),
  // so we adopt the response directly — the canvas (OrgFlow) and the inspector both
  // read `overlay`, so they refresh together. Returns {ok, detail} so the inspector
  // can surface the backend's 400/404 message inline.
  const addRelationship = useCallback(async (from, to, kind) => {
    try {
      const r = await fetch('/api/org/relationships', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from, to, kind }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) return { ok: false, detail: d.detail || 'Add failed' }
      if (d && Array.isArray(d.relationships)) setOverlay(d)
      else await loadOverlay()
      return { ok: true }
    } catch {
      return { ok: false, detail: 'Add failed' }
    }
  }, [loadOverlay])

  const removeRelationship = useCallback(async (from, to, kind) => {
    try {
      const r = await fetch('/api/org/relationships', {
        method: 'DELETE', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from, to, kind }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) return { ok: false, detail: d.detail || 'Remove failed' }
      if (d && Array.isArray(d.relationships)) setOverlay(d)
      else await loadOverlay()
      return { ok: true }
    } catch {
      return { ok: false, detail: 'Remove failed' }
    }
  }, [loadOverlay])

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
        <div>
          <div className="lh-label mb-2">
            {view === 'flow'
              ? 'flow — drag a node onto another to re-parent it · click a brain to inspect'
              : 'hierarchy — who commands whom · click a brain to inspect'}
          </div>
          {/* The canvas spans the full panel width; the inspector floats over its
              right edge (desktop) or as a bottom sheet (mobile) when a node is picked. */}
          <div className="lh-canvas-stage">
            {view === 'flow' ? (
              <Suspense
                fallback={(
                  <div
                    className="lh-flow-canvas"
                    style={{
                      height: 'clamp(440px, 72vh, 880px)',
                      border: '1px solid var(--lh-border)',
                      borderRadius: 'var(--lh-radius)',
                      background: 'var(--lh-bg)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: 'var(--lh-text-faint)',
                      fontSize: 12,
                    }}
                  >
                    Loading canvas…
                  </div>
                )}
              >
                <OrgFlow
                  topology={topology} selectedId={selectedId} routePath={routePath}
                  relationships={overlay.relationships} kindMeta={relKinds}
                  onSelect={(id) => setSelectedId(id)} onMove={moveBrain}
                  onCreate={() => setShowCreate(true)}
                />
              </Suspense>
            ) : (
              <OrgTree
                topology={topology} selectedId={selectedId} routePath={routePath}
                onSelect={(id) => setSelectedId(id)}
                onCreate={() => setShowCreate(true)}
              />
            )}

            {/* Inspector — a dismissible popup, only while a brain is selected. */}
            {selected && (
              <InspectorPopup onClose={() => setSelectedId(null)}>
                <Inspector
                  key={selected.id} node={selected} nodes={nodes} departments={departments}
                  installedSet={installedSet} onInstall={installSkill} onAddSkill={addSkillToBrain}
                  onRemoveSkill={removeSkillFromBrain}
                  onPatch={patchBrain} onMove={moveBrain} onPromote={promoteBrain}
                  onDelete={deleteBrain}
                  relationships={overlay.relationships} relKinds={relKinds}
                  onAddRel={addRelationship} onRemoveRel={removeRelationship}
                  busy={busy}
                />
                {skillNotice && (
                  <div className="text-[11px] mt-2 lh-row" style={{ color: 'var(--lh-accent-text)' }}>
                    {skillNotice}
                  </div>
                )}
              </InspectorPopup>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
