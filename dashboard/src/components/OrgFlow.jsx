import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Panel,
  useNodesState,
  useEdgesState,
  useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { RELATION_KIND_ORDER, relationshipEdge } from './relationshipKinds'
import {
  NODE_W,
  NODE_H_EST,
  computeLayout,
  computeDeptGroups,
  descendantsOf,
} from './orgflow/layout'
import { BrainFlowNode, DeptGroupNode } from './orgflow/nodes'
import { RelationshipLegend } from './orgflow/legend'
import { ContextMenu } from './orgflow/menu'
import { useDeptDrag } from './orgflow/useDeptDrag'

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
// This file is the ReactFlow COMPOSITION; the pieces live beside it under `orgflow/`:
//   • orgflow/layout.js    — pure tidy-tree + department-box math + offset store
//   • orgflow/nodes.jsx    — the brain + department-group node renderers (nodeTypes)
//   • orgflow/legend.jsx   — the relationship overlay legend + layer toggles
//   • orgflow/menu.jsx     — the right-click "Move under ▸" context menu
//   • orgflow/useDeptDrag  — the draggable-department offset state + persistence + reset
//
// Interactions wired back to BrainsPanel (which owns the API calls):
//   • click a node            → onSelect(id)        (drives the side inspector)
//   • drag A's bottom → B     → onMove(A, B)        re-parent A under B
//     (blocked when it would form a cycle, target itself, or move the admin)

// The map React Flow keys custom nodes on — brains + the department-group boxes.
const nodeTypes = { brain: BrainFlowNode, deptGroup: DeptGroupNode }

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

  // Draggable department boxes: per-department {dx,dy} offset (seeded from localStorage),
  // the drag handlers that move box + members as a unit + persist on drop, Reset, and
  // the refit tick Reset bumps so the graph re-frames after offsets clear.
  const {
    deptLayouts,
    nodeOffsets,
    initialViewport,
    refitTick,
    onNodeDrag,
    onNodeDragStop,
    onDeptResizeEnd,
    onViewportSave,
    resetLayout,
    hasOffsets,
  } = useDeptDrag({
    groupNodes, layout, nodeDept, setRfNodes,
  })

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
      const off = dept ? deptLayouts[dept] : null
      return off ? { x: base.x + off.dx, y: base.y + off.dy } : base
    }

    const brainNodes = nodes.map((n) => ({
      id: n.id,
      type: 'brain',
      position: (() => {
        const base = withOffset(layout[n.id] || { x: 0, y: 0 }, nodeDept[n.id])
        const off = nodeOffsets[n.id] || { dx: 0, dy: 0 }
        return { x: base.x + off.dx, y: base.y + off.dy }
      })(),
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
      width: deptLayouts[g.data.department]?.width || g.width,
      height: deptLayouts[g.data.department]?.height || g.height,
      position: withOffset(g.position, g.data.department),
      data: {
        ...g.data,
        width: deptLayouts[g.data.department]?.width || g.data.width,
        height: deptLayouts[g.data.department]?.height || g.data.height,
        onResizeEnd: onDeptResizeEnd,
      },
      style: {
        ...(g.style || {}),
        width: deptLayouts[g.data.department]?.width || g.width,
        height: deptLayouts[g.data.department]?.height || g.height,
      },
    }))

    // Group boxes first so they paint behind the brains they enclose.
    setRfNodes([...offsetGroupNodes, ...brainNodes])
  }, [topology, selectedId, routePath, routeSet, admin, layout, groupNodes, deptStyle, nodeDept, deptLayouts, nodeOffsets, onDeptResizeEnd, setRfNodes])

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
      } else if (!initialViewport || refitTick > 0) {
        rf.fitView({ padding: 0.34, duration: 350, minZoom: 0.42, maxZoom: 1 })
      }
    })
    return () => cancelAnimationFrame(raf)
    // refitTick: bumped by Reset layout so the graph re-frames after offsets clear.
    // Manual drag offsets deliberately do NOT re-fit (they'd fight the drag).
  }, [nodeIdSig, selectedId, rf, refitTick, initialViewport])

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
      <ContextMenu
        menu={menu}
        menuIsAdmin={menuIsAdmin}
        menuTargets={menuTargets}
        onMove={onMove}
        onClose={() => setMenu(null)}
      />
    <div
      className="lh-flow-canvas"
      style={{
        // Fluid, viewport-driven height: never cramped, never absurdly tall.
        height: 'clamp(560px, 82vh, 980px)',
        minHeight: 420,
        maxHeight: '92vh',
        resize: 'vertical',
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
        fitViewOptions={{ padding: 0.34, minZoom: 0.42, maxZoom: 1 }}
        defaultViewport={initialViewport}
        onMoveEnd={(_event, viewport) => onViewportSave(viewport)}
        minZoom={0.42}
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
              Drag or resize a department box
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
