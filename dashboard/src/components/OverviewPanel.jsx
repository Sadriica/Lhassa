import { useCallback, useEffect, useRef, useState } from 'react'

// The Overview home — the "see everything + approve at a glance" surface.
//   GET /api/overview → { brains, skills, approvals:{pending,items[]}, cost,
//                         health, activity:{recent[]}, tools, errors, … }
// Polled every ~10s. Approvals are resolved inline against the same endpoint the
// ApprovalQueuePanel uses (POST /api/approvals/{id} {approved}); after a resolve
// we refetch so the digest stays truthful. Defensive: a partial/failed section is
// surfaced subtly under `errors` and never blocks the rest of the home.

const POLL_MS = 10000

function fmtUsd(n) {
  const v = Number(n || 0)
  if (v === 0) return '$0.00'
  if (v < 0.01) return `$${v.toFixed(4)}`
  return `$${v.toFixed(2)}`
}

function fmtInt(n) {
  return Number(n || 0).toLocaleString()
}

// worst_level → a dot class + a label tone.
function healthDot(level, ok) {
  const l = String(level || '').toLowerCase()
  if (l === 'critical' || l === 'alert' || l === 'error') return 'lh-dot-alert'
  if (l === 'warn' || l === 'warning') return 'lh-dot-warn'
  if (ok || l === 'ok' || l === 'info' || l === '') return 'lh-dot-ok'
  return 'lh-dot-warn'
}

// ── a compact stat card ───────────────────────────────────────────────────────
function StatCard({ label, value, sub, children, accent }) {
  return (
    <div className="lh-card" style={{ padding: '0.85rem 0.95rem' }}>
      <div className="lh-eyebrow">{label}</div>
      <div
        className="mt-1.5 font-bold lh-mono"
        style={{ fontSize: 26, lineHeight: 1, color: accent || 'var(--lh-text)' }}
      >
        {value}
      </div>
      {sub && (
        <div className="text-[11px] mt-1.5" style={{ color: 'var(--lh-text-faint)' }}>{sub}</div>
      )}
      {children}
    </div>
  )
}

