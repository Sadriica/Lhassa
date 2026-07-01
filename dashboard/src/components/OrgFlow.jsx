import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Panel,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { RELATION_KIND_ORDER, kindStyle, relationshipEdge } from './relationshipKinds'

// n8n-style org FLOW editor. Renders the org chart as a node graph: brains are
// custom nodes, parent→child command links are SOLID edges (the command spine).
// On TOP of that spine it draws the typed RELATIONSHIP overlay (reads / notifies /
// reviews / collaborates) as dashed, animated, per-kind-colored edges with a small
// kind label and direction arrows — a legend with per-kind layer toggles lets the
// owner show/hide each kind so the two graphs never get confused. Lhassa (the admin / root)
// is visually special and anchored — the sovereign: a matte gold fill with a thin
// deep-red ring, pinned at the top, with no drag-out handle (it can't be
// re-parented or deleted). Layout is a tidy layered
// top-down tree computed from depth; no heavy layout dep.
//
// Interactions wired back to BrainsPanel (which owns the API calls):
//   • click a node            → onSelect(id)        (drives the side inspector)
//   • drag A's bottom → B     → onMove(A, B)        re-parent A under B
//     (blocked when it would form a cycle, target itself, or move the admin)

// ── layout: a tidy top-down layered tree ──────────────────────────────────────
const NODE_W = 210
const H_GAP = 36
const V_GAP = 116
const ROW_H = 150 // generous vertical step (nodes vary in height)

