export function fmtDuration(ms) {
  const n = Number(ms || 0)
  if (!n) return '—'
  if (n < 1000) return `${n}ms`
  const s = n / 1000
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`
  const m = Math.floor(s / 60)
  return `${m}m ${Math.round(s - m * 60)}s`
}

export function fmtPct(value) {
  if (value === null || value === undefined) return '—'
  return `${Math.round(Number(value) * 100)}%`
}

export function fmtUsd(value) {
  const n = Number(value || 0)
  if (!n) return '$0'
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`
}

export function shortWhen(value, fallback = 'never') {
  if (!value) return fallback
  const parsed = Date.parse(value)
  if (Number.isNaN(parsed)) return value
  return new Date(parsed).toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
