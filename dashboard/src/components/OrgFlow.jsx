import { useCallback, useEffect, useMemo } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

// n8n-style org FLOW editor. Renders the org chart as a node graph: brains are
// custom nodes, parent→child command links are edges. Lhassa (the admin / root)
// is visually special and anchored — the sovereign: a gold fill with a thin deep-red
// ring and soft glow, pinned at the top, with no drag-out handle (it can't be
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
      draggable: false,
      connectable: false,
      deletable: false,
      focusable: false,
      zIndex: 0,
      style: { pointerEvents: 'none', width, height },
    })
  })
  return { groupNodes, deptStyle }
}

// A non-interactive, rounded, tinted rectangle with an uppercase tracked label.
function DeptGroupNode({ data }) {
  const { department, color, width, height } = data
  return (
    <div
      style={{
        width,
        height,
        boxSizing: 'border-box',
        position: 'relative',
        border: `1px solid rgba(${color},0.32)`,
        background: `rgba(${color},0.05)`,
        borderRadius: 18,
        pointerEvents: 'none',
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 6,
          left: 14,
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.16em',
          textTransform: 'uppercase',
          color: `rgba(${color},0.92)`,
        }}
      >
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

  const glow = isAdmin
    ? '0 0 0 1px rgba(162,62,52,0.7), 0 0 26px rgba(217,165,33,0.38), inset 0 1px 0 rgba(236,228,211,0.12)'
    : selected
      ? '0 0 0 1px rgba(217,165,33,0.5), 0 0 18px rgba(217,165,33,0.18)'
      : 'var(--lh-shadow)'

  return (
    <div
      style={{
        width: NODE_W,
        background: isAdmin
          ? 'linear-gradient(180deg, rgba(217,165,33,0.24), rgba(217,165,33,0.06) 60%, var(--lh-surface))'
          : 'var(--lh-surface)',
        border: `1px solid ${borderColor}`,
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

function FlowInner({ topology, selectedId, routePath, onSelect, onMove }) {
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState([])
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([])

  const byId = useMemo(() => {
    const m = {}
    ;(topology?.nodes || []).forEach((n) => { m[n.id] = n })
    return m
  }, [topology])

  const routeSet = useMemo(() => new Set(routePath || []), [routePath])
  const admin = topology?.admin

  // (Re)build nodes + edges whenever the chart, selection, or route changes.
  useEffect(() => {
    const nodes = topology?.nodes || []
    const pos = computeLayout(topology)
    const routeLeaf = routePath && routePath.length ? routePath[routePath.length - 1] : null
    // Department grouping: tinted boxes behind contiguous clusters; per-node tint
    // for scattered ones. Recomputed here so it tracks every layout/topology change.
    const { groupNodes, deptStyle } = computeDeptGroups(topology, pos)

    const brainNodes = nodes.map((n) => ({
      id: n.id,
      type: 'brain',
      position: pos[n.id] || { x: 0, y: 0 },
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

    // Group boxes first so they paint behind the brains they enclose.
    setRfNodes([...groupNodes, ...brainNodes])

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
    setRfEdges(edges)
  }, [topology, selectedId, routePath, routeSet, admin, byId, setRfNodes, setRfEdges])

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

  const onNodeClick = useCallback((_e, node) => onSelect(node.id), [onSelect])

  return (
    <div
      style={{
        height: 480,
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
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={1.75}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
      >
        <Background color="rgba(217,165,33,0.07)" gap={22} />
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
  )
}

export default function OrgFlow(props) {
  const nodes = props.topology?.nodes || []
  if (nodes.length === 0) {
    return (
      <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>
        No brains yet. Create the first one to start the org chart.
      </p>
    )
  }
  return (
    <ReactFlowProvider>
      <FlowInner {...props} />
    </ReactFlowProvider>
  )
}
