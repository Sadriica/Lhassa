// ── limits helpers ──────────────────────────────────────────────────────────
export function parseLimitValue(raw) {
  const s = String(raw).trim()
  if (s === '') return ''
  if (s.includes(',')) return s.split(',').map((x) => x.trim()).filter(Boolean)
  const n = Number(s)
  if (!Number.isNaN(n) && /^-?\d*\.?\d+$/.test(s)) return n
  if (s === 'true') return true
  if (s === 'false') return false
  return s
}
export function limitsToRows(limits) {
  return Object.entries(limits || {}).map(([key, value]) => ({
    key,
    value: Array.isArray(value) ? value.join(',') : String(value),
  }))
}
export function rowsToLimits(rows) {
  // The backend REPLACES limits with whatever object we send (a None/absent field
  // means "leave unchanged", so we always send a full object; never null). A key
  // is CLEARED by simply omitting it: removing a row drops it here, and an empty
  // value drops it too, so clearing the field is also a removal. Sending `{}`
  // clears every limit.
  const out = {}
  rows.forEach(({ key, value }) => {
    const k = key.trim()
    if (!k) return
    const v = parseLimitValue(value)
    if (v === '' || v === null || v === undefined) return // empty → remove the key
    out[k] = v
  })
  return out
}
