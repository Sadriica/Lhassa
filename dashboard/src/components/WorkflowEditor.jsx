import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Panel,
  MarkerType,
  addEdge,
  useReactFlow,
  useNodesState,
  useEdgesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { KINDS, kindMeta, freshNodeId } from './flowNodeKinds'
import { toast } from '../lib/toast'
import { fmtDuration, fmtPct } from '../lib/format'
import { computeLayout, computeDisconnected } from './workflow/layout'
import { pickAutoSource, describeWhen, edgeStyle } from './workflow/wiring'
import { FlowCanvasNode } from './workflow/nodes'
import { NodePalette } from './workflow/palette'
import { NodeInspector, EdgeInspector } from './workflow/inspector'
import { RunRecord } from './workflow/runPanel'

// The n8n-style WORK-FLOW canvas: a flow wires trigger → task → gate → output (with
// branches), left-to-right. A small palette drops kinds onto the canvas (click or
// drag); dragging between handles wires an edge; a side inspector edits the selected
// node's `config` by kind (task uses HYBRID routing; auto by skill OR pin a brain).
// Save POSTs/PUTs the full flow; Run executes the stored flow and renders the step
// list. The canvas owns the React Flow state; the parent owns the API calls.
//
// The pieces live in ./workflow/: layout.js (computeLayout + the disconnected BFS),
// wiring.js (pickAutoSource + edge visuals), nodes.jsx (the per-kind node box),
// palette.jsx (the drag/click palette), inspector.jsx (per-kind node + edge config),
// runPanel.jsx (the RunRecord trace). This file stays the composition wiring
// props/state → those pieces.

// The custom node types, assembled at module level (react-refresh needs a stable
// identity, not a per-render object).
const nodeTypes = { flowNode: FlowCanvasNode }

// Build the initial React Flow node/edge arrays from a flow dict (with a computed
// layout). Done once at mount; EditorInner is keyed by flow.id so a different flow
// remounts fresh; no synchronous setState-in-effect hydration needed.
function initialGraph(flow, nodeStats = {}) {
  const pos = computeLayout(flow.nodes || [], flow.edges || [])
  const nodes = (flow.nodes || []).map((n) => ({
    id: n.id,
    type: 'flowNode',
    position: pos[n.id] || { x: 0, y: 0 },
    data: { id: n.id, kind: n.kind, label: n.label || '', config: n.config || {}, stats: nodeStats[n.id] },
  }))
  const edges = (flow.edges || []).map((e, i) => ({
    id: `e${i}-${e.from_node}-${e.to_node}`,
    source: e.from_node,
    target: e.to_node,
    data: { when: e.when || null },
    label: describeWhen(e.when),
    style: edgeStyle(e.when),
    markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--lh-border-strong)' },
  }))
  return { nodes, edges }
}

// ── the canvas + side rail ──────────────────────────────────────────────────────
function nodeLabel(step) {
  return step.node || step.entity_id || 'step'
}

function stepStatus(step) {
  return step.status || (step.ok === false ? 'failed' : 'completed')
}

function buildNodeStats(flowRuns = []) {
  const byNode = {}
  for (const run of flowRuns) {
    const steps = run.raw?.steps || []
    for (const step of steps) {
      const node = step.node
      if (!node) continue
      const bucket = byNode[node] || { runs: 0, failures: 0, durationMs: 0 }
      bucket.runs += 1
      bucket.durationMs += Number(step.duration_ms || 0)
      if (!['completed', 'needs_approval'].includes(stepStatus(step))) bucket.failures += 1
      byNode[node] = bucket
    }
  }
  for (const [node, stats] of Object.entries(byNode)) {
    stats.avgDuration = stats.durationMs ? fmtDuration(stats.durationMs / stats.runs) : ''
    byNode[node] = stats
  }
  return byNode
}

