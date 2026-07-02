import { useState } from 'react'

// ── department field ──────────────────────────────────────────────────────────
// A dropdown over the EXISTING departments (distinct non-empty values across the
// chart) plus a "+ New department…" option that reveals a text input to coin a new
// one. Creating a department is just assigning a fresh string — no backend change.
const NEW_DEPT = '__new_dept__'
export function DepartmentField({ value, options, onChange }) {
  // We're in "type a new one" mode when adding, or when the current value isn't a
  // known department (e.g. mid-typing a brand new name).
  const known = options.includes(value)
  const [adding, setAdding] = useState(value !== '' && !known)

  if (adding) {
    // The input is bound LIVE to `value`, so the typed name is already the field's
    // value. Confirming (↵ / Enter) just leaves type-mode and KEEPS that value —
    // it must never be discarded. ↩ goes back to the dropdown and clears.
    return (
      <div className="flex gap-1.5">
        <input
          className="lh-input" autoFocus placeholder="new department name"
          value={value} onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); if (value.trim()) setAdding(false) }
          }}
        />
        <button
          type="button" className="lh-btn" title="Use this department"
          disabled={!value.trim()}
          onClick={() => { if (value.trim()) setAdding(false) }}
        >
          ↵
        </button>
        <button
          type="button" className="lh-btn" title="Pick from existing"
          onClick={() => { setAdding(false); onChange('') }}
        >
          ↩
        </button>
      </div>
    )
  }
  // A confirmed brand-new department isn't in `options` yet — surface it as its own
  // selected option so the field shows the coined name (never snaps back to "(none)").
  const isCustom = value !== '' && !known
  return (
    <select
      className="lh-select"
      value={value}
      onChange={(e) => {
        if (e.target.value === NEW_DEPT) { setAdding(true); onChange('') }
        else onChange(e.target.value)
      }}
    >
      <option value="">(none)</option>
      {isCustom && <option value={value}>{value}</option>}
      {options.map((d) => <option key={d} value={d}>{d}</option>)}
      <option value={NEW_DEPT}>+ New department…</option>
    </select>
  )
}
