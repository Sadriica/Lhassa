// ── edge wiring + edge-visual helpers (pure) ────────────────────────────────────
// Where a freshly-added palette node auto-connects FROM, plus the label/style an
// edge gets from its optional `when` condition. No React — the editor closures
// (addNode / onConnect / updateEdgeWhen / initialGraph) call these.
import { kindMeta } from '../flowNodeKinds'

// Pick a sensible node to auto-wire a freshly-added node FROM: the currently
// selected node (if it can emit), else the nearest "tail" — a node with a source
// handle and no outgoing edge (most-recently-added first), else the last emitter.
export function pickAutoSource(nodes, edges, selId) {
  const byId = {}
  nodes.forEach((n) => { byId[n.id] = n })
  const canEmit = (id) => Boolean(byId[id] && kindMeta(byId[id].data.kind).hasSource)
  if (selId && canEmit(selId)) return selId
  const hasOutgoing = new Set(edges.map((e) => e.source))
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    if (canEmit(nodes[i].id) && !hasOutgoing.has(nodes[i].id)) return nodes[i].id
  }
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    if (canEmit(nodes[i].id)) return nodes[i].id
  }
  return null
}

export function describeWhen(when) {
  if (!when || !when.op) return ''
  const f = when.field || '·'
  if (when.op === 'truthy' || when.op === 'falsy') return `${f} ${when.op}`
  return `${f} ${when.op} ${when.value ?? ''}`
}

export function edgeStyle(when) {
  const dashed = Boolean(when && when.op)
  return {
    stroke: dashed ? 'rgba(150,112,164,0.85)' : 'var(--lh-border-strong)',
    strokeWidth: dashed ? 2 : 1.6,
    strokeDasharray: dashed ? '5 4' : undefined,
  }
}
