import { Handle, NodeResizer, Position } from '@xyflow/react'
import { NODE_W } from './layout'

// Custom node renderers for the org FLOW canvas: the department-group box (a
// non-interactive, draggable, tinted bounding rectangle drawn behind its members)
// and the brain node (the sovereign-aware card with re-parent handles). The
// composition (OrgFlow.jsx) keys React Flow's `nodeTypes` on these; kept identical
// so the canvas wiring is a pure structural move.

// A non-interactive, rounded, tinted rectangle with an uppercase tracked label.
export function DeptGroupNode({ data }) {
  const { department, color } = data
  return (
    <div
      title={`Drag to move the ${department} department`}
      style={{
        width: '100%',
        height: '100%',
        minWidth: 220,
        minHeight: 140,
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
      <NodeResizer
        minWidth={220}
        minHeight={140}
        onResizeEnd={(_event, params) => data.onResizeEnd?.(department, params)}
        lineStyle={{ borderColor: `rgba(${color},0.55)` }}
        handleStyle={{
          width: 9,
          height: 9,
          borderRadius: 3,
          border: `1px solid rgba(${color},0.8)`,
          background: 'var(--lh-surface)',
        }}
      />
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
          maxWidth: 'calc(100% - 32px)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
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
export function BrainFlowNode({ data }) {
  const { node, selected, isAdmin, inRoute, isRouteLeaf, deptColor, deptTinted } = data
  const limits = node.limits || {}
  const limitKeys = Object.keys(limits)

  // The admin (Lhassa) is the sovereign; deep-red ring over a gold-tinted fill.
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
        maxWidth: NODE_W,
        background: isAdmin
          ? 'rgba(217,165,33,0.12)'
          : 'var(--lh-surface)',
        border: `1px solid ${isAdmin ? 'rgba(217,165,33,0.55)' : borderColor}`,
        borderRadius: 'var(--lh-radius)',
        padding: '0.6rem 0.7rem',
        boxShadow: glow,
        cursor: 'pointer',
        overflow: 'hidden',
      }}
    >
      {/* target (top): a connection ends here → this node becomes the new parent */}
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: 'var(--lh-accent)', width: 8, height: 8, border: 'none' }}
      />
      <div className="flex items-center gap-1.5 flex-wrap" style={{ minWidth: 0 }}>
        <span className="lh-mono" style={{ fontSize: 13, fontWeight: 600, color: 'var(--lh-text)', minWidth: 0, maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.id}
        </span>
        {isAdmin && (
          <span
            className="lh-tag"
            title="Lhassa; commands everyone"
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
        <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.role || 'department'}
        </span>
      </div>

      <div style={{ fontSize: 10, marginTop: 2, color: 'var(--lh-text-faint)' }}>
        <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.parent ? `reports to ${node.parent}` : 'root · commands everyone'}
        </span>
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
          The admin is anchored; it gets no source handle, so it can't be moved. */}
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
