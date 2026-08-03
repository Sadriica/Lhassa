import { Handle, Position } from '@xyflow/react'
import { kindMeta } from '../flowNodeKinds'
import { NODE_W } from './layout'

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
export function FlowCanvasNode({ data, selected }) {
  const { kind, label, config, stats } = data
  const m = kindMeta(kind)
  const rgb = m.rgb
  return (
    <div
      style={{
        width: NODE_W,
        background: 'var(--lh-surface)',
        border: `1px solid ${selected ? `rgba(${rgb},0.9)` : `rgba(${rgb},0.45)`}`,
        borderRadius: 'var(--lh-radius)',
        padding: '0.5rem 0.6rem',
        boxShadow: selected
          ? `0 0 0 1px rgba(${rgb},0.5), var(--lh-shadow-sm)`
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
      {stats && stats.runs > 0 && (
        <div className="flex gap-1 flex-wrap" style={{ marginTop: 6 }}>
          <span className="lh-tag" style={{ fontSize: 8 }}>{stats.runs} runs</span>
          <span className="lh-tag" style={{ fontSize: 8 }}>{stats.failures ? `${stats.failures} fail` : 'ok'}</span>
          {stats.avgDuration && <span className="lh-tag" style={{ fontSize: 8 }}>{stats.avgDuration}</span>}
        </div>
      )}
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
