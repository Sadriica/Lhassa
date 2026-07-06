export function SectionHeader({ eyebrow, title, meta, actions }) {
  return (
    <div className="lh-card-head">
      <div>
        {eyebrow && <div className="lh-eyebrow">{eyebrow}</div>}
        {title && <h2 className="lh-card-title mt-1">{title}</h2>}
      </div>
      {(meta || actions) && (
        <div className="flex gap-1 flex-wrap justify-end">
          {meta}
          {actions}
        </div>
      )}
    </div>
  )
}

export function EmptyState({ children }) {
  return (
    <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>
      {children}
    </p>
  )
}

export function ErrorState({ children }) {
  return (
    <div className="lh-row" style={{ color: 'var(--lh-alert)', borderColor: 'rgba(162,62,52,0.45)' }}>
      {children}
    </div>
  )
}

export function MetricCard({ label, value, sub }) {
  return (
    <div className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="lh-label">{label}</div>
      <div className="lh-mono text-lg mt-1" style={{ color: 'var(--lh-text)' }}>{value}</div>
      {sub && <div className="text-[11px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>{sub}</div>}
    </div>
  )
}
