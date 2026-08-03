// ── limits editor ───────────────────────────────────────────────────────────
const LIMIT_PRESETS = ['max_risk', 'budget_usd', 'allowed_actions']
export function LimitsEditor({ rows, setRows }) {
  const update = (i, field, val) => setRows(rows.map((r, j) => (j === i ? { ...r, [field]: val } : r)))
  const remove = (i) => setRows(rows.filter((_, j) => j !== i))
  const add = (key = '') => setRows([...rows, { key, value: '' }])
  const presetsLeft = LIMIT_PRESETS.filter((p) => !rows.some((r) => r.key === p))
  return (
    <div className="space-y-1.5">
      {rows.map((r, i) => (
        <div key={i} className="flex gap-1.5 items-center">
          <input
            className="lh-input" style={{ flex: '0 0 42%' }} placeholder="key"
            value={r.key} onChange={(e) => update(i, 'key', e.target.value)}
          />
          <input
            className="lh-input" placeholder="value (num, true/false, or a,b,c)"
            value={r.value} onChange={(e) => update(i, 'value', e.target.value)}
          />
          <button type="button" className="lh-btn" onClick={() => remove(i)} title="Remove">✕</button>
        </div>
      ))}
      <div className="flex flex-wrap gap-1.5">
        <button type="button" className="lh-btn" onClick={() => add()}>+ limit</button>
        {presetsLeft.map((p) => (
          <button key={p} type="button" className="lh-btn" onClick={() => add(p)}>+ {p}</button>
        ))}
      </div>
      <p className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
        ✕ or an empty value removes a limit. Click Save changes to persist.
      </p>
    </div>
  )
}
