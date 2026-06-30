import { useCallback, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Panel,
  Handle,
  Position,
  MarkerType,
  addEdge,
  useReactFlow,
  useNodesState,
  useEdgesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { KINDS, KIND_ORDER, kindMeta, freshNodeId } from './flowNodeKinds'

// The n8n-style WORK-FLOW canvas: a flow wires trigger → task → gate → output (with
// branches), left-to-right. A small palette drops kinds onto the canvas (click or
// drag); dragging between handles wires an edge; a side inspector edits the selected
// node's `config` by kind (task uses HYBRID routing — auto by skill OR pin a brain).
// Save POSTs/PUTs the full flow; Run executes the stored flow and renders the step
// list. The canvas owns the React Flow state; the parent owns the API calls.

// ── layout: a tidy left→right layered graph computed from the edges ─────────────
const NODE_W = 184
const COL_GAP = 92
const ROW_GAP = 28
const ROW_H = 96

function computeLayout(nodes, edges) {
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
  const pos = {}
  Object.keys(byCol).forEach((d) => {
    byCol[d].forEach((id, row) => {
      pos[id] = { x: Number(d) * (NODE_W + COL_GAP), y: row * (ROW_H + ROW_GAP) }
    })
  })
  return pos
}

// ── per-kind config summary shown inside a node ─────────────────────────────────
function summarize(kind, config) {
  const c = config || {}
  if (kind === 'trigger') return c.type || 'manual'
  if (kind === 'task') {
    if (c.brain) return `pin · ${c.brain}`
    if (c.skill) return `auto · ${c.skill}`
    return 'auto-route'
  }
  if (kind === 'gate') return c.type || 'verify'
  if (kind === 'output') return c.target ? `${c.type || 'log'} → ${c.target}` : (c.type || 'log')
  if (kind === 'branch') return 'fork on edges'
  return ''
}

// ── custom node: one styled box per kind, handles per kind ──────────────────────
function FlowCanvasNode({ data, selected }) {
  const { kind, label, config } = data
  const m = kindMeta(kind)
  const rgb = m.rgb
  return (
    <div
      style={{
        width: NODE_W,
        background: `linear-gradient(180deg, rgba(${rgb},0.14), var(--lh-surface))`,
        border: `1px solid ${selected ? `rgba(${rgb},0.95)` : `rgba(${rgb},0.5)`}`,
        borderRadius: 'var(--lh-radius)',
        padding: '0.5rem 0.6rem',
        boxShadow: selected
          ? `0 0 0 1px rgba(${rgb},0.55), 0 0 16px rgba(${rgb},0.22)`
          : 'var(--lh-shadow-sm)',
        cursor: 'pointer',
      }}
    >
      {m.hasTarget && (
        <Handle
          type="target"
          position={Position.Left}
          style={{ background: `rgb(${rgb})`, width: 9, height: 9, border: 'none' }}
        />
      )}
      <div className="flex items-center gap-1.5">
        <span style={{ color: `rgb(${rgb})`, fontSize: 13, lineHeight: 1 }}>{m.glyph}</span>
        <span
          style={{
            fontSize: 9,
            fontWeight: 600,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            color: `rgba(${rgb},0.95)`,
          }}
        >
          {m.label}
        </span>
      </div>
      <div className="lh-mono" style={{ fontSize: 12, marginTop: 3, color: 'var(--lh-text)' }}>
        {label || data.id}
      </div>
      <div style={{ fontSize: 10, marginTop: 1, color: 'var(--lh-text-faint)' }}>
        {summarize(kind, config)}
      </div>
      {m.hasSource && (
        <Handle
          type="source"
          position={Position.Right}
          style={{ background: `rgb(${rgb})`, width: 9, height: 9, border: 'none' }}
        />
      )}
    </div>
  )
}

const nodeTypes = { flowNode: FlowCanvasNode }

function describeWhen(when) {
  if (!when || !when.op) return ''
  const f = when.field || '·'
  if (when.op === 'truthy' || when.op === 'falsy') return `${f} ${when.op}`
  return `${f} ${when.op} ${when.value ?? ''}`
}

function edgeStyle(when) {
  const dashed = Boolean(when && when.op)
  return {
    stroke: dashed ? 'rgba(150,112,164,0.85)' : 'var(--lh-border-strong)',
    strokeWidth: dashed ? 2 : 1.6,
    strokeDasharray: dashed ? '5 4' : undefined,
  }
}

// ── the WHEN op vocabulary (mirrors flows/engine.eval_when) ──────────────────────
const WHEN_OPS = ['truthy', 'falsy', 'eq', 'ne', 'contains', 'gt', 'lt']

// ── node inspector (per kind) ───────────────────────────────────────────────────
function NodeInspector({ node, skills, brains, onChange, onLabel, onDelete }) {
  const kind = node.data.kind
  const config = node.data.config || {}
  const setConfig = (patch) => onChange({ ...config, ...patch })

  // Task HYBRID routing mode: a pinned brain wins; else auto-by-skill.
  const routingMode = config.brain ? 'pin' : 'auto'

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="lh-mono text-sm font-semibold" style={{ color: 'var(--lh-text)' }}>{node.id}</span>
        <span
          className="lh-tag"
          style={{
            borderColor: `rgba(${kindMeta(kind).rgb},0.55)`,
            color: `rgba(${kindMeta(kind).rgb},0.95)`,
            background: 'transparent',
            textTransform: 'uppercase',
            letterSpacing: '0.1em',
            fontSize: 9,
          }}
        >
          {kindMeta(kind).label}
        </span>
      </div>

      <label className="space-y-1 block">
        <span className="lh-label">label</span>
        <input
          className="lh-input"
          value={node.data.label || ''}
          placeholder={node.id}
          onChange={(e) => onLabel(e.target.value)}
        />
      </label>

      {kind === 'trigger' && (
        <label className="space-y-1 block">
          <span className="lh-label">type</span>
          <select className="lh-select" value={config.type || 'manual'} onChange={(e) => setConfig({ type: e.target.value })}>
            <option value="manual">manual</option>
            <option value="schedule">schedule</option>
            <option value="event">event</option>
          </select>
        </label>
      )}

      {kind === 'task' && (
        <div className="space-y-2">
          <span className="lh-label">routing — HYBRID (auto by skill, or pin a brain)</span>
          <div className="flex gap-1" role="group" aria-label="routing mode">
            <button
              type="button"
              className={`lh-btn ${routingMode === 'auto' ? 'lh-btn-primary' : ''}`}
              style={{ flex: 1 }}
              onClick={() => { const { brain, ...rest } = config; void brain; onChange(rest) }}
            >
              Auto by skill
            </button>
            <button
              type="button"
              className={`lh-btn ${routingMode === 'pin' ? 'lh-btn-primary' : ''}`}
              style={{ flex: 1 }}
              onClick={() => { const { skill, ...rest } = config; void skill; onChange({ ...rest, brain: config.brain || (brains[0]?.id || '') }) }}
            >
              Pin a brain
            </button>
          </div>

          {routingMode === 'auto' ? (
            <label className="space-y-1 block">
              <span className="lh-label">skill — auto-routes through the org</span>
              <select className="lh-select" value={config.skill || ''} onChange={(e) => setConfig({ skill: e.target.value || undefined })}>
                <option value="">(any — route by prompt)</option>
                {skills.map((s) => (
                  <option key={s.domain} value={s.domain}>{s.domain}{s.name ? ` · ${s.name}` : ''}</option>
                ))}
              </select>
            </label>
          ) : (
            <label className="space-y-1 block">
              <span className="lh-label">brain — pinned target</span>
              <select className="lh-select" value={config.brain || ''} onChange={(e) => setConfig({ brain: e.target.value })}>
                <option value="">choose a brain…</option>
                {brains.map((b) => (
                  <option key={b.id} value={b.id}>{b.id}{b.department ? ` · ${b.department}` : ''}</option>
                ))}
              </select>
            </label>
          )}

          <label className="space-y-1 block">
            <span className="lh-label">prompt (optional · {'{input}'} is filled from upstream)</span>
            <textarea
              className="lh-input"
              rows={3}
              value={config.prompt || ''}
              placeholder="what should this task do?"
              onChange={(e) => setConfig({ prompt: e.target.value || undefined })}
            />
          </label>
        </div>
      )}

      {kind === 'gate' && (
        <div className="space-y-2">
          <label className="space-y-1 block">
            <span className="lh-label">type</span>
            <select className="lh-select" value={config.type || 'verify'} onChange={(e) => setConfig({ type: e.target.value })}>
              <option value="verify">verify — objective check (no LLM)</option>
              <option value="approval">approval — park for a human</option>
            </select>
          </label>
          {(config.type || 'verify') === 'verify' && (
            <SpecEditor spec={config.spec} onChange={(spec) => setConfig({ spec })} />
          )}
          {config.type === 'approval' && (
            <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
              An approval gate parks the run for a human (it does not pass on its own).
            </p>
          )}
        </div>
      )}

      {kind === 'output' && (
        <div className="space-y-2">
          <label className="space-y-1 block">
            <span className="lh-label">type</span>
            <select className="lh-select" value={config.type || 'log'} onChange={(e) => setConfig({ type: e.target.value })}>
              <option value="notify">notify</option>
              <option value="doc">doc</option>
              <option value="log">log</option>
            </select>
          </label>
          <label className="space-y-1 block">
            <span className="lh-label">target (optional)</span>
            <input
              className="lh-input"
              value={config.target || ''}
              placeholder="e.g. telegram, a path, a channel"
              onChange={(e) => setConfig({ target: e.target.value || undefined })}
            />
          </label>
        </div>
      )}

      {kind === 'branch' && (
        <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
          A branch has no config of its own — the condition lives on each OUTGOING edge&apos;s
          <span className="lh-mono"> when</span>. Click an edge leaving this node to set its condition
          (the first matching edge is taken; an unconditional edge is the <span className="lh-mono">else</span>).
        </p>
      )}

      <button
        type="button"
        className="lh-btn lh-btn-danger"
        onClick={onDelete}
        disabled={kind === 'trigger'}
        title={kind === 'trigger' ? 'a flow keeps its single trigger' : 'remove this node'}
      >
        Delete node
      </button>
    </div>
  )
}

