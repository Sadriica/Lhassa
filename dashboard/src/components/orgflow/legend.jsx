import { kindStyle } from '../relationshipKinds'

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

export function RelationshipLegend({ kinds, kindMeta, hiddenKinds, onToggle }) {
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
