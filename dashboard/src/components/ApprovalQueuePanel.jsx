import { useCallback, useEffect, useState } from 'react'
import { getApprovalBriefs, resolveApproval } from '../lib/api'
import { toast } from '../lib/toast'
import { shortWhen } from '../lib/format'

const RIESGO_PILL = {
  low: { label: 'low', cls: 'lh-pill lh-pill-ok' },
  medium: { label: 'medium', cls: 'lh-pill lh-pill-warn' },
  high: { label: 'high', cls: 'lh-pill lh-pill-danger' },
  // legacy rows stored before the queue moved to English
  baja: { label: 'low', cls: 'lh-pill lh-pill-ok' },
  media: { label: 'medium', cls: 'lh-pill lh-pill-warn' },
  alta: { label: 'high', cls: 'lh-pill lh-pill-danger' },
}

// The decision queue, enriched: each row carries a category, an origin
// (brain / flow / department), a risk level and a brief with a summary plus
// Lhassa's recommendation (why yes or why no), newest decision first.
export default function ApprovalQueuePanel() {
  const [approvals, setApprovals] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await getApprovalBriefs()
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
      await resolveApproval(id, approved)
      setApprovals(prev => prev.filter(a => a.id !== id))
      toast.success(approved ? 'Approved.' : 'Rejected.')
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
        <h2 className="lh-card-title">Decisions queue</h2>
        <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>{approvals.length} pending</span>
      </div>

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : approvals.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No decisions waiting for approval.</p>
      ) : (
        <ul className="space-y-3">
          {approvals.map(a => {
            const p = a.payload || {}
            const s = a.summary || {}
            const risk = RIESGO_PILL[a.riesgo] || RIESGO_PILL.medium
            const rec = /^(reject|rechazar)/i.test(a.recomendacion || '')
              ? { label: a.recomendacion, cls: 'lh-pill lh-pill-danger' }
              : { label: a.recomendacion || '-', cls: 'lh-pill lh-pill-ok' }
            return (
              <li key={a.id} className="lh-row">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium" style={{ color: 'var(--lh-text)' }}>
                    {s.title || a.tipo || p.accion || 'action'}
                  </span>
                  <span className="text-[10px] shrink-0" style={{ color: 'var(--lh-text-muted)' }}>{shortWhen(a.creado_en)}</span>
                </div>

                <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                  <span className="lh-badge">{a.categoria || 'General'}</span>
                  <span className="lh-badge">{a.origen || 'Lhassa'}</span>
                  <span className={risk.cls}>risk {risk.label}</span>
                  <span className={rec.cls}>{rec.label}</span>
                  <span className="text-[10px] lh-mono ml-auto" style={{ color: 'var(--lh-text-faint)' }}>{a.id}</span>
                </div>

                {s.detail && (
                  <div className="text-[11px] mt-1.5" style={{ color: 'var(--lh-text-muted)' }}>{s.detail}</div>
                )}

                {a.resumen && (
                  <div className="text-[11px] mt-1.5 lh-card" style={{ padding: '6px 10px', color: 'var(--lh-text)' }}>
                    {a.resumen}
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
                    {busy === a.id ? '…' : 'Reject'}
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
