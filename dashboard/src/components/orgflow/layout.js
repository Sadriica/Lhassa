// Pure layout + department-grouping math for the org FLOW canvas, plus the persisted
// per-department offset store. No React here — just the tidy-tree geometry, the
// department bounding-box clustering, and the localStorage offset (de)serialization.
// Kept dependency-free so the ReactFlow composition (OrgFlow.jsx) can memoize it.

// ── layout: a tidy top-down layered tree ──────────────────────────────────────
export const NODE_W = 210
export const H_GAP = 170
export const V_GAP = 170
export const ROW_H = 236 // generous vertical step (nodes vary in height)

export function computeLayout(topology) {
  const nodes = topology?.nodes || []
  const byId = {}
  nodes.forEach((n) => { byId[n.id] = n })
  const pos = {}
  let cursor = 0

  // Reingold–Tilford-lite: leaves take the next slot; parents center over kids.
  // `seen` guards cycles; we never leave a node with a NaN/undefined position
  // (React Flow silently drops those — the cause of "5 counted, 4 drawn").
  const place = (id, depth, seen) => {
    const node = byId[id]
    if (!node || seen.has(id)) return
    seen.add(id)
    const kids = (node.children || []).filter((c) => byId[c] && !seen.has(c))
    const y = depth * ROW_H
    if (kids.length === 0) {
      pos[id] = { x: cursor * (NODE_W + H_GAP), y }
      cursor += 1
      return
    }
    kids.forEach((c) => place(c, depth + 1, seen))
    const xs = kids.map((c) => pos[c]?.x).filter((v) => typeof v === 'number' && !Number.isNaN(v))
    pos[id] = {
      x: xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : cursor * (NODE_W + H_GAP),
      y,
    }
    if (!xs.length) cursor += 1
  }

  // The roots to lay out, top-down. We do NOT trust `topology.root` (it can point
  // below the admin and orphan it). The permanent administrator (Lhassa) is forced
  // FIRST so it anchors at the very top (depth 0); then every genuinely parentless
  // node (parent missing/null) seeds its own subtree — so EVERY node is drawn.
  const admin = topology?.admin
  const isRootNode = (n) => !n.parent || !byId[n.parent]
  const rootIds = []
  if (admin && byId[admin]) rootIds.push(admin)
  nodes.forEach((n) => { if (isRootNode(n) && !rootIds.includes(n.id)) rootIds.push(n.id) })

  const seen = new Set()
  rootIds.forEach((rid) => place(rid, 0, seen))

  // Anything still unreached (a detached cycle, defensive) → an extra bottom row.
  const orphans = nodes.filter((n) => !seen.has(n.id))
  if (orphans.length > 0) {
    const orphanY = (Object.keys(pos).length ? Math.max(...Object.values(pos).map((p) => p.y)) : 0) + ROW_H
    orphans.forEach((n, i) => { pos[n.id] = { x: i * (NODE_W + H_GAP), y: orphanY } })
  }
  return pos
}

// ── department grouping ────────────────────────────────────────────────────────
// Brains that share a `department` get a labeled, subtly-tinted bounding rectangle
// drawn BEHIND them (the Oz/Lhasa look). A box is only drawn when the department's
// brains form a clean cluster — i.e. no foreign brain sits inside their bounding
// box. When a department is scattered through the tree (a foreign node would be
// swallowed by the box), we fall back to a per-node tint + a colored department
// chip so it's still identifiable without an ugly overlapping rectangle. The admin
// and any department-less brains (coordinators) are never grouped.
export const NODE_H_EST = 136 // generous node-height estimate for bbox math
export const DEPT_PAD = 34
export const DEPT_LABEL_H = 24
// Muted, warm, on-palette tints — red (162,62,52) stays reserved for the sovereign.
export const DEPT_COLORS = [
  '217,165,33', // gold
  '120,140,92', // olive
  '92,128,140', // muted teal
  '150,112,164', // muted plum
  '190,138,86', // amber/brown
  '120,150,120', // sage
]

// ── persisted per-department layout offsets ─────────────────────────────────────
// The owner can drag whole DEPARTMENT boxes to tidy the canvas. Each department
// keeps a {dx,dy} pixel offset applied to its box AND to every member brain (so the
// cluster moves as one unit while member nodes keep ABSOLUTE coords — the handle-based
// re-parent path is untouched). Offsets persist here, keyed by department name, so an
// arrangement survives reloads/Refresh. Departments with no saved offset auto-layout.
export const LAYOUT_KEY = 'lh-org-layout'
function cleanDeptLayout(raw) {
  const clean = {}
  for (const [k, v] of Object.entries(raw || {})) {
    if (!v || typeof v !== 'object') continue
    const row = {}
    if (Number.isFinite(v.dx) && Number.isFinite(v.dy)) {
      row.dx = v.dx
      row.dy = v.dy
    }
    if (Number.isFinite(v.width) && Number.isFinite(v.height)) {
      row.width = v.width
      row.height = v.height
    }
    if (Object.keys(row).length) clean[k] = row
  }
  return clean
}

