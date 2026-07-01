import { useEffect, useState } from 'react'

const STATUS_COLOR = {
  pending: 'var(--lh-accent)',
  done:    'var(--lh-ok)',
  error:   'var(--lh-alert)',
}

export default function ScheduledPanel({ refreshTick = 0 }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)

  const load = () => {
    fetch('/api/scheduled')
      .then(r => r.json())
      .then(data => { setTasks(data); setLoading(false) })
      .catch(() => setLoading(false))
  }

  // Refresh on interval AND whenever a task fires (refreshTick changes)
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [refreshTick])

  const pending = tasks.filter(t => t.status === 'pending')

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="lh-card-title">
          Scheduled
        </h2>
        {pending.length > 0 && (
          <span className="text-xs" style={{ color: 'var(--lh-accent-text)' }}>{pending.length} pending</span>
        )}
      </div>

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : tasks.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No scheduled tasks.</p>
      ) : (
        <div className="space-y-2 max-h-48 overflow-y-auto">
          {tasks.map(t => (
            <div key={t.id} className="flex items-start gap-2 text-xs">
              <span className="shrink-0 mt-0.5" style={{ color: STATUS_COLOR[t.status] || 'var(--lh-text-muted)' }}>●</span>
              <div className="min-w-0">
                <div className="truncate font-medium" style={{ color: 'var(--lh-text)' }}>{t.name}</div>
                <div style={{ color: 'var(--lh-text-muted)' }}>{t.run_at?.slice(0, 16).replace('T', ' ')}</div>
                <div className="truncate" style={{ color: 'var(--lh-text-muted)' }}>{t.message}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