function computeLayout(topology) {
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
const NODE_H_EST = 132 // generous node-height estimate for bbox math
const DEPT_PAD = 26
const DEPT_LABEL_H = 16
// Muted, warm, on-palette tints — red (162,62,52) stays reserved for the sovereign.
const DEPT_COLORS = [
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
const LAYOUT_KEY = 'lh-org-layout'
function loadOffsets() {
  try {
    if (typeof localStorage === 'undefined') return {}
    const raw = localStorage.getItem(LAYOUT_KEY)
    if (!raw) return {}
    const o = JSON.parse(raw)
    if (!o || typeof o !== 'object') return {}
    // keep only well-formed {dx,dy} numeric entries
    const clean = {}
    for (const [k, v] of Object.entries(o)) {
      if (v && Number.isFinite(v.dx) && Number.isFinite(v.dy)) clean[k] = { dx: v.dx, dy: v.dy }
    }
    return clean
  } catch { return {} }
}
function saveOffsets(o) {
  try {
    if (typeof localStorage === 'undefined') return
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(o))
  } catch { /* storage unavailable — arrangement just won't persist */ }
}

function computeDeptGroups(topology, pos) {
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

    const width = (maxX - minX) + DEPT_PAD * 2
    const height = (maxY - minY) + DEPT_PAD * 2 + DEPT_LABEL_H
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

// A non-interactive, rounded, tinted rectangle with an uppercase tracked label.
function DeptGroupNode({ data }) {
  const { department, color, width, height } = data
  return (
    <div
      title={`Drag to move the ${department} department`}
      style={{
        width,
        height,
        boxSizing: 'border-box',
        position: 'relative',
        border: `1px solid rgba(${color},0.32)`,
        background: `rgba(${color},0.05)`,
        borderRadius: 18,
        // Inherit the box's grab affordance; 'auto' lets the wrapper receive the drag.
        pointerEvents: 'auto',
        cursor: 'grab',
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 6,
          left: 14,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.16em',
          textTransform: 'uppercase',
          color: `rgba(${color},0.92)`,
        }}
      >
        {/* grip glyph → discoverable "you can drag this box" affordance */}
        <span aria-hidden="true" style={{ letterSpacing: 0, opacity: 0.7, fontSize: 11 }}>⠿</span>
        {department}
      </span>
    </div>
  )
}

function formatLimit(v) {
  if (Array.isArray(v)) return v.join(',')
  if (v === null || v === undefined) return '—'
  return String(v)
}

// ── custom brain node ─────────────────────────────────────────────────────────
function BrainFlowNode({ data }) {
  const { node, selected, isAdmin, inRoute, isRouteLeaf, deptColor, deptTinted } = data
  const limits = node.limits || {}
  const limitKeys = Object.keys(limits)

  // The admin (Lhassa) is the sovereign — deep-red ring over a gold-tinted fill.
  // A scattered-department brain (no group box) carries its department tint on the
  // border so it's still identifiable; grouped brains keep the neutral border (the
  // surrounding box already marks them).
  const borderColor = isAdmin
    ? 'rgba(162,62,52,0.85)'
    : selected
      ? 'rgba(217,165,33,0.75)'
      : isRouteLeaf
        ? 'rgba(217,165,33,0.7)'
        : inRoute
          ? 'rgba(217,165,33,0.4)'
          : (deptTinted && deptColor)
            ? `rgba(${deptColor},0.6)`
            : 'var(--lh-border-strong)'

  // Matte: the admin node keeps a tasteful gold fill + a thin deep-red ring (no
  // glow); selection is a crisp gold hairline ring, not a blur halo.
  const glow = isAdmin
    ? '0 0 0 1px var(--lh-red-deep), var(--lh-shadow)'
    : selected
      ? '0 0 0 1px rgba(217,165,33,0.5), var(--lh-shadow)'
      : 'var(--lh-shadow)'

  return (
    <div
      style={{
        width: NODE_W,
        background: isAdmin
          ? 'rgba(217,165,33,0.12)'
          : 'var(--lh-surface)',
        border: `1px solid ${isAdmin ? 'rgba(217,165,33,0.55)' : borderColor}`,
        borderRadius: 'var(--lh-radius)',
        padding: '0.6rem 0.7rem',
        boxShadow: glow,
        cursor: 'pointer',
      }}
    >
      {/* target (top): a connection ends here → this node becomes the new parent */}
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: 'var(--lh-accent)', width: 8, height: 8, border: 'none' }}
      />
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="lh-mono" style={{ fontSize: 13, fontWeight: 600, color: 'var(--lh-text)' }}>
          {node.id}
        </span>
        {isAdmin && (
          <span
            className="lh-tag"
            title="Lhassa — commands everyone"
            style={{
              borderColor: 'rgba(162,62,52,0.6)',
              background: 'rgba(162,62,52,0.16)',
              color: 'var(--lh-gold-soft)',
            }}
          >
            ◆ admin
          </span>
        )}
        {isRouteLeaf && (
          <span className="lh-tag" style={{ borderColor: 'rgba(217,165,33,0.5)', color: 'var(--lh-warn)' }}>
            route target
          </span>
        )}
        {node.is_leaf && !isAdmin && (
          <span style={{ fontSize: 10, color: 'var(--lh-text-faint)' }}>leaf</span>
        )}
        {node.department && !isAdmin && (
          <span
            className="lh-tag"
            title={`department · ${node.department}`}
            style={{
              borderColor: `rgba(${deptColor || '217,165,33'},0.5)`,
              background: `rgba(${deptColor || '217,165,33'},0.12)`,
              color: 'var(--lh-text-muted)',
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              fontSize: 9,
            }}
          >
            {node.department}
          </span>
        )}
      </div>

      <div style={{ fontSize: 11, marginTop: 2, color: 'var(--lh-text-muted)' }}>
        {node.role || 'department'}
      </div>

      <div style={{ fontSize: 10, marginTop: 2, color: 'var(--lh-text-faint)' }}>
        {node.parent ? `reports to ${node.parent}` : 'root · commands everyone'}
      </div>

      {(node.skills?.length > 0 || limitKeys.length > 0) && (
        <div className="flex flex-wrap gap-1" style={{ marginTop: 6 }}>
          {(node.skills || []).map((s) => (
            <span key={s} className="lh-tag lh-tag-accent">{s}</span>
          ))}
          {limitKeys.map((k) => (
            <span key={k} className="lh-badge" title="limit">{k}={formatLimit(limits[k])}</span>
          ))}
        </div>
      )}

      {/* source (bottom): drag from here to another node to re-parent under it.
          The admin is anchored — it gets no source handle, so it can't be moved. */}
      {!isAdmin && (
        <Handle
          type="source"
          position={Position.Bottom}
          style={{ background: 'var(--lh-accent)', width: 8, height: 8, border: 'none' }}
        />
      )}
    </div>
  )
}

const nodeTypes = { brain: BrainFlowNode, deptGroup: DeptGroupNode }

