import { useEffect, useState } from 'react'

const STATUS_STYLE = {
  ok: 'text-green-400',
  blocked: 'text-amber-400',
  error: 'text-red-400',
}

function fmtTs(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function AuditPanel({ seed = [], refreshTick = 0 }) {
  const [events, setEvents] = useState(seed)
  const [loading, setLoading] = useState(seed.length === 0)

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

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Audit</h2>
        <span className="text-xs text-slate-500">{events.length} recent</span>
      </div>

      {loading ? (
        <p className="text-slate-600 text-xs">Loading…</p>
      ) : events.length === 0 ? (
        <p className="text-slate-600 text-xs">No sensitive actions recorded yet.</p>
      ) : (
        <div className="space-y-2 max-h-56 overflow-y-auto">
          {events.map((event, idx) => (
            <div key={`${event.ts}-${idx}`} className="text-xs border-l-2 border-slate-700 pl-3">
              <div className="flex items-center gap-2">
                <span className={STATUS_STYLE[event.status] || 'text-slate-400'}>{event.status}</span>
                <span className="text-slate-300">{event.action}</span>
                <span className="text-slate-500">{event.source}</span>
                <span className="text-slate-600">{fmtTs(event.ts)}</span>
              </div>
              <div className="text-slate-400 truncate">{event.target}</div>
              {event.detail && <div className="text-slate-500 truncate">{event.detail}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
