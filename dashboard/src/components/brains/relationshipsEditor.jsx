import { useState } from 'react'
import { RELATION_KIND_ORDER, kindStyle } from '../relationshipKinds'

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

export function RelationshipsEditor({ node, nodes, relationships, relKinds, onAdd, onRemove, busy }) {
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
