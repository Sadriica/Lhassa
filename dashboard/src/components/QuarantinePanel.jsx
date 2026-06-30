import { useCallback, useEffect, useState } from 'react'

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
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Graduation failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Quarantine room
        </h2>
        <span className="text-xs text-slate-500">{tools.length} tools</span>
      </div>

      {loading ? (
        <p className="text-slate-600 text-xs">Loading…</p>
      ) : tools.length === 0 ? (
        <p className="text-slate-600 text-xs">No self-written tools yet.</p>
      ) : (
        <ul className="space-y-2">
          {tools.map(t => {
            const estado = t.estado || 'cuarentena'
            const badge = ESTADO_STYLES[estado] || 'bg-slate-800 text-slate-300 border-slate-700'
            return (
              <li key={t.id || t.nombre} className="border border-slate-800 rounded p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-slate-200">{t.nombre}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${badge}`}>
                    {estado}
                  </span>
                </div>
                <div className="text-[11px] text-slate-500 mt-1">
                  {t.dominio ? `domain: ${t.dominio}` : 'domain: —'}
                  {' · '}
                  metric: {fmtMetric(t.metrica_actual)} (baseline {fmtMetric(t.metrica_baseline)})
                </div>
                <div className="text-[11px] text-slate-600 mt-0.5">
                  uses: {t.usos ?? 0} · fails: {t.fallos ?? 0}
                </div>
                {estado === 'cuarentena' && (
                  <div className="flex gap-2 mt-2">
                    <button
                      type="button"
                      disabled={busy === t.nombre}
                      onClick={() => graduate(t.nombre)}
                      className="text-xs px-2 py-1 rounded bg-green-700 hover:bg-green-600 disabled:bg-slate-700 text-white"
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

      {error && <div className="text-xs text-amber-400 mt-2">{error}</div>}
    </div>
  )
}
