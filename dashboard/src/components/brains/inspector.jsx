import { useEffect, useMemo, useState } from 'react'
import { toast } from '../../lib/toast'
import { DepartmentField } from './departmentField'
import { CarriedSkills } from './carriedSkills'
import { LimitsEditor } from './limitsEditor'
import { RelationshipsEditor } from './relationshipsEditor'
import { descendantIds } from './topology'
import { limitsToRows, rowsToLimits } from './limits'

// ── inspector for the selected brain ────────────────────────────────────────
export function Inspector({ node, nodes, departments, installedSet, onInstall, onAddSkill, onRemoveSkill, onPatch, onMove, onPromote, onDelete, relationships, relKinds, onAddRel, onRemoveRel, busy }) {
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
  }, [node.id, (node.skills || []).join('')])

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
export function InspectorPopup({ onClose, children }) {
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
