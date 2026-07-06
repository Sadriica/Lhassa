import { useCallback, useEffect, useState } from 'react'

// Phase 6 "madurez" — reports + cost projections. Pulls /api/reports (recent
// episodes + outcome counts) and /api/projections (spend so far today + a simple
// linear projection off the same ledger the LLM panels use). Read-only.
function fmtUsd(v) {
  if (v === null || v === undefined) return '$0.0000'
  return `$${Number(v).toFixed(4)}`
}

export default function ReportsPanel() {
  const [reports, setReports] = useState(null)
  const [proj, setProj] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const [r1, r2] = await Promise.all([
        fetch('/api/reports?n=50'),
        fetch('/api/projections'),
      ])
      setReports(await r1.json())
      setProj(await r2.json())
      setError('')
    } catch {
      setError('Could not load reports.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let mounted = true
    const tick = () => { if (mounted) load() }
    tick()
    const t = setInterval(tick, 10000)
    return () => { mounted = false; clearInterval(t) }
  }, [load])

  const recent = reports?.recent || []
  const reportTotal = Number(reports?.total ?? reports?.count ?? 0)
  const today = proj?.today
  const projection = proj?.projection

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="lh-card-title">
          Reports & projections
        </h2>
        <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>
          latest {reports?.count ?? 0} of {reportTotal} episodes
        </span>
      </div>

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : (
        <>
          {today && (
            <div className="text-[11px] mb-2" style={{ color: 'var(--lh-text-muted)' }}>
              <span style={{ color: 'var(--lh-text)' }}>Today: {fmtUsd(today.cost_usd)}</span>
              {' · '}{today.input_tokens}+{today.output_tokens} tokens
              {projection && (
                <span style={{ color: 'var(--lh-text-muted)' }}>
                  {' · '}proj. day {fmtUsd(projection.projected_today_cost_usd)}
                  {' · '}month {fmtUsd(projection.projected_month_cost_usd)}
                </span>
              )}
            </div>
          )}

          {reports?.by_despertar && Object.keys(reports.by_despertar).length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {Object.entries(reports.by_despertar).map(([k, v]) => (
                <span key={k} className="lh-badge">
                  {k}: {v}
                </span>
              ))}
            </div>
          )}

          {recent.length === 0 ? (
            <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No episodes yet.</p>
          ) : (
            <ul className="space-y-1 max-h-64 overflow-y-auto">
              {recent.slice(0, 20).map(e => (
                <li key={e.id} className="lh-row">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px]" style={{ color: 'var(--lh-text)' }}>{e.decision || e.despertar_por || '—'}</span>
                    <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{e.creado_en}</span>
                  </div>
                  {e.resultado && (
                    <div className="text-[11px] mt-0.5 truncate" style={{ color: 'var(--lh-text-muted)' }}>{e.resultado}</div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {error && <div className="text-xs mt-2" style={{ color: 'var(--lh-alert)' }}>{error}</div>}
    </div>
  )
}
