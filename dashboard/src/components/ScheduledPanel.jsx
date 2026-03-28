import { useEffect, useState } from 'react'

const STATUS_COLOR = {
  pending: 'text-amber-400',
  done:    'text-green-400',
  error:   'text-red-400',
}

export default function ScheduledPanel() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)

  const load = () => {
    fetch('/api/scheduled')
      .then(r => r.json())
      .then(data => { setTasks(data); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  const pending = tasks.filter(t => t.status === 'pending')
  const done = tasks.filter(t => t.status === 'done')

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Scheduled
        </h2>
        {pending.length > 0 && (
          <span className="text-xs text-amber-400">{pending.length} pending</span>
        )}
      </div>

      {loading ? (
        <p className="text-slate-600 text-xs">Loading…</p>
      ) : tasks.length === 0 ? (
        <p className="text-slate-600 text-xs">No scheduled tasks.</p>
      ) : (
        <div className="space-y-2 max-h-48 overflow-y-auto">
          {tasks.map(t => (
            <div key={t.id} className="flex items-start gap-2 text-xs">
              <span className={`shrink-0 mt-0.5 ${STATUS_COLOR[t.status] || 'text-slate-400'}`}>●</span>
              <div className="min-w-0">
                <div className="text-slate-300 truncate font-medium">{t.name}</div>
                <div className="text-slate-500">{t.run_at?.slice(0, 16).replace('T', ' ')}</div>
                <div className="text-slate-400 truncate">{t.message}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
