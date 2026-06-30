import { useCallback, useEffect, useState } from 'react'

// The approval queue (§2.9 — "la cola de aprobaciones"). Lists pending approvals
// and lets a human approve or deny each one. Resolving feeds the autonomy knob on
// the server: a repeatedly approved human action earns auto-approval.
export default function ApprovalQueuePanel() {
  const [approvals, setApprovals] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null) // id currently being resolved
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/approvals')
      const data = await r.json()
      setApprovals(Array.isArray(data) ? data : [])
      setError('')
    } catch {
      setError('Could not load approvals.')
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

  const resolve = async (id, approved) => {
    setBusy(id)
    setError('')
    try {
      const r = await fetch(`/api/approvals/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Resolve failed')
      }
      setApprovals(prev => prev.filter(a => a.id !== id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Resolve failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Approval queue
        </h2>
        <span className="text-xs text-slate-500">{approvals.length} pending</span>
      </div>

      {loading ? (
        <p className="text-slate-600 text-xs">Loading…</p>
      ) : approvals.length === 0 ? (
        <p className="text-slate-600 text-xs">Nothing awaiting approval.</p>
      ) : (
        <ul className="space-y-2">
          {approvals.map(a => {
            const p = a.payload || {}
            return (
              <li key={a.id} className="border border-slate-800 rounded p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-slate-200">{a.tipo || p.accion || 'action'}</span>
                  <span className="text-[10px] text-slate-500">{a.creado_en}</span>
                </div>
                {(p.objetivo || p.actor_id) && (
                  <div className="text-[11px] text-slate-500 mt-1">
                    {p.objetivo ? `target: ${p.objetivo}` : ''}
                    {p.objetivo && p.actor_id ? ' · ' : ''}
                    {p.actor_id ? `who: ${p.actor_id}` : ''}
                  </div>
                )}
                <div className="flex gap-2 mt-2">
                  <button
                    type="button"
                    disabled={busy === a.id}
                    onClick={() => resolve(a.id, true)}
                    className="text-xs px-2 py-1 rounded bg-green-700 hover:bg-green-600 disabled:bg-slate-700 text-white"
                  >
                    {busy === a.id ? '…' : 'Approve'}
                  </button>
                  <button
                    type="button"
                    disabled={busy === a.id}
                    onClick={() => resolve(a.id, false)}
                    className="text-xs px-2 py-1 rounded bg-red-800 hover:bg-red-700 disabled:bg-slate-700 text-white"
                  >
                    {busy === a.id ? '…' : 'Deny'}
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {error && <div className="text-xs text-amber-400 mt-2">{error}</div>}
    </div>
  )
}
