// ── skills picker ───────────────────────────────────────────────────────────
export function SkillPicker({ available, selected, onToggle }) {
  if (available.length === 0) {
    return <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>No skills registered.</p>
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {available.map((s) => {
        const on = selected.includes(s.domain)
        return (
          <button
            key={s.domain}
            type="button"
            onClick={() => onToggle(s.domain)}
            className={`lh-tag ${on ? 'lh-tag-accent' : ''}`}
            style={{ cursor: 'pointer' }}
            title={s.name ? `${s.name}${s.tools?.length ? ` · ${s.tools.length} tools` : ''}` : s.domain}
          >
            {on ? '✓ ' : ''}{s.domain}
          </button>
        )
      })}
    </div>
  )
}