function cleanNodeLayout(raw) {
  const clean = {}
  for (const [k, v] of Object.entries(raw || {})) {
    if (v && Number.isFinite(v.dx) && Number.isFinite(v.dy)) clean[k] = { dx: v.dx, dy: v.dy }
  }
  return clean
}

function cleanViewport(raw) {
  if (!raw || !Number.isFinite(raw.x) || !Number.isFinite(raw.y) || !Number.isFinite(raw.zoom)) return null
  return { x: raw.x, y: raw.y, zoom: raw.zoom }
}

export function loadOrgLayout() {
  try {
    if (typeof localStorage === 'undefined') return { departments: {}, nodes: {}, viewport: null }
    const raw = localStorage.getItem(LAYOUT_KEY)
    if (!raw) return { departments: {}, nodes: {}, viewport: null }
    const o = JSON.parse(raw)
    if (!o || typeof o !== 'object') return { departments: {}, nodes: {}, viewport: null }

    // Back-compat: the old format was { deptName: { dx, dy } }.
    if (!('departments' in o) && !('nodes' in o) && !('viewport' in o)) {
      return { departments: cleanDeptLayout(o), nodes: {}, viewport: null }
    }

    return {
      departments: cleanDeptLayout(o.departments),
      nodes: cleanNodeLayout(o.nodes),
      viewport: cleanViewport(o.viewport),
    }
  } catch { return { departments: {}, nodes: {}, viewport: null } }
}

export function saveOrgLayout(layout) {
  try {
    if (typeof localStorage === 'undefined') return
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({
      departments: cleanDeptLayout(layout?.departments),
      nodes: cleanNodeLayout(layout?.nodes),
      viewport: cleanViewport(layout?.viewport),
    }))
  } catch { /* storage unavailable — arrangement just won't persist */ }
}

export function loadOffsets() {
  return loadOrgLayout().departments
}

export function saveOffsets(o) {
  const current = loadOrgLayout()
  saveOrgLayout({ ...current, departments: o || {} })
}

export function computeDeptGroups(topology, pos) {
  const nodes = topology?.nodes || []
  const admin = topology?.admin
  const byDept = new Map()
  nodes.forEach((n) => {
    const d = (n.department || '').trim()
    if (!d || n.is_admin || n.id === admin) return
    if (!byDept.has(d)) byDept.set(d, [])
    byDept.get(d).push(n.id)
  })
  const depts = [...byDept.keys()].sort((a, b) => a.localeCompare(b))
  const colorFor = (d) => DEPT_COLORS[depts.indexOf(d) % DEPT_COLORS.length]

  const groupNodes = []
  const deptStyle = {} // nodeId → { color, tinted }
  const nodeDept = {} // nodeId → department, for members of a BOXED (draggable) dept

  depts.forEach((d) => {
    const ids = byDept.get(d).filter((id) => pos[id])
    if (!ids.length) return
    const color = colorFor(d)

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    ids.forEach((id) => {
      const p = pos[id]
      minX = Math.min(minX, p.x)
      minY = Math.min(minY, p.y)
      maxX = Math.max(maxX, p.x + NODE_W)
      maxY = Math.max(maxY, p.y + NODE_H_EST)
    })

    // Contiguity: a clean box only if no FOREIGN node's center lands inside the bbox.
    const idset = new Set(ids)
    const foreignInside = nodes.some((n) => {
      if (idset.has(n.id) || !pos[n.id]) return false
      const p = pos[n.id]
      const cx = p.x + NODE_W / 2
      const cy = p.y + NODE_H_EST / 2
      return cx >= minX && cx <= maxX && cy >= minY && cy <= maxY
    })

    ids.forEach((id) => { deptStyle[id] = { color, tinted: foreignInside } })
    if (foreignInside) return // scattered → per-node tint + chip instead of a box

    // A clean box → it's a draggable layout handle; its members move with it.
    ids.forEach((id) => { nodeDept[id] = d })

    const width = Math.max(260, (maxX - minX) + DEPT_PAD * 2)
    const height = Math.max(170, (maxY - minY) + DEPT_PAD * 2 + DEPT_LABEL_H)
    groupNodes.push({
      id: `__deptgroup__:${d}`,
      type: 'deptGroup',
      position: { x: minX - DEPT_PAD, y: minY - DEPT_PAD - DEPT_LABEL_H },
      data: { department: d, color, width, height },
      width,
      height,
      selectable: false,
      // Draggable: dragging the box repositions the whole department cluster.
      draggable: true,
      connectable: false,
      deletable: false,
      focusable: false,
      zIndex: 0,
      // pointerEvents 'auto' so the box chrome (label + padding around/between the
      // brains) is grabbable; the brains sit on zIndex 1 and still catch their own
      // clicks, so only the empty box chrome starts a department drag.
      style: { pointerEvents: 'auto', cursor: 'grab', width, height },
    })
  })
  return { groupNodes, deptStyle, nodeDept }
}

// Descendants of `id` (so we can block creating a cycle on re-parent).
export function descendantsOf(id, byId) {
  const out = new Set()
  const walk = (cur) => {
    ;(byId[cur]?.children || []).forEach((c) => {
      if (!out.has(c)) { out.add(c); walk(c) }
    })
  }
  walk(id)
  return out
}
