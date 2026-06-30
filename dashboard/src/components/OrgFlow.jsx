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
// is visually special and anchored — a teal ring/glow, pinned at the top, with no
// drag-out handle (it can't be re-parented or deleted). Layout is a tidy layered
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
  const root = topology?.root
  const pos = {}
  let cursor = 0

  // Reingold–Tilford-lite: leaves take the next slot; parents center over kids.
  const place = (id, depth, seen) => {
    const node = byId[id]
    if (!node || seen.has(id)) return
    seen.add(id)
    const kids = (node.children || []).filter((c) => byId[c])
    const y = depth * ROW_H
    if (kids.length === 0) {
      pos[id] = { x: cursor * (NODE_W + H_GAP), y }
      cursor += 1
      return
    }
    kids.forEach((c) => place(c, depth + 1, seen))
    const xs = kids.map((c) => pos[c].x).filter((v) => v !== undefined)
    pos[id] = { x: xs.reduce((a, b) => a + b, 0) / xs.length, y }
  }

  const seen = new Set()
  if (root && byId[root]) place(root, 0, seen)

  // Anything unreachable from root (defensive) → an extra row at the bottom.
  const orphans = nodes.filter((n) => !seen.has(n.id))
  if (orphans.length > 0) {
    const orphanY = (Object.keys(pos).length ? Math.max(...Object.values(pos).map((p) => p.y)) : 0) + ROW_H
    orphans.forEach((n, i) => { pos[n.id] = { x: i * (NODE_W + H_GAP), y: orphanY } })
  }
  return pos
}

function formatLimit(v) {
  if (Array.isArray(v)) return v.join(',')
  if (v === null || v === undefined) return '—'
  return String(v)
}

// ── custom brain node ─────────────────────────────────────────────────────────
function BrainFlowNode({ data }) {
  const { node, selected, isAdmin, inRoute, isRouteLeaf } = data
  const limits = node.limits || {}
  const limitKeys = Object.keys(limits)

  const borderColor = selected
    ? 'rgba(45,212,191,0.7)'
    : isRouteLeaf
      ? 'rgba(251,191,36,0.7)'
      : inRoute
        ? 'rgba(251,191,36,0.45)'
        : isAdmin
          ? 'rgba(45,212,191,0.55)'
          : 'var(--lh-border-strong)'

  const glow = isAdmin
    ? '0 0 0 1px rgba(45,212,191,0.5), 0 0 22px rgba(45,212,191,0.35)'
    : selected
      ? '0 0 0 1px rgba(45,212,191,0.45)'
      : 'var(--lh-shadow)'

  return (
    <div
      style={{
        width: NODE_W,
        background: isAdmin ? 'linear-gradient(180deg, rgba(45,212,191,0.10), var(--lh-surface))' : 'var(--lh-surface)',
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
          <span className="lh-tag lh-tag-accent" title="Lhassa — commands everyone">◆ admin</span>
        )}
        {isRouteLeaf && (
          <span className="lh-tag" style={{ borderColor: 'rgba(251,191,36,0.5)', color: 'var(--lh-warn)' }}>
            route target
          </span>
        )}
        {node.is_leaf && !isAdmin && (
          <span style={{ fontSize: 10, color: 'var(--lh-text-faint)' }}>leaf</span>
        )}
      </div>

      <div style={{ fontSize: 11, marginTop: 2, color: 'var(--lh-text-muted)' }}>
        {node.role || 'department'}{node.department ? ` · ${node.department}` : ''}
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

const nodeTypes = { brain: BrainFlowNode }

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

    setRfNodes(nodes.map((n) => ({
      id: n.id,
      type: 'brain',
      position: pos[n.id] || { x: 0, y: 0 },
      data: {
        node: n,
        selected: n.id === selectedId,
        isAdmin: n.is_admin || n.id === admin,
        inRoute: routeSet.has(n.id),
        isRouteLeaf: n.id === routeLeaf,
      },
      // keep the admin pinned/anchored; others can be nudged (cosmetic only)
      draggable: !(n.is_admin || n.id === admin),
    })))

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
        <Background color="rgba(255,255,255,0.06)" gap={22} />
        <MiniMap
          pannable
          zoomable
          style={{ background: 'var(--lh-surface)', border: '1px solid var(--lh-border)' }}
          maskColor="rgba(0,0,0,0.6)"
          nodeColor={(n) => (n.data?.isAdmin ? 'var(--lh-accent)' : 'var(--lh-surface-3)')}
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
