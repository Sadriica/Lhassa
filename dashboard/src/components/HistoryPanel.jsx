import { useEffect, useState } from 'react'

export default function HistoryPanel() {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/memory/sessions')
      .then(r => r.json())
      .then(data => { setSessions(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
      <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
        Session History
      </h2>

      {loading ? (
        <p className="text-slate-600 text-xs">Loading…</p>
      ) : sessions.length === 0 ? (
        <p className="text-slate-600 text-xs">No sessions saved yet.</p>
      ) : (
        <div className="space-y-3 max-h-72 overflow-y-auto">
          {sessions.map((s, i) => (
            <div key={i} className="border-l-2 border-slate-700 pl-3">
              <div className="text-xs text-slate-500 mb-1">{s.date?.slice(0, 16).replace('T', ' ')}</div>
              <p className="text-xs text-slate-400 leading-relaxed">{s.summary}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