// A small JSON spec editor for a verify gate (free-form object, parsed on the fly).
function SpecEditor({ spec, onChange }) {
  const [text, setText] = useState(() => {
    try { return spec && Object.keys(spec).length ? JSON.stringify(spec, null, 2) : '' }
    catch { return '' }
  })
  const [err, setErr] = useState('')
  return (
    <label className="space-y-1 block">
      <span className="lh-label">spec — verify rule (JSON, optional)</span>
      <textarea
        className="lh-input lh-mono"
        rows={3}
        value={text}
        placeholder='e.g. {"contains": "ok"}'
        onChange={(e) => {
          const v = e.target.value
          setText(v)
          if (!v.trim()) { setErr(''); onChange({}); return }
          try { onChange(JSON.parse(v)); setErr('') }
          catch { setErr('not valid JSON yet — last good value kept') }
        }}
      />
      {err && <span className="text-[10px]" style={{ color: 'var(--lh-warn)' }}>{err}</span>}
    </label>
  )
}

// ── edge inspector: the optional `when` condition (used by branches / gates) ─────
function EdgeInspector({ edge, onChange, onDelete }) {
  const when = edge.data?.when || null
  const enabled = Boolean(when && when.op)
  const set = (patch) => onChange({ ...(when || { op: 'truthy' }), ...patch })
  const valueless = !enabled || when.op === 'truthy' || when.op === 'falsy'
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="lh-mono text-[12px]" style={{ color: 'var(--lh-text)' }}>{edge.source} → {edge.target}</span>
        <span className="lh-tag" style={{ fontSize: 9 }}>edge</span>
      </div>
      <label className="flex items-center gap-2 text-[12px]" style={{ color: 'var(--lh-text-muted)' }}>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onChange(e.target.checked ? { field: when?.field || '', op: 'truthy' } : null)}
          style={{ accentColor: 'var(--lh-accent)' }}
        />
        conditional (when) — otherwise unconditional / the else path
      </label>
      {enabled && (
        <div className="space-y-2">
          <label className="space-y-1 block">
            <span className="lh-label">field — a key in the upstream result (e.g. passed, status)</span>
            <input className="lh-input lh-mono" value={when.field || ''} onChange={(e) => set({ field: e.target.value })} placeholder="passed" />
          </label>
          <label className="space-y-1 block">
            <span className="lh-label">op</span>
            <select className="lh-select" value={when.op} onChange={(e) => set({ op: e.target.value })}>
              {WHEN_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
            </select>
          </label>
          {!valueless && (
            <label className="space-y-1 block">
              <span className="lh-label">value</span>
              <input className="lh-input lh-mono" value={when.value ?? ''} onChange={(e) => set({ value: e.target.value })} placeholder="ok" />
            </label>
          )}
        </div>
      )}
      <button type="button" className="lh-btn lh-btn-danger" onClick={onDelete}>Delete edge</button>
    </div>
  )
}