// ── relationship overlay legend + layer toggles ────────────────────────────────
// A small on-palette card pinned to the canvas. Each typed kind shows its dashed
// per-kind swatch (with the direction arrow — a double arrow for the symmetric
// `collaborates`) and a checkbox that shows/hides that layer so the owner isn't
// overwhelmed. The command spine is intentionally NOT in here — it's the solid
// hierarchy, not a toggleable overlay.
function KindSwatch({ kind }) {
  const s = kindStyle(kind)
  return (
    <svg width="26" height="9" style={{ flex: '0 0 auto' }} aria-hidden="true">
      {s.symmetric && (
        <path d="M6 4.5 L1 4.5 M3.5 2.5 L1 4.5 L3.5 6.5" stroke={s.color} strokeWidth="1.2" fill="none" />
      )}
      <line x1="3" y1="4.5" x2="23" y2="4.5" stroke={s.color} strokeWidth="1.6" strokeDasharray={s.dash} />
      <path d="M20 2.5 L25 4.5 L20 6.5" stroke={s.color} strokeWidth="1.2" fill="none" />
    </svg>
  )
}

function RelationshipLegend({ kinds, kindMeta, hiddenKinds, onToggle }) {
  const metaFor = (k) => (kindMeta || []).find((m) => m.kind === k)
  return (
    <div
      style={{
        background: 'var(--lh-surface)',
        border: '1px solid var(--lh-border)',
        borderRadius: 'var(--lh-radius)',
        boxShadow: 'var(--lh-shadow)',
        padding: '0.45rem 0.55rem',
        minWidth: 132,
      }}
    >
      <div
        style={{
          fontSize: 9,
          fontWeight: 600,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          color: 'var(--lh-text-muted)',
          marginBottom: 5,
        }}
      >
        relationships
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {kinds.map((k) => {
          const m = metaFor(k)
          const visible = !hiddenKinds.has(k)
          return (
            <label
              key={k}
              title={m?.description || ''}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                cursor: 'pointer',
                opacity: visible ? 1 : 0.45,
              }}
            >
              <input
                type="checkbox"
                checked={visible}
                onChange={() => onToggle(k)}
                style={{ accentColor: kindStyle(k).color, width: 12, height: 12, cursor: 'pointer' }}
              />
              <KindSwatch kind={k} />
              <span style={{ fontSize: 11, color: 'var(--lh-text)' }}>
                {kindStyle(k).label}
                <span style={{ color: 'var(--lh-text-faint)', marginLeft: 3 }}>
                  {m?.symmetric || kindStyle(k).symmetric ? '⇄' : '→'}
                </span>
              </span>
            </label>
          )
        })}
      </div>
      <div style={{ fontSize: 9, color: 'var(--lh-text-faint)', marginTop: 5, lineHeight: 1.3 }}>
        Solid = command. Dashed = overlay.
      </div>
    </div>
  )
}

// Descendants of `id` (so we can block creating a cycle on re-parent).
function descendantsOf(id, byId) {
  const out = new Set()
  const walk = (cur) => {
    ;(byId[cur]?.children || []).forEach((c) => {
      if (!out.has(c)) { out.add(c); walk(c) }
    })
  }
  walk(id)
  return out
}

