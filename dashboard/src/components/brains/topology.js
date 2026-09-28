// ── tree helper ───────────────────────────────────────────────────────────────
// Descendants of `rootId` (so the Move dropdown can exclude them; re-parenting a
// node under its own descendant would create a cycle).
export function descendantIds(rootId, byId) {
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
// is_leaf drives a small tag; recompute it so an optimistic frame stays coherent.
function withRecomputedLeaves(nodes) {
  return nodes.map((n) => ({ ...n, is_leaf: (n.children || []).length === 0 }))
}
export function reparentTopology(topology, id, newParent) {
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
export function removeNodeLocal(topology, id) {
  if (!topology?.nodes) return topology
  const nodes = cloneNodes(topology)
    .filter((n) => n.id !== id)
    .map((n) => ({ ...n, children: (n.children || []).filter((c) => c !== id) }))
  return { ...topology, nodes: withRecomputedLeaves(nodes) }
}
export function setNodeSkills(topology, id, skills) {
  if (!topology?.nodes) return topology
  const nodes = (topology.nodes || []).map((n) => (n.id === id ? { ...n, skills: [...skills] } : n))
  return { ...topology, nodes }
}