// ── run-record renderer ─────────────────────────────────────────────────────────
function StepResult({ step }) {
  // A short, kind-aware one-liner of what a step produced.
  if (step.error) return <span style={{ color: 'var(--lh-alert)' }}>{step.error}</span>
  if (step.kind === 'task') {
    const r = step.result
    const summary = r && typeof r === 'object' ? (r.summary || r.output || JSON.stringify(r)) : r
    return (
      <span>
        <span className="lh-mono" style={{ color: 'var(--lh-accent-text)' }}>{step.brain}</span>
        <span style={{ color: 'var(--lh-text-faint)' }}>{step.pinned ? ' · pinned' : ' · auto'}</span>
        {summary ? <span style={{ color: 'var(--lh-text-muted)' }}> — {String(summary).slice(0, 120)}</span> : null}
      </span>
    )
  }
  if (step.kind === 'gate') {
    if (step.needs_approval) return <span style={{ color: 'var(--lh-warn)' }}>parked for approval</span>
    return <span style={{ color: 'var(--lh-text-muted)' }}>{step.gate} · {step.passed ? 'passed' : 'failed'}{step.detail ? ` — ${String(step.detail).slice(0, 100)}` : ''}</span>
  }
  if (step.kind === 'output') return <span style={{ color: 'var(--lh-text-muted)' }}>{step.output_type}{step.target ? ` → ${step.target}` : ''}</span>
  if (step.kind === 'trigger') return <span style={{ color: 'var(--lh-text-muted)' }}>{step.trigger}</span>
  if (step.kind === 'branch') return <span style={{ color: 'var(--lh-text-muted)' }}>chose {step.chosen_edge || '— (no edge matched)'}</span>
  return null
}