function FlowInner({ topology, selectedId, routePath, relationships, kindMeta, onSelect, onMove }) {
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState([])
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([])

  const byId = useMemo(() => {
    const m = {}
    ;(topology?.nodes || []).forEach((n) => { m[n.id] = n })
    return m
  }, [topology])

  const routeSet = useMemo(() => new Set(routePath || []), [routePath])
  const admin = topology?.admin
  const rf = useReactFlow()

  // #12 — the tidy-tree layout and the department bounding-box math are the heavy
  // derivations. Memoize them on the topology so re-selecting a node or toggling a
  // relationship layer (edges-only churn) never recomputes the whole layout.
  const layout = useMemo(() => computeLayout(topology), [topology])
  const { groupNodes, deptStyle, nodeDept } = useMemo(
    () => computeDeptGroups(topology, layout),
    [topology, layout],
  )

  // Per-department drag offsets {dept: {dx,dy}} — the user's manual arrangement,
  // seeded from localStorage so it survives reloads/Refresh. Applied to each dept
  // box AND its member brains so the cluster moves as a unit.
  const [deptOffsets, setDeptOffsets] = useState(loadOffsets)
  // Base (un-offset) box position per department, so a drag's absolute offset is
  // draggedPosition − basePosition regardless of any previously-applied offset.
  const baseBoxByDept = useMemo(() => {
    const m = {}
    groupNodes.forEach((g) => { m[g.data.department] = g.position })
    return m
  }, [groupNodes])
  // Bumped by Reset to re-fit the graph (fitView otherwise ignores offset changes).
  const [refitTick, setRefitTick] = useState(0)

  // Legend layers: which relationship KINDS are visible. Default all on; the owner can
  // hide a kind to declutter. Stored as the HIDDEN set so a newly-seen kind defaults on.
  const legendKinds = useMemo(() => {
    const ks = kindMeta && kindMeta.length ? kindMeta.map((k) => k.kind) : RELATION_KIND_ORDER
    return ks
  }, [kindMeta])
  const [hiddenKinds, setHiddenKinds] = useState(() => new Set())
  const toggleKind = useCallback((k) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }, [])

  // (Re)build NODES whenever the chart, selection, or route changes. Kept separate from
  // edges so toggling a relationship layer (an edges-only change) never churns nodes
  // (which would reset any cosmetic drag the owner nudged).
  useEffect(() => {
    const nodes = topology?.nodes || []
    const routeLeaf = routePath && routePath.length ? routePath[routePath.length - 1] : null

    // Apply the manual department offset to a base position, if the node/box is in
    // a boxed (draggable) department that the user has moved.
    const withOffset = (base, dept) => {
      const off = dept ? deptOffsets[dept] : null
      return off ? { x: base.x + off.dx, y: base.y + off.dy } : base
    }

    const brainNodes = nodes.map((n) => ({
      id: n.id,
      type: 'brain',
      position: withOffset(layout[n.id] || { x: 0, y: 0 }, nodeDept[n.id]),
      zIndex: 1, // above the department group boxes
      data: {
        node: n,
        selected: n.id === selectedId,
        isAdmin: n.is_admin || n.id === admin,
        inRoute: routeSet.has(n.id),
        isRouteLeaf: n.id === routeLeaf,
        deptColor: deptStyle[n.id]?.color,
        deptTinted: deptStyle[n.id]?.tinted,
      },
      // keep the admin pinned/anchored; others can be nudged (cosmetic only)
      draggable: !(n.is_admin || n.id === admin),
    }))

    // Department boxes carry their own offset too, so box + members stay aligned.
    const offsetGroupNodes = groupNodes.map((g) => ({
      ...g,
      position: withOffset(g.position, g.data.department),
    }))

    // Group boxes first so they paint behind the brains they enclose.
    setRfNodes([...offsetGroupNodes, ...brainNodes])
  }, [topology, selectedId, routePath, routeSet, admin, layout, groupNodes, deptStyle, nodeDept, deptOffsets, setRfNodes])

  // #7 — keep freshly-created/refreshed nodes on screen. A signature of the node-id
  // SET (not positions) so this fires only when brains are added/removed, not on a
  // cosmetic drag. When a brain is selected we center IT (dock-aware, below) instead
  // of refitting the whole graph.
  const nodeIdSig = useMemo(
    () => (topology?.nodes || []).map((n) => n.id).sort().join('|'),
    [topology],
  )

  // #7a — after the node set changes, fit the whole graph so new departments/brains
  // aren't rendered off-screen. #7b — when a node is selected (incl. a just-created
  // one), pan it into the VISIBLE area: on desktop the inspector docks on the right,
  // so we bias the node left of centre so the dock never covers it. Pan/zoom stay
  // usable — this only nudges on a set/selection change, animated.
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      const target = selectedId && rf.getNode(selectedId)
      if (target) {
        const zoom = rf.getViewport().zoom || 1
        const dockPx = typeof window !== 'undefined' && window.innerWidth >= 768 ? 380 : 0
        const w = target.measured?.width || NODE_W
        const h = target.measured?.height || NODE_H_EST
        const cx = target.position.x + w / 2 + (dockPx / 2) / zoom
        const cy = target.position.y + h / 2
        rf.setCenter(cx, cy, { zoom, duration: 350 })
      } else {
        rf.fitView({ padding: 0.2, duration: 350 })
      }
    })
    return () => cancelAnimationFrame(raf)
    // refitTick: bumped by Reset layout so the graph re-frames after offsets clear.
    // Manual drag offsets deliberately do NOT re-fit (they'd fight the drag).
  }, [nodeIdSig, selectedId, rf, refitTick])

  // (Re)build EDGES: the solid command spine (parent→child, gold on a route) PLUS the
  // typed relationship overlay (dashed/animated, per-kind color + label), filtered by
  // the visible legend layers. Both endpoints must still be real nodes.
  useEffect(() => {
    const nodes = topology?.nodes || []
    const edges = []
    nodes.forEach((n) => {
      if (n.parent && byId[n.parent]) {
        const onRoute = routeSet.has(n.id) && routeSet.has(n.parent)
        edges.push({
          id: `${n.parent}->${n.id}`,
          source: n.parent,
          target: n.id,
          animated: onRoute,
          style: {
            stroke: onRoute ? 'var(--lh-warn)' : 'var(--lh-border-strong)',
            strokeWidth: onRoute ? 2 : 1.5,
          },
        })
      }
    })
    const overlay = (relationships || [])
      .filter((r) => byId[r.from] && byId[r.to] && !hiddenKinds.has(r.kind))
      .map((r, i) => relationshipEdge(r, i))
    setRfEdges([...edges, ...overlay])
  }, [topology, routeSet, byId, relationships, hiddenKinds, setRfEdges])

  // Live validity: forbid self, cycles, and moving the anchored admin.
  const isValidConnection = useCallback((conn) => {
    const { source, target } = conn
    if (!source || !target || source === target) return false
    if (source === admin) return false // admin is anchored
    if (target === source) return false
    const desc = descendantsOf(source, byId)
    if (desc.has(target)) return false // would create a cycle
    if (byId[source]?.parent === target) return false // already there
    return true
  }, [admin, byId])

  // Drag A(bottom/source) → B(top/target): re-parent A under B.
  const onConnect = useCallback((conn) => {
    if (!isValidConnection(conn)) return
    onMove(conn.source, conn.target)
  }, [isValidConnection, onMove])

  const onNodeClick = useCallback((_e, node) => {
    if (node.type === 'deptGroup') return // the box is a layout handle, not selectable
    onSelect(node.id)
  }, [onSelect])

  // ── department-box drag: move the whole cluster as a unit ──────────────────────
  // While a department box is dragged, React Flow owns the BOX node's position; we
  // live-move only its member brains so the cluster tracks the box. On drop we commit
  // the department's absolute offset (dragged − base) to state + localStorage; the
  // node-build effect then re-lays box + members consistently (no snap). Brain-node
  // drags fall through untouched (cosmetic; re-parent still happens via the handle).
  const onNodeDrag = useCallback((_e, node) => {
    if (node.type !== 'deptGroup') return
    const dept = node.data.department
    const base = baseBoxByDept[dept]
    if (!base) return
    const off = { dx: node.position.x - base.x, dy: node.position.y - base.y }
    setRfNodes((cur) => cur.map((rn) => {
      if (nodeDept[rn.id] !== dept) return rn
      const b = layout[rn.id]
      if (!b) return rn
      return { ...rn, position: { x: b.x + off.dx, y: b.y + off.dy } }
    }))
  }, [baseBoxByDept, nodeDept, layout, setRfNodes])

  const onNodeDragStop = useCallback((_e, node) => {
    if (node.type !== 'deptGroup') return
    const dept = node.data.department
    const base = baseBoxByDept[dept]
    if (!base) return
    const off = { dx: node.position.x - base.x, dy: node.position.y - base.y }
    setDeptOffsets((prev) => {
      const next = { ...prev, [dept]: off }
      saveOffsets(next)
      return next
    })
  }, [baseBoxByDept])

  const resetLayout = useCallback(() => {
    setDeptOffsets({})
    saveOffsets({})
    setRefitTick((t) => t + 1)
  }, [])
  const hasOffsets = Object.keys(deptOffsets).length > 0

  // Right-click a node → a floating "Move under ▸" menu of VALID new parents. This
  // makes re-parenting reachable without a drag (and usable on trackpads/touch via
  // a long-press → contextmenu). Validity reuses isValidConnection — no cycles, not
  // itself, not its current parent, admin anchored — so the menu can never offer an
  // illegal move. The admin node has no valid targets (it's pinned at the root).
  const [menu, setMenu] = useState(null) // { x, y, nodeId } | null
  const onNodeContextMenu = useCallback((e, node) => {
    if (node.type === 'deptGroup') return // no re-parent menu for a layout box
    e.preventDefault()
    setMenu({ x: e.clientX, y: e.clientY, nodeId: node.id })
  }, [])
  const onPaneContextMenu = useCallback(() => setMenu(null), [])

  // Dismiss the menu on any outside click / Esc.
  useEffect(() => {
    if (!menu) return undefined
    const onDown = () => setMenu(null)
    const onKey = (e) => { if (e.key === 'Escape') setMenu(null) }
    window.addEventListener('click', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu])

  const menuIsAdmin = menu ? (menu.nodeId === admin || byId[menu.nodeId]?.is_admin) : false
  const menuTargets = useMemo(() => {
    if (!menu || menuIsAdmin) return []
    return (topology?.nodes || [])
      .filter((n) => isValidConnection({ source: menu.nodeId, target: n.id }))
      .map((n) => n.id)
  }, [menu, menuIsAdmin, topology, isValidConnection])

  return (
    <>
      {menu && (
        <div
          onClick={(e) => e.stopPropagation()}
          role="menu"
          style={{
            position: 'fixed',
            top: menu.y,
            left: menu.x,
            zIndex: 50,
            minWidth: 190,
            maxHeight: 320,
            overflowY: 'auto',
            background: 'var(--lh-surface)',
            border: '1px solid var(--lh-border)',
            borderRadius: 'var(--lh-radius)',
            boxShadow: 'var(--lh-shadow)',
            padding: '0.35rem',
          }}
        >
          <div
            style={{
              fontSize: 9,
              fontWeight: 600,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              color: 'var(--lh-text-muted)',
              padding: '0.15rem 0.4rem 0.35rem',
            }}
          >
            <span className="lh-mono" style={{ textTransform: 'none', letterSpacing: 0, color: 'var(--lh-text)' }}>
              {menu.nodeId}
            </span>
            {' · move under ▸'}
          </div>
          {menuIsAdmin ? (
            <div style={{ fontSize: 11, color: 'var(--lh-text-faint)', padding: '0.3rem 0.4rem' }}>
              Lhassa is anchored at the root — it can&apos;t be moved.
            </div>
          ) : menuTargets.length === 0 ? (
            <div style={{ fontSize: 11, color: 'var(--lh-text-faint)', padding: '0.3rem 0.4rem' }}>
              No valid parent to move under.
            </div>
          ) : (
            menuTargets.map((pid) => (
              <button
                key={pid}
                type="button"
                role="menuitem"
                className="lh-mono"
                onClick={() => { onMove(menu.nodeId, pid); setMenu(null) }}
                onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--lh-bg-2)' }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  color: 'var(--lh-text)',
                  fontSize: 12,
                  padding: '0.3rem 0.4rem',
                  borderRadius: 6,
                }}
              >
                {pid}
              </button>
            ))
          )}
        </div>
      )}
    <div
      className="lh-flow-canvas"
      style={{
        // Fluid, viewport-driven height: never cramped, never absurdly tall.
        height: 'clamp(440px, 72vh, 880px)',
        border: '1px solid var(--lh-border)',
        borderRadius: 'var(--lh-radius)',
        overflow: 'hidden',
        background: 'var(--lh-bg)',
      }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onNodeClick={onNodeClick}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={1.75}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
      >
        <Background color="rgba(217,165,33,0.07)" gap={22} />
        <Panel position="top-left">
          <RelationshipLegend
            kinds={legendKinds}
            kindMeta={kindMeta}
            hiddenKinds={hiddenKinds}
            onToggle={toggleKind}
          />
        </Panel>
        {/* Layout controls: drag a department box to arrange the canvas; Reset returns
            to pure auto-layout. Shown only once the owner has moved something. */}
        <Panel position="top-right">
          {hasOffsets ? (
            <button
              type="button"
              className="lh-btn"
              onClick={resetLayout}
              title="Clear the manual department arrangement and re-fit the graph"
            >
              ⤺ Reset layout
            </button>
          ) : (
            <span
              style={{
                fontSize: 10,
                color: 'var(--lh-text-faint)',
                background: 'var(--lh-surface)',
                border: '1px solid var(--lh-border)',
                borderRadius: 'var(--lh-radius)',
                padding: '0.3rem 0.5rem',
                letterSpacing: '0.04em',
              }}
            >
              Drag a department box to arrange
            </span>
          )}
        </Panel>
        <MiniMap
          pannable
          zoomable
          style={{ background: 'var(--lh-surface)', border: '1px solid var(--lh-border)' }}
          maskColor="rgba(16,14,9,0.66)"
          nodeColor={(n) => (n.data?.isAdmin ? 'var(--lh-gold)' : 'var(--lh-surface-3)')}
          nodeStrokeColor={(n) => (n.data?.isAdmin ? 'var(--lh-red)' : 'transparent')}
        />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
    </>
  )
}

export default function OrgFlow(props) {
  const nodes = props.topology?.nodes || []
  if (nodes.length === 0) {
    return (
      <div className="space-y-2">
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>
          No brains yet. Create the first one to start the org chart.
        </p>
        {props.onCreate && (
          <button type="button" className="lh-btn lh-btn-primary" onClick={props.onCreate}>
            + Create the first brain
          </button>
        )}
      </div>
    )
  }
  return (
    <ReactFlowProvider>
      <FlowInner {...props} />
    </ReactFlowProvider>
  )
}
