import { useEffect, useState } from 'react'

export default function HistoryPanel() {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/memory/sessions')
      .then(r => r.json())
      .then(data => { setSessions(data); setLoading(false) })
      .catch(() => setLoading(false))

    // Refresh every 60s; new sessions are written on Lhassa shutdown
    const t = setInterval(() => {
      fetch('/api/memory/sessions')
        .then(r => r.json())
        .then(data => setSessions(data))
        .catch(() => {})
    }, 60000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="lh-card">
      <h2 className="lh-card-title mb-3">
        Session History
      </h2>

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : sessions.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No sessions saved yet.</p>
      ) : (
        <div className="space-y-3 max-h-72 overflow-y-auto">
          {sessions.map((s, i) => (
            <div key={i} className="pl-3" style={{ borderLeft: '2px solid var(--lh-border-strong)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--lh-text-muted)' }}>{s.date?.slice(0, 16).replace('T', ' ')}</div>
              <p className="text-xs leading-relaxed" style={{ color: 'var(--lh-text-muted)' }}>{s.summary}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