function RunRecord({ run, live }) {
  if (!run) return null
  const ok = run.status === 'completed'
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="lh-label" style={{ margin: 0 }}>run</span>
        <span
          className="lh-tag"
          style={{
            borderColor: ok ? 'rgba(124,154,91,0.5)' : 'rgba(162,62,52,0.5)',
            color: ok ? 'var(--lh-ok)' : 'var(--lh-alert)',
            background: 'transparent',
          }}
        >
          {ok ? '✓ completed' : '✗ failed'}
        </span>
        {!live && (
          <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
            offline / stubbed — set LHASSA_ORG_LIVE for a live run
          </span>
        )}
      </div>
      <div className="space-y-1">
        {(run.steps || []).map((s, i) => (
          <div
            key={i}
            className="lh-row"
            style={{ background: 'var(--lh-surface-2)', display: 'flex', gap: 8, alignItems: 'baseline' }}
          >
            <span style={{ color: s.ok ? 'var(--lh-ok)' : 'var(--lh-alert)', fontSize: 12 }}>{s.ok ? '✓' : '✗'}</span>
            <span className="lh-mono" style={{ fontSize: 11, color: 'var(--lh-text)' }}>{s.node ?? '—'}</span>
            <span
              className="lh-tag"
              style={{
                fontSize: 8,
                textTransform: 'uppercase',
                letterSpacing: '0.1em',
                borderColor: `rgba(${kindMeta(s.kind).rgb},0.5)`,
                color: `rgba(${kindMeta(s.kind).rgb},0.95)`,
                background: 'transparent',
              }}
            >
              {s.kind || '—'}
            </span>
            <span className="text-[11px]" style={{ flex: 1, minWidth: 0 }}><StepResult step={s} /></span>
          </div>
        ))}
      </div>
      <div className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
        <span className="lh-label">output</span>
        <pre className="lh-mono" style={{ fontSize: 11, color: 'var(--lh-text-muted)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
          {typeof run.output === 'string' ? run.output : JSON.stringify(run.output, null, 2)}
        </pre>
      </div>
    </div>
  )
}

// Build the initial React Flow node/edge arrays from a flow dict (with a computed
// layout). Done once at mount — EditorInner is keyed by flow.id so a different flow
// remounts fresh; no synchronous setState-in-effect hydration needed.
function initialGraph(flow) {
  const pos = computeLayout(flow.nodes || [], flow.edges || [])
  const nodes = (flow.nodes || []).map((n) => ({
    id: n.id,
    type: 'flowNode',
    position: pos[n.id] || { x: 0, y: 0 },
    data: { id: n.id, kind: n.kind, label: n.label || '', config: n.config || {} },
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
function EditorInner({ flow, skills, brains, isNew, onSave, onRun, onClose }) {
  const initial = useMemo(() => initialGraph(flow), [flow])
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(initial.nodes)
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(initial.edges)
  const [name, setName] = useState(flow.name || '')
  const [description, setDescription] = useState(flow.description || '')
  const [selNode, setSelNode] = useState(null)
  const [selEdge, setSelEdge] = useState(null)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [saveMsg, setSaveMsg] = useState(null) // {ok, text}
  const [run, setRun] = useState(null)
  const wrapperRef = useRef(null)
  const { screenToFlowPosition } = useReactFlow()

  const triggerCount = useMemo(() => rfNodes.filter((n) => n.data.kind === 'trigger').length, [rfNodes])

  // ── add a node (palette click or drop) ────────────────────────────────────────
  const addNode = useCallback((kind, position) => {
    setRfNodes((nds) => {
      const used = new Set(nds.map((n) => n.id))
      const id = freshNodeId(kind, used)
      const node = {
        id,
        type: 'flowNode',
        position: position || { x: 40 + nds.length * 26, y: 40 + nds.length * 22 },
        data: { id, kind, label: '', config: { ...KINDS[kind].defaultConfig } },
      }
      return [...nds, node]
    })
  }, [setRfNodes])

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
    setSaveMsg(null)
    const res = await onSave(buildFlow())
    setSaving(false)
    if (res.ok) setSaveMsg({ ok: true, text: isNew ? 'Created.' : 'Saved.' })
    else setSaveMsg({ ok: false, text: res.detail || 'Save failed.' })
    return res
  }, [onSave, buildFlow, isNew])

  const doRun = useCallback(async () => {
    setRunning(true)
    setRun(null)
    // Persist the canvas first so the stored flow matches what the owner sees.
    const saved = await doSave()
    if (!saved.ok) { setRunning(false); return }
    const res = await onRun(flow.id)
    setRunning(false)
    if (res.ok) setRun(res.run)
    else setSaveMsg({ ok: false, text: res.detail || 'Run failed.' })
  }, [doSave, onRun, flow.id])

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

      {/* validation + save feedback */}
      {triggerCount !== 1 && (
        <div className="lh-row" style={{ borderColor: 'rgba(217,165,33,0.45)', color: 'var(--lh-warn)' }}>
          A flow needs exactly one trigger — found {triggerCount}. {triggerCount === 0 ? 'Add a Trigger.' : 'Remove the extras.'}
        </div>
      )}
      {saveMsg && (
        <div
          className="lh-row"
          style={{
            borderColor: saveMsg.ok ? 'rgba(124,154,91,0.45)' : 'rgba(162,62,52,0.45)',
            color: saveMsg.ok ? 'var(--lh-ok)' : 'var(--lh-alert)',
          }}
        >
          {saveMsg.text}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* canvas */}
        <div className="lg:col-span-3">
          <div
            ref={wrapperRef}
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

          {(run || running) && (
            <div className="pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
              {running ? (
                <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Running the flow…</p>
              ) : (
                <RunRecord run={run} live={run?.steps?.some((s) => s.live)} />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── the draggable / clickable palette ───────────────────────────────────────────
function NodePalette({ onAdd }) {
  return (
    <div
      style={{
        background: 'var(--lh-surface)',
        border: '1px solid var(--lh-border)',
        borderRadius: 'var(--lh-radius)',
        boxShadow: 'var(--lh-shadow)',
        padding: '0.45rem 0.5rem',
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
        palette
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {KIND_ORDER.map((k) => {
          const m = KINDS[k]
          return (
            <button
              key={k}
              type="button"
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData('application/lhassa-flow-kind', k)
                e.dataTransfer.effectAllowed = 'move'
              }}
              onClick={() => onAdd(k)}
              title={m.hint}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                padding: '0.28rem 0.5rem',
                borderRadius: 8,
                border: `1px solid rgba(${m.rgb},0.5)`,
                background: `rgba(${m.rgb},0.1)`,
                color: 'var(--lh-text)',
                cursor: 'grab',
                fontSize: 12,
                textAlign: 'left',
              }}
            >
              <span style={{ color: `rgb(${m.rgb})` }}>{m.glyph}</span>
              <span>{m.label}</span>
            </button>
          )
        })}
      </div>
      <div style={{ fontSize: 9, color: 'var(--lh-text-faint)', marginTop: 6, lineHeight: 1.3 }}>
        Click or drag onto the canvas.
      </div>
    </div>
  )
}

export default function WorkflowEditor(props) {
  // Key the inner editor by flow id so opening a different flow remounts it fresh
  // (state initialised straight from props — no hydration effect). A save adopts the
  // returned flow with the SAME id, so the canvas is preserved.
  return (
    <ReactFlowProvider>
      <EditorInner key={props.flow.id} {...props} />
    </ReactFlowProvider>
  )
}