export default function OverviewPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(null) // approval id being resolved
  const [resolveErr, setResolveErr] = useState('')
  const mounted = useRef(true)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/overview')
      if (!r.ok) throw new Error('overview fetch failed')
      const d = await r.json()
      if (mounted.current) { setData(d); setError('') }
    } catch {
      if (mounted.current) setError('Could not load the overview.')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    load()
    const t = setInterval(load, POLL_MS)
    return () => { mounted.current = false; clearInterval(t) }
  }, [load])

  const resolve = async (id, approved) => {
    setBusy(id)
    setResolveErr('')
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
      await load() // refetch so the home reflects the resolved queue
    } catch (e) {
      setResolveErr(e instanceof Error ? e.message : 'Resolve failed')
    } finally {
      setBusy(null)
    }
  }

  if (loading) {
    return (
      <div className="lh-card">
        <h2 className="lh-card-title mb-3">Overview</h2>
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading the overview…</p>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="lh-card">
        <div className="flex items-center justify-between mb-3">
          <h2 className="lh-card-title">Overview</h2>
          <button type="button" className="lh-btn" onClick={load}>Retry</button>
        </div>
        <div className="lh-row" style={{ borderColor: 'rgba(162,62,52,0.45)', color: 'var(--lh-alert)' }}>{error}</div>
      </div>
    )
  }

  const brains = data?.brains || {}
  const skills = data?.skills || {}
  const approvals = data?.approvals || { pending: 0, items: [] }
  const cost = data?.cost || {}
  const health = data?.health || {}
  const activity = data?.activity || { recent: [] }
  const tools = data?.tools || { count: 0, by_estado: {} }
  const errors = data?.errors || {}
  const errorKeys = Object.keys(errors)
  const items = approvals.items || []
  const recent = activity.recent || []
  const estados = Object.entries(tools.by_estado || {})

  const budget = cost.daily_token_budget
  const budgetLabel = !budget || budget === 0
    ? 'unlimited budget'
    : `${fmtInt(cost.output_tokens_today)} / ${fmtInt(budget)} out tok`

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="lh-card">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <h2 className="lh-card-title">Overview</h2>
            <span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
              everything at a glance · approve from here
            </span>
          </div>
          <div className="flex items-center gap-2">
            {data?.instance_id && (
              <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
                {data.instance_id}
              </span>
            )}
            <button type="button" className="lh-btn" onClick={load}>Refresh</button>
          </div>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4">
        <StatCard
          label="Brains"
          value={fmtInt(brains.count)}
          sub={brains.admin ? `admin · ${brains.admin}` : (brains.standalone ? 'standalone' : '—')}
        />
        <StatCard
          label="Skills"
          value={fmtInt(skills.installed)}
          sub={`${fmtInt(skills.recommended)} recommended`}
        />
        <StatCard
          label="Cost today"
          value={fmtUsd(cost.today_cost_usd)}
          accent="var(--lh-green)"
          sub={budgetLabel}
        >
          {cost.kill_switch && (
            <div className="mt-2">
              <span className="lh-pill" style={{ color: 'var(--lh-alert)', borderColor: 'rgba(162,62,52,0.5)' }}>
                <span className="lh-dot lh-dot-alert" /> kill-switch on
              </span>
            </div>
          )}
        </StatCard>
        <StatCard label="Health" value={
          <span className="inline-flex items-center gap-2">
            <span className={`lh-dot ${healthDot(health.worst_level, health.ok)}`} style={{ width: 12, height: 12 }} />
            <span style={{ fontSize: 16 }}>{health.ok ? 'OK' : (health.worst_level || 'check')}</span>
          </span>
        } sub={health.ok ? 'all checks pass' : `worst: ${health.worst_level || '—'}`} />
        <StatCard label="Tools" value={fmtInt(tools.count)} sub="self-written">
          {estados.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {estados.map(([estado, n]) => (
                <span key={estado} className="lh-tag">{estado} · {n}</span>
              ))}
            </div>
          )}
        </StatCard>
      </div>

      {/* Approvals — pending + inline approve/deny */}
      <div className="lh-card" style={approvals.pending > 0 ? { borderColor: 'rgba(217,165,33,0.3)' } : undefined}>
        <div className="lh-card-head">
          <div className="flex items-center gap-2">
            <span className="lh-eyebrow">Approvals</span>
            {approvals.pending > 0 ? (
              <span className="lh-tag lh-tag-accent">{approvals.pending} pending</span>
            ) : (
              <span className="lh-tag">clear</span>
            )}
          </div>
        </div>
        {items.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Nothing awaiting approval.</p>
        ) : (
          <ul className="space-y-2">
            {items.map((a) => (
              <li key={a.id} className="lh-row">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[13px] font-medium" style={{ color: 'var(--lh-text)' }}>
                        {a.summary || a.accion || a.tipo || 'action'}
                      </span>
                      {a.tipo && <span className="lh-tag">{a.tipo}</span>}
                    </div>
                    <div className="lh-mono text-[10px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>
                      {a.actor_id ? `who: ${a.actor_id}` : ''}
                      {a.actor_id && a.creado_en ? ' · ' : ''}
                      {a.creado_en || ''}
                    </div>
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    <button
                      type="button" className="lh-btn lh-btn-primary" disabled={busy === a.id}
                      onClick={() => resolve(a.id, true)}
                    >
                      {busy === a.id ? '…' : 'Approve'}
                    </button>
                    <button
                      type="button" className="lh-btn lh-btn-danger" disabled={busy === a.id}
                      onClick={() => resolve(a.id, false)}
                    >
                      {busy === a.id ? '…' : 'Deny'}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        {resolveErr && <div className="text-[11px] mt-2" style={{ color: 'var(--lh-alert)' }}>{resolveErr}</div>}
      </div>

      {/* Recent activity */}
      <div className="lh-card">
        <div className="lh-card-head">
          <span className="lh-eyebrow">Recent activity</span>
          <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{recent.length}</span>
        </div>
        {recent.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No episodes yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {recent.map((ep) => (
              <li key={ep.id} className="lh-row" style={{ padding: '0.5rem 0.7rem' }}>
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <span className="text-[12px]" style={{ color: 'var(--lh-text-muted)' }}>
                    <span className="lh-tag" style={{ marginRight: 6 }}>{ep.despertar_por || 'beat'}</span>
                    {ep.decision || '—'}
                  </span>
                  <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{ep.creado_en}</span>
                </div>
                {ep.resultado && (
                  <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>{ep.resultado}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Errors — surfaced subtly */}
      {errorKeys.length > 0 && (
        <div className="lh-card" style={{ borderColor: 'rgba(162,62,52,0.25)' }}>
          <div className="lh-eyebrow" style={{ color: 'var(--lh-red-soft)' }}>Degraded sections</div>
          <ul className="mt-1.5 space-y-1">
            {errorKeys.map((k) => (
              <li key={k} className="text-[11px] lh-mono" style={{ color: 'var(--lh-text-faint)' }}>
                <span style={{ color: 'var(--lh-red-soft)' }}>{k}</span>: {errors[k]}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