function EditorStats({ stats }) {
  if (!stats) return null
  return (
    <div className="flex gap-1.5 flex-wrap">
      <span className="lh-tag">{stats.runs || 0} runs</span>
      <span className="lh-tag">{fmtPct(stats.success_rate)} success</span>
      <span className="lh-tag">avg {fmtDuration(stats.avg_duration_ms)}</span>
      {stats.last_status && <span className="lh-tag">last {stats.last_status}</span>}
    </div>
  )
}

function RunHistory({ runs = [] }) {
  if (runs.length === 0) {
    return (
      <div className="lh-row" style={{ color: 'var(--lh-text-faint)' }}>
        No runs recorded for this flow yet.
      </div>
    )
  }
  return (
    <div className="space-y-2 max-h-72 overflow-y-auto">
      {runs.slice(0, 8).map((item) => (
        <div key={item.id} className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="flex items-center gap-1 flex-wrap">
                <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text)' }}>{item.id}</span>
                <span className="lh-tag">{item.status}</span>
                <span className="lh-tag">{fmtDuration(item.duration_ms)}</span>
              </div>
              <div className="text-[10px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>{item.started_at || '—'}</div>
            </div>
            <span className="lh-tag">{(item.raw?.steps || []).length} steps</span>
          </div>
          {(item.raw?.steps || []).length > 0 && (
            <div className="mt-2 flex gap-1 flex-wrap">
              {(item.raw.steps || []).slice(0, 6).map((step, idx) => (
                <span key={`${item.id}-${idx}`} className="lh-tag">
                  {nodeLabel(step)} · {stepStatus(step)}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function EditorInner({ flow, skills, brains, flowStats, flowRuns = [], isNew, onSave, onRun, onClose }) {
  const nodeStats = useMemo(() => buildNodeStats(flowRuns), [flowRuns])
  const initial = useMemo(() => initialGraph(flow, nodeStats), [flow, nodeStats])
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(initial.nodes)
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(initial.edges)
  const [name, setName] = useState(flow.name || '')
  const [description, setDescription] = useState(flow.description || '')
  const [selNode, setSelNode] = useState(null)
  const [selEdge, setSelEdge] = useState(null)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [run, setRun] = useState(null)
  const [runInput, setRunInput] = useState('')
  const [runInputError, setRunInputError] = useState('')
  const wrapperRef = useRef(null)
  const { screenToFlowPosition, fitView } = useReactFlow()

  const triggerCount = useMemo(() => rfNodes.filter((n) => n.data.kind === 'trigger').length, [rfNodes])

  useEffect(() => {
    setRfNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, stats: nodeStats[n.id] } })))
  }, [nodeStats, setRfNodes])

  // Nodes that can't be reached from the trigger; surfaced as a non-blocking
  // warning so a visually-"connected" flow can't be saved silently disconnected.
  const disconnected = useMemo(() => computeDisconnected(rfNodes, rfEdges), [rfNodes, rfEdges])

  // ── add a node (palette click or drop); AUTO-WIRES it from a sensible source ──
  const addNode = useCallback((kind, position) => {
    const meta = KINDS[kind]
    const used = new Set(rfNodes.map((n) => n.id))
    const id = freshNodeId(kind, used)
    const node = {
      id,
      type: 'flowNode',
      position: position || { x: 40 + rfNodes.length * 26, y: 40 + rfNodes.length * 22 },
      data: { id, kind, label: '', config: { ...meta.defaultConfig } },
    }
    // Only auto-connect INTO a node that accepts input (never into a trigger).
    const sourceId = meta.hasTarget ? pickAutoSource(rfNodes, rfEdges, selNode) : null
    setRfNodes((nds) => [...nds, node])
    if (sourceId && sourceId !== id) {
      setRfEdges((eds) => addEdge({
        source: sourceId,
        target: id,
        id: `e${Date.now()}-${sourceId}-${id}`,
        data: { when: null },
        label: '',
        style: edgeStyle(null),
        markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--lh-border-strong)' },
      }, eds))
    }
    // Select the new node so a chain keeps building linearly from it (easy to rewire).
    setSelEdge(null)
    setSelNode(id)
  }, [rfNodes, rfEdges, selNode, setRfNodes, setRfEdges])

  // ── Tidy: re-run the auto-layout, optionally forcing a direction ──────────────
  const relayout = useCallback((direction) => {
    const modelNodes = rfNodes.map((n) => ({ id: n.id, data: { kind: n.data.kind }, kind: n.data.kind }))
    const modelEdges = rfEdges.map((e) => ({ from_node: e.source, to_node: e.target }))
    const pos = computeLayout(modelNodes, modelEdges, direction)
    setRfNodes((nds) => nds.map((n) => (pos[n.id] ? { ...n, position: pos[n.id] } : n)))
    window.requestAnimationFrame(() => { try { fitView({ padding: 0.2 }) } catch { /* canvas not ready */ } })
  }, [rfNodes, rfEdges, setRfNodes, fitView])

  const onDrop = useCallback((event) => {
    event.preventDefault()
    const kind = event.dataTransfer.getData('application/lhassa-flow-kind')
    if (!kind || !KINDS[kind]) return
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
    addNode(kind, position)
  }, [screenToFlowPosition, addNode])

  const onDragOver = useCallback((event) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  // ── connect: wire an edge (no self-loop, no edge into a trigger, no dup) ───────
  const isValidConnection = useCallback((conn) => {
    if (!conn.source || !conn.target || conn.source === conn.target) return false
    const tgt = rfNodes.find((n) => n.id === conn.target)
    if (tgt?.data.kind === 'trigger') return false
    if (rfEdges.some((e) => e.source === conn.source && e.target === conn.target)) return false
    return true
  }, [rfNodes, rfEdges])

  const onConnect = useCallback((conn) => {
    if (!isValidConnection(conn)) return
    setRfEdges((eds) => addEdge({
      ...conn,
      id: `e${Date.now()}-${conn.source}-${conn.target}`,
      data: { when: null },
      label: '',
      style: edgeStyle(null),
      markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--lh-border-strong)' },
    }, eds))
  }, [isValidConnection, setRfEdges])

  const onNodeClick = useCallback((_e, node) => { setSelEdge(null); setSelNode(node.id) }, [])
  const onEdgeClick = useCallback((_e, edge) => { setSelNode(null); setSelEdge(edge.id) }, [])
  const onPaneClick = useCallback(() => { setSelNode(null); setSelEdge(null) }, [])

  // ── inspector mutations ───────────────────────────────────────────────────────
  const updateNodeConfig = useCallback((nodeId, config) => {
    setRfNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, config } } : n)))
  }, [setRfNodes])
  const updateNodeLabel = useCallback((nodeId, label) => {
    setRfNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, label } } : n)))
  }, [setRfNodes])
  const deleteNode = useCallback((nodeId) => {
    setRfNodes((nds) => nds.filter((n) => n.id !== nodeId))
    setRfEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId))
    setSelNode(null)
  }, [setRfNodes, setRfEdges])
  const updateEdgeWhen = useCallback((edgeId, when) => {
    setRfEdges((eds) => eds.map((e) => (e.id === edgeId
      ? { ...e, data: { ...e.data, when }, label: describeWhen(when), style: edgeStyle(when) }
      : e)))
  }, [setRfEdges])
  const deleteEdge = useCallback((edgeId) => {
    setRfEdges((eds) => eds.filter((e) => e.id !== edgeId))
    setSelEdge(null)
  }, [setRfEdges])

  // ── build the flow dict the API speaks ────────────────────────────────────────
  const buildFlow = useCallback(() => ({
    id: flow.id,
    name: name.trim() || flow.name || flow.id,
    description: description.trim(),
    nodes: rfNodes.map((n) => ({ id: n.id, kind: n.data.kind, config: n.data.config || {}, label: n.data.label || '' })),
    edges: rfEdges.map((e) => {
      const out = { from_node: e.source, to_node: e.target }
      if (e.data?.when && e.data.when.op) out.when = e.data.when
      return out
    }),
  }), [flow, name, description, rfNodes, rfEdges])

  const doSave = useCallback(async () => {
    setSaving(true)
    const res = await onSave(buildFlow())
    setSaving(false)
    if (res.ok) toast.success(isNew ? 'Flow created' : 'Flow saved')
    else toast.error(res.detail || 'Save failed')
    return res
  }, [onSave, buildFlow, isNew])

  const doRun = useCallback(async () => {
    let inputs = {}
    if (runInput.trim()) {
      try {
        inputs = JSON.parse(runInput)
        if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error('object expected')
        setRunInputError('')
      } catch {
        setRunInputError('Run input must be a JSON object.')
        return
      }
    }
    setRunning(true)
    setRun(null)
    // Persist the canvas first so the stored flow matches what the owner sees.
    const saved = await doSave()
    if (!saved.ok) { setRunning(false); return }
    const res = await onRun(flow.id, inputs)
    setRunning(false)
    if (res.ok) {
      setRun(res.run)
      if (res.run?.status === 'waiting_approval') toast.info('Run waiting for approval')
      else toast.success(`Run ${res.run?.status || 'completed'}`)
    } else {
      toast.error(res.detail || 'Run failed')
    }
  }, [doSave, onRun, flow.id, runInput])

  const selectedNode = rfNodes.find((n) => n.id === selNode) || null
  const selectedEdge = rfEdges.find((e) => e.id === selEdge) || null

  return (
    <div className="space-y-3">
      {/* header: name / description / actions */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex-1 min-w-[240px] space-y-1.5">
          <div className="flex items-center gap-2">
            <button type="button" className="lh-btn" onClick={onClose} title="Back to the flow list">← Flows</button>
            <input
              className="lh-input"
              style={{ fontWeight: 600 }}
              value={name}
              placeholder="flow name"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <input
            className="lh-input"
            value={description}
            placeholder="a short description (optional)"
            onChange={(e) => setDescription(e.target.value)}
          />
          <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{flow.id}</span>
          <EditorStats stats={flowStats} />
        </div>
        <div className="flex gap-2 items-center">
          <button type="button" className="lh-btn lh-btn-primary" onClick={doSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button type="button" className="lh-btn" onClick={doRun} disabled={running || saving}>
            {running ? 'Running…' : '▷ Run'}
          </button>
        </div>
      </div>

      {/* validation (save/run feedback is delivered via global toasts) */}
      {triggerCount !== 1 && (
        <div className="lh-row" style={{ borderColor: 'rgba(217,165,33,0.45)', color: 'var(--lh-warn)' }}>
          A flow needs exactly one trigger; found {triggerCount}. {triggerCount === 0 ? 'Add a Trigger.' : 'Remove the extras.'}
        </div>
      )}
      {disconnected.length > 0 && (
        <div className="lh-row" style={{ borderColor: 'rgba(162,62,52,0.45)', color: 'var(--lh-alert)' }}>
          {disconnected.length} node{disconnected.length === 1 ? " isn't" : "s aren't"} connected to the trigger:{' '}
          <span className="lh-mono">{disconnected.join(', ')}</span>. Wire {disconnected.length === 1 ? 'it' : 'them'} from an upstream node or it won&apos;t run.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* canvas */}
        <div className="lg:col-span-3">
          <div
            ref={wrapperRef}
            className="lh-flow-canvas"
            style={{
              // Fluid, generous height so long vertical chains stay readable.
              height: 'clamp(460px, 72vh, 920px)',
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
              onEdgeClick={onEdgeClick}
              onPaneClick={onPaneClick}
              onDrop={onDrop}
              onDragOver={onDragOver}
              deleteKeyCode={['Backspace', 'Delete']}
              onNodesDelete={() => setSelNode(null)}
              onEdgesDelete={() => setSelEdge(null)}
              fitView
              fitViewOptions={{ padding: 0.25 }}
              minZoom={0.25}
              maxZoom={1.75}
              proOptions={{ hideAttribution: true }}
              colorMode="dark"
            >
              <Background color="rgba(217,165,33,0.07)" gap={22} />
              <Panel position="top-left">
                <NodePalette onAdd={(k) => addNode(k)} />
              </Panel>
              <Panel position="top-right">
                <div style={{ display: 'flex', gap: 4 }}>
                  <button type="button" className="lh-btn" onClick={() => relayout('vertical')} title="Tidy: stack the flow top-to-bottom (best for long chains)">
                    Tidy ↓
                  </button>
                  <button type="button" className="lh-btn" onClick={() => relayout('horizontal')} title="Tidy: lay the flow out left-to-right">
                    Tidy →
                  </button>
                </div>
              </Panel>
              <MiniMap
                pannable
                zoomable
                style={{ background: 'var(--lh-surface)', border: '1px solid var(--lh-border)' }}
                maskColor="rgba(16,14,9,0.66)"
                nodeColor={(n) => `rgb(${kindMeta(n.data?.kind).rgb})`}
              />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
        </div>

        {/* inspector / run rail */}
        <div className="lg:col-span-2 space-y-3">
          <div className="lh-label">inspector</div>
          {selectedNode ? (
            <NodeInspector
              key={selectedNode.id}
              node={selectedNode}
              skills={skills}
              brains={brains}
              onChange={(config) => updateNodeConfig(selectedNode.id, config)}
              onLabel={(label) => updateNodeLabel(selectedNode.id, label)}
              onDelete={() => deleteNode(selectedNode.id)}
            />
          ) : selectedEdge ? (
            <EdgeInspector
              key={selectedEdge.id}
              edge={selectedEdge}
              onChange={(when) => updateEdgeWhen(selectedEdge.id, when)}
              onDelete={() => deleteEdge(selectedEdge.id)}
            />
          ) : (
            <p className="text-xs lh-row" style={{ color: 'var(--lh-text-faint)' }}>
              Drop a node from the palette (or drag one onto the canvas), connect handles to wire it,
              then select a node or edge to edit it. Trigger → Task → Gate → Output, with Branches.
            </p>
          )}

          <div className="pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
            <div className="lh-label mb-1">run input</div>
            <textarea
              className="lh-input lh-mono"
              rows={4}
              value={runInput}
              placeholder='{"summary":"seed input"}'
              onChange={(e) => { setRunInput(e.target.value); setRunInputError('') }}
            />
            {runInputError && <div className="text-[10px] mt-1" style={{ color: 'var(--lh-alert)' }}>{runInputError}</div>}
            <div className="text-[10px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>
              Empty input runs with <span className="lh-mono">{'{}'}</span>. JSON is sent as <span className="lh-mono">inputs</span>.
            </div>
          </div>

          {(run || running) && (
            <div className="pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
              {running ? (
                <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Running the flow…</p>
              ) : (
                <RunRecord run={run} live={run?.steps?.some((s) => s.live)} />
              )}
            </div>
          )}

          <div className="pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
            <div className="lh-label mb-2">run history</div>
            <RunHistory runs={flowRuns} />
          </div>
        </div>
      </div>
    </div>
  )
}

export default function WorkflowEditor(props) {
  // Key the inner editor by flow id so opening a different flow remounts it fresh
  // (state initialised straight from props; no hydration effect). A save adopts the
  // returned flow with the SAME id, so the canvas is preserved.
  return (
    <ReactFlowProvider>
      <EditorInner key={props.flow.id} {...props} />
    </ReactFlowProvider>
  )
}
