// ── layout: a tidy left→right layered graph computed from the edges ─────────────
// Pure geometry + reachability helpers for the work-flow canvas. No React, no
// side effects — the editor calls these to place nodes and to flag orphans.
export const NODE_W = 184
export const COL_GAP = 92
export const ROW_GAP = 28
export const ROW_H = 96

// `direction`: 'horizontal' | 'vertical' | undefined. When undefined we AUTO-pick:
// a long / deep chain lays out vertically (top→bottom) so a 10-node linear flow is
// readable without endless horizontal panning; short/wide graphs stay left→right.
export function computeLayout(nodes, edges, direction) {
  const byId = {}
  nodes.forEach((n) => { byId[n.id] = n })
  const indeg = {}
  nodes.forEach((n) => { indeg[n.id] = 0 })
  edges.forEach((e) => { if (e.to_node in indeg) indeg[e.to_node] += 1 })

  // Depth = longest path from a root; BFS layering keeps it acyclic-safe.
  const depth = {}
  const trigger = nodes.find((n) => n.kind === 'trigger')
  const roots = nodes.filter((n) => indeg[n.id] === 0).map((n) => n.id)
  const seeds = [...new Set([...(trigger ? [trigger.id] : []), ...roots])]
  const queue = seeds.map((id) => [id, 0])
  const seen = new Set()
  while (queue.length) {
    const [id, d] = queue.shift()
    if (!(id in byId)) continue
    depth[id] = Math.max(depth[id] ?? 0, d)
    const key = `${id}@${d}`
    if (seen.has(key)) continue
    seen.add(key)
    edges.filter((e) => e.from_node === id).forEach((e) => {
      if (e.to_node in byId) queue.push([e.to_node, d + 1])
    })
  }
  nodes.forEach((n) => { if (!(n.id in depth)) depth[n.id] = 0 })

  // Stack nodes that share a column.
  const byCol = {}
  nodes.forEach((n) => {
    const d = depth[n.id]
    ;(byCol[d] = byCol[d] || []).push(n.id)
  })
  const maxDepth = nodes.reduce((m, n) => Math.max(m, depth[n.id] || 0), 0)
  // A long chain (5+ deep) defaults to a vertical stack; otherwise left→right.
  const vertical = direction === 'vertical' || (!direction && maxDepth >= 5)
  const pos = {}
  Object.keys(byCol).forEach((d) => {
    byCol[d].forEach((id, row) => {
      pos[id] = vertical
        ? { x: row * (NODE_W + COL_GAP), y: Number(d) * (ROW_H + ROW_GAP) }
        : { x: Number(d) * (NODE_W + COL_GAP), y: row * (ROW_H + ROW_GAP) }
    })
  })
  return pos
}

// Nodes that can't be reached from the trigger — surfaced as a non-blocking
// warning so a visually-"connected" flow can't be saved silently disconnected.
// Operates on React Flow nodes/edges (kind lives under `n.data.kind`).
export function computeDisconnected(rfNodes, rfEdges) {
  const trigger = rfNodes.find((n) => n.data.kind === 'trigger')
  if (!trigger) return []
  const adj = {}
  rfEdges.forEach((e) => { (adj[e.source] = adj[e.source] || []).push(e.target) })
  const reachable = new Set([trigger.id])
  const stack = [trigger.id]
  while (stack.length) {
    const cur = stack.pop()
    ;(adj[cur] || []).forEach((t) => { if (!reachable.has(t)) { reachable.add(t); stack.push(t) } })
  }
  return rfNodes.filter((n) => n.data.kind !== 'trigger' && !reachable.has(n.id)).map((n) => n.id)
}
