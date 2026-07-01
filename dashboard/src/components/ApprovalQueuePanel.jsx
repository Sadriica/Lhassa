import { useCallback, useEffect, useState } from 'react'
import { toast } from '../lib/toast'

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
      toast.success(approved ? 'Approved.' : 'Denied.')
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Resolve failed'
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
          Approval queue
        </h2>
        <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>{approvals.length} pending</span>
      </div>

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : approvals.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Nothing awaiting approval.</p>
      ) : (
        <ul className="space-y-2">
          {approvals.map(a => {
            const p = a.payload || {}
            return (
              <li key={a.id} className="lh-row">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium" style={{ color: 'var(--lh-text)' }}>{a.tipo || p.accion || 'action'}</span>
                  <span className="text-[10px]" style={{ color: 'var(--lh-text-muted)' }}>{a.creado_en}</span>
                </div>
                {(p.objetivo || p.actor_id) && (
                  <div className="text-[11px] mt-1" style={{ color: 'var(--lh-text-muted)' }}>
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
                    className="lh-btn lh-btn-primary"
                  >
                    {busy === a.id ? '…' : 'Approve'}
                  </button>
                  <button
                    type="button"
                    disabled={busy === a.id}
                    onClick={() => resolve(a.id, false)}
                    className="lh-btn lh-btn-danger"
                  >
                    {busy === a.id ? '…' : 'Deny'}
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {error && <div className="text-xs mt-2" style={{ color: 'var(--lh-alert)' }}>{error}</div>}
    </div>
  )
}
