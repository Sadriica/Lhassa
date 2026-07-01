import { useCallback, useEffect, useState } from 'react'
import { toast } from '../lib/toast'

// The quarantine room (§2.9 — "sala de cuarentena"). Lists self-written tools by
// estado (cuarentena / graduada / rechazada) with their gate metric + usage info,
// and lets a human graduate a candidate — "aprobación = merge": the server routes a
// tool_graduation approval through the queue and merges the candidate branch.
const ESTADO_STYLES = {
  cuarentena: 'bg-amber-900/40 text-amber-300 border-amber-700',
  graduada: 'bg-green-900/40 text-green-300 border-green-700',
  rechazada: 'bg-red-900/40 text-red-300 border-red-700',
}

function fmtMetric(v) {
  if (v === null || v === undefined) return '—'
  return typeof v === 'number' ? v.toLocaleString() : String(v)
}

export default function QuarantinePanel() {
  const [tools, setTools] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/tools')
      const data = await r.json()
      setTools(Array.isArray(data) ? data : [])
      setError('')
    } catch {
      setError('Could not load tools.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let mounted = true
    const tick = () => { if (mounted) load() }
    tick()
    const t = setInterval(tick, 5000)
    return () => { mounted = false; clearInterval(t) }
  }, [load])

  const graduate = async (nombre) => {
    setBusy(nombre)
    setError('')
    try {
      const r = await fetch(`/api/tools/${encodeURIComponent(nombre)}/graduate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Graduation failed')
      }
      toast.success(`Graduated ${nombre}.`)
      await load()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Graduation failed'
      setError(msg)
      toast.error(msg)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="lh-card-title">
          Quarantine room
        </h2>
        <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>{tools.length} tools</span>
      </div>

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : tools.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No self-written tools yet.</p>
      ) : (
        <ul className="space-y-2">
          {tools.map(t => {
            const estado = t.estado || 'cuarentena'
            const badge = ESTADO_STYLES[estado]
            return (
              <li key={t.id || t.nombre} className="lh-row">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium" style={{ color: 'var(--lh-text)' }}>{t.nombre}</span>
                  {badge ? (
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${badge}`}>
                      {estado}
                    </span>
                  ) : (
                    <span className="lh-badge text-[10px]">{estado}</span>
                  )}
                </div>
                <div className="text-[11px] mt-1" style={{ color: 'var(--lh-text-muted)' }}>
                  {t.dominio ? `domain: ${t.dominio}` : 'domain: —'}
                  {' · '}
                  metric: {fmtMetric(t.metrica_actual)} (baseline {fmtMetric(t.metrica_baseline)})
                </div>
                <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>
                  uses: {t.usos ?? 0} · fails: {t.fallos ?? 0}
                </div>
                {estado === 'cuarentena' && (
                  <div className="flex gap-2 mt-2">
                    <button
                      type="button"
                      disabled={busy === t.nombre}
                      onClick={() => graduate(t.nombre)}
                      className="lh-btn lh-btn-primary"
                    >
                      {busy === t.nombre ? '…' : 'Approve graduation'}
                    </button>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {error && <div className="text-xs mt-2" style={{ color: 'var(--lh-alert)' }}>{error}</div>}
    </div>
  )
}
