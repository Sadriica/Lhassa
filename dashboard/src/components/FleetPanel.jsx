import { useCallback, useEffect, useState } from 'react'

// Phase 6 "madurez" — the fleet view. Lists known machines (from /api/fleet) with
// their last check-in and desired_state reconciliation targets. Read-only.
export default function FleetPanel() {
  const [machines, setMachines] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/fleet')
      const data = await r.json()
      setMachines(Array.isArray(data) ? data : [])
      setError('')
    } catch {
      setError('Could not load fleet.')
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

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Fleet
        </h2>
        <span className="text-xs text-slate-500">{machines.length} machines</span>
      </div>

      {loading ? (
        <p className="text-slate-600 text-xs">Loading…</p>
      ) : machines.length === 0 ? (
        <p className="text-slate-600 text-xs">No machines known yet.</p>
      ) : (
        <ul className="space-y-2">
          {machines.map(m => {
            const ds = m.desired_state || {}
            const keys = Object.keys(ds)
            return (
              <li key={m.machine_id} className="border border-slate-800 rounded p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-slate-200">{m.machine_id}</span>
                  <span className="text-[10px] text-slate-600">
                    {m.last_checkin ? `seen ${m.last_checkin}` : 'never checked in'}
                  </span>
                </div>
                {keys.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {keys.map(k => (
                      <span key={k} className="text-[10px] px-1.5 py-0.5 rounded border border-slate-700 bg-slate-800 text-slate-400">
                        {k}={String(ds[k])}
                      </span>
                    ))}
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
