import { useState } from 'react'

// ── carried-skills install status ───────────────────────────────────────────
// A brain can carry a skill NAME that has no installed SKILL.md yet — in which
// case the name does nothing. We compare each carried skill against the installed
// set: an installed one is a normal gold tag; a not-installed one is a dashed,
// muted tag with an Install action (→ POST /api/skills/{name}/install).
export function CarriedSkills({ skills, installedSet, onInstall }) {
  const [installing, setInstalling] = useState('')
  if (!skills.length) {
    return <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>No skills carried yet.</p>
  }
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5 items-center">
        {skills.map((s) => {
          const ok = installedSet.has(String(s).toLowerCase())
          if (ok) {
            return <span key={s} className="lh-tag lh-tag-accent" title="installed SKILL.md">{s}</span>
          }
          return (
            <span key={s} className="inline-flex items-center gap-1">
              <span
                className="lh-tag"
                style={{ borderStyle: 'dashed', color: 'var(--lh-text-faint)', background: 'transparent' }}
                title="referenced but not installed"
              >
                {s}
              </span>
              <button
                type="button"
                className="lh-btn"
                style={{ padding: '0.18rem 0.5rem', fontSize: '0.6875rem' }}
                disabled={installing === s}
                onClick={async () => { setInstalling(s); await onInstall(s); setInstalling('') }}
              >
                {installing === s ? '…' : 'Install'}
              </button>
            </span>
          )
        })}
      </div>
      <p className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
        A gold tag is an installed SKILL.md. A dashed tag is only a name — it does nothing until its skill is installed.
      </p>
    </div>
  )
}
