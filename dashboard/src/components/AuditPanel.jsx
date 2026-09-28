import { useEffect, useMemo, useState } from 'react'

const STATUS_STYLE = {
  ok: 'var(--lh-ok)',
  blocked: 'var(--lh-accent)',
  error: 'var(--lh-alert)',
}

function fmtTs(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function AuditPanel({ seed = [], refreshTick = 0 }) {
  const [events, setEvents] = useState(seed)
  const [loading, setLoading] = useState(seed.length === 0)
  const [query, setQuery] = useState('')

  useEffect(() => {
    setEvents(seed)
  }, [seed])

  useEffect(() => {
    fetch('/api/audit')
      .then(r => r.json())
      .then(data => {
        setEvents(data)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [refreshTick])

  // Client-side filter over the already-fetched rows (action / target / detail /
  // source / status); keeps a busy activity log searchable.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return events
    return events.filter((e) =>
      [e.action, e.target, e.detail, e.source, e.status]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    )
  }, [events, query])

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="lh-card-title">Audit</h2>
        <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>{events.length} recent</span>
      </div>

      {!loading && events.length > 0 && (
        <div className="relative mb-3">
          <input
            className="lh-input"
            style={{ paddingRight: query ? '1.9rem' : undefined }}
            placeholder="Filter by action, target, source…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="Clear filter"
              style={{
                position: 'absolute', right: '0.5rem', top: '50%', transform: 'translateY(-50%)',
                background: 'none', border: 'none', cursor: 'pointer', color: 'var(--lh-text-faint)', fontSize: '0.8rem',
              }}
            >
              ✕
            </button>
          )}
        </div>
      )}

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : events.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No sensitive actions recorded yet.</p>
      ) : filtered.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No events match “{query}”.</p>
      ) : (
        <div className="space-y-2 max-h-56 overflow-y-auto">
          {filtered.map((event, idx) => (
            <div key={`${event.ts}-${idx}`} className="text-xs pl-3" style={{ borderLeft: '2px solid var(--lh-border-strong)' }}>
              <div className="flex items-center gap-2">
                <span style={{ color: STATUS_STYLE[event.status] || 'var(--lh-text-muted)' }}>{event.status}</span>
                <span style={{ color: 'var(--lh-text)' }}>{event.action}</span>
                <span style={{ color: 'var(--lh-text-muted)' }}>{event.source}</span>
                <span style={{ color: 'var(--lh-text-faint)' }}>{fmtTs(event.ts)}</span>
              </div>
              <div className="truncate" style={{ color: 'var(--lh-text-muted)' }}>{event.target}</div>
              {event.detail && <div className="truncate" style={{ color: 'var(--lh-text-muted)' }}>{event.detail}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
