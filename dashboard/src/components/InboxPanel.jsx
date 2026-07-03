import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from '../lib/toast'

const POLL_MS = 8000

function tsValue(value) {
  if (!value) return 0
  if (typeof value === 'number') return value * 1000
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? 0 : parsed
}

function shortTime(value) {
  const ms = tsValue(value)
  if (!ms) return ''
  return new Date(ms).toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function toneFor(item) {
  if (item.kind === 'approval') return 'accent'
  if (item.status === 'error' || item.status === 'failed' || item.status === 'blocked') return 'alert'
  if (item.status === 'running') return 'accent'
  return 'muted'
}

function labelForKind(kind) {
  if (kind === 'approval') return 'Decision'
  if (kind === 'run') return 'Running work'
  if (kind === 'audit') return 'System event'
  return 'Decision record'
}

function itemDotClass(item) {
  const tone = toneFor(item)
  if (tone === 'alert') return 'lh-dot-alert'
  if (tone === 'accent') return 'lh-dot-accent'
  return 'lh-dot-ok'
}

function activityItems(overview) {
  return (overview?.activity?.recent || []).slice(0, 12).map((ep) => ({
    id: `episode-${ep.id || ep.creado_en}`,
    kind: 'activity',
    title: ep.decision || ep.accion || ep.despertar_por || 'Episode',
    body: ep.resultado || ep.despertar_por || '',
    status: ep.decision || 'logged',
    when: ep.creado_en,
    href: '#activity',
  }))
}

function approvalItems(overview) {
  return (overview?.approvals?.items || []).map((a) => ({
    id: `approval-${a.id}`,
    rawId: a.id,
    kind: 'approval',
    title: a.summary || a.accion || a.tipo || 'Approval requested',
    body: [a.tipo, a.actor_id ? `actor ${a.actor_id}` : null].filter(Boolean).join(' · '),
    status: 'needs approval',
    when: a.creado_en,
    href: '#approvals',
  }))
}

function runItems(overview) {
  return (overview?.now_running || []).map((run) => ({
    id: `run-${run.id}`,
    kind: 'run',
    title: run.task || run.name || 'Agentic run',
    body: run.cli || run.name || '',
    status: run.status || 'running',
    when: run.started_at,
    href: '#run',
  }))
}

function auditItems(audit) {
  return (audit || []).slice(0, 18).map((event, idx) => ({
    id: `audit-${event.ts}-${idx}`,
    kind: 'audit',
    title: event.action || 'Audit event',
    body: [event.target, event.detail].filter(Boolean).join(' · '),
    status: event.status || 'logged',
    when: event.ts,
    href: '#activity',
  }))
}

export default function InboxPanel() {
  const [overview, setOverview] = useState(null)
  const [audit, setAudit] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(null)
  const [filter, setFilter] = useState('open')

  const load = useCallback(async () => {
    try {
      const [overviewRes, auditRes] = await Promise.all([
        fetch('/api/overview'),
        fetch('/api/audit'),
      ])
      if (!overviewRes.ok) throw new Error('overview unavailable')
      const overviewData = await overviewRes.json()
      const auditData = auditRes.ok ? await auditRes.json() : []
      setOverview(overviewData)
      setAudit(Array.isArray(auditData) ? auditData : [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load inbox.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [load])

  const resolve = async (id, approved) => {
    setBusy(id)
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
      toast.success(approved ? 'Approved.' : 'Denied.')
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Resolve failed')
    } finally {
      setBusy(null)
    }
  }

  const items = useMemo(() => {
    const all = [
      ...approvalItems(overview),
      ...runItems(overview),
      ...auditItems(audit),
      ...activityItems(overview),
    ].sort((a, b) => tsValue(b.when) - tsValue(a.when))

    if (filter === 'approval') return all.filter((i) => i.kind === 'approval')
    if (filter === 'attention') {
      return all.filter((i) => i.kind === 'approval' || i.status === 'error' || i.status === 'blocked' || i.status === 'running')
    }
    return all
  }, [overview, audit, filter])

  const pending = overview?.approvals?.pending || 0
  const running = overview?.now_running?.length || 0

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <div className="lh-card-head">
          <div>
            <div className="lh-eyebrow">Attention queue</div>
            <h2 className="lh-card-title mt-1">What needs a decision</h2>
          </div>
          <button type="button" className="lh-btn" onClick={load}>Refresh</button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="lh-row">
            <div className="lh-label">decisions waiting</div>
            <div className="lh-mono text-lg mt-1" style={{ color: pending ? 'var(--lh-accent-text)' : 'var(--lh-text)' }}>{pending}</div>
          </div>
          <div className="lh-row">
            <div className="lh-label">running work</div>
            <div className="lh-mono text-lg mt-1" style={{ color: running ? 'var(--lh-accent-text)' : 'var(--lh-text)' }}>{running}</div>
          </div>
          <div className="lh-row">
            <div className="lh-label">logged events</div>
            <div className="lh-mono text-lg mt-1">{audit.length}</div>
          </div>
        </div>
      </div>

      <div className="lh-card">
        <div className="lh-card-head">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="lh-eyebrow">Attention feed</span>
            <span className="lh-tag">{items.length} shown</span>
          </div>
          <div className="flex gap-1">
            {[
              ['open', 'All events'],
              ['attention', 'Attention'],
              ['approval', 'Decisions'],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                className="lh-btn"
                onClick={() => setFilter(id)}
                style={filter === id ? { borderColor: 'var(--lh-gold-deep)', color: 'var(--lh-accent-text)' } : undefined}
                aria-pressed={filter === id}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading inbox…</p>
        ) : error ? (
          <div className="lh-row" style={{ color: 'var(--lh-alert)', borderColor: 'rgba(162,62,52,0.45)' }}>{error}</div>
        ) : items.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No decisions or warnings need attention.</p>
        ) : (
          <ul className="space-y-2">
            {items.map((item) => (
              <li key={item.id} className="lh-row">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`lh-dot ${itemDotClass(item)}`} />
                      <span className="text-[13px] font-medium" style={{ color: 'var(--lh-text)' }}>{item.title}</span>
                      <span className="lh-tag">{labelForKind(item.kind)}</span>
                      <span className="lh-tag">{item.status}</span>
                    </div>
                    {item.body && (
                      <div className="text-[11px] mt-1 truncate max-w-3xl" style={{ color: 'var(--lh-text-muted)' }} title={item.body}>
                        {item.body}
                      </div>
                    )}
                    <div className="lh-mono text-[10px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>{shortTime(item.when)}</div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {item.kind === 'approval' ? (
                      <>
                        <button type="button" className="lh-btn lh-btn-primary" disabled={busy === item.rawId} onClick={() => resolve(item.rawId, true)}>
                          {busy === item.rawId ? '…' : 'Approve'}
                        </button>
                        <button type="button" className="lh-btn lh-btn-danger" disabled={busy === item.rawId} onClick={() => resolve(item.rawId, false)}>
                          {busy === item.rawId ? '…' : 'Deny'}
                        </button>
                      </>
                    ) : (
                      <button type="button" className="lh-btn" onClick={() => { window.location.hash = item.href.replace(/^#/, '') }}>
                        Open
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
