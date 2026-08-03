import { KINDS, KIND_ORDER } from '../flowNodeKinds'

// ── the draggable / clickable palette ───────────────────────────────────────────
export function NodePalette({ onAdd }) {
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
