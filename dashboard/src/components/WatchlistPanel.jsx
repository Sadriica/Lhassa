import { useCallback, useEffect, useMemo, useState } from 'react'

const POLL_MS = 15000

function toMs(value) {
  if (!value) return 0
  if (typeof value === 'number') return value * 1000
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? 0 : parsed
}

function fmtWhen(value) {
  const ms = toMs(value)
  if (!ms) return 'unscheduled'
  return new Date(ms).toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function statusClass(status) {
  if (status === 'pending') return 'lh-dot-accent'
  if (status === 'done') return 'lh-dot-ok'
  if (status === 'error' || status === 'failed') return 'lh-dot-alert'
  return 'lh-dot-warn'
}

function isDue(task) {
  const when = toMs(task.run_at)
  return task.status === 'pending' && when > 0 && when <= Date.now()
}

export default function WatchlistPanel() {
  const [scheduled, setScheduled] = useState([])
  const [overview, setOverview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const [scheduledRes, overviewRes] = await Promise.all([
        fetch('/api/scheduled'),
        fetch('/api/overview'),
      ])
      if (!scheduledRes.ok) throw new Error('scheduled unavailable')
      const scheduledData = await scheduledRes.json()
      const overviewData = overviewRes.ok ? await overviewRes.json() : null
      setScheduled(Array.isArray(scheduledData) ? scheduledData : [])
      setOverview(overviewData)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load watchlist.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [load])

  const pending = useMemo(
    () => scheduled.filter((task) => task.status === 'pending').sort((a, b) => toMs(a.run_at) - toMs(b.run_at)),
    [scheduled],
  )
  const due = useMemo(() => pending.filter(isDue), [pending])
  const recent = useMemo(
    () => scheduled.filter((task) => task.status !== 'pending').slice(0, 8),
    [scheduled],
  )

  const pulse = overview?.pulse || {}
  const running = overview?.now_running || []

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <div className="lh-card-head">
          <div>
            <div className="lh-eyebrow">Watchlist</div>
            <h2 className="lh-card-title mt-1">Active watchers and due checks</h2>
          </div>
          <button type="button" className="lh-btn" onClick={load}>Refresh</button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div className="lh-row">
            <div className="lh-label">active watchers</div>
            <div className="lh-mono text-lg mt-1">{pending.length}</div>
          </div>
          <div className="lh-row">
            <div className="lh-label">due now</div>
            <div className="lh-mono text-lg mt-1" style={{ color: due.length ? 'var(--lh-alert)' : 'var(--lh-text)' }}>{due.length}</div>
          </div>
          <div className="lh-row">
            <div className="lh-label">running work</div>
            <div className="lh-mono text-lg mt-1" style={{ color: running.length ? 'var(--lh-accent-text)' : 'var(--lh-text)' }}>{running.length}</div>
          </div>
          <div className="lh-row">
            <div className="lh-label">autonomy heartbeat</div>
            <div className="mt-1 flex items-center gap-2 text-xs">
              <span className={`lh-dot ${pulse.alive ? 'lh-dot-ok' : 'lh-dot-warn'}`} />
              <span className="lh-mono">{pulse.mode || 'offline'}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="lh-card">
          <div className="lh-card-head">
            <span className="lh-eyebrow">Upcoming checks</span>
            <span className="lh-tag">{pending.length} pending</span>
          </div>
          {loading ? (
            <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading watch items…</p>
          ) : error ? (
            <div className="lh-row" style={{ color: 'var(--lh-alert)', borderColor: 'rgba(162,62,52,0.45)' }}>{error}</div>
          ) : pending.length === 0 ? (
            <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>
              No active watchers yet. Reminders, recurrence, file/repo changes and health thresholds should appear here.
            </p>
          ) : (
            <ul className="space-y-2">
              {pending.map((task) => (
                <li key={task.id} className="lh-row" style={isDue(task) ? { borderColor: 'rgba(162,62,52,0.45)' } : undefined}>
                  <div className="flex items-start gap-2">
                    <span className={`lh-dot ${statusClass(task.status)} mt-1`} />
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium truncate" style={{ color: 'var(--lh-text)' }}>{task.name || 'scheduled task'}</div>
                      <div className="lh-mono text-[10px] mt-1" style={{ color: isDue(task) ? 'var(--lh-alert)' : 'var(--lh-text-faint)' }}>
                        {isDue(task) ? 'due now · ' : ''}{fmtWhen(task.run_at)}
                      </div>
                      {task.message && (
                        <div className="text-[11px] mt-1 truncate" style={{ color: 'var(--lh-text-muted)' }}>{task.message}</div>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="lh-card">
          <div className="lh-card-head">
            <span className="lh-eyebrow">Recent triggers</span>
            <span className="lh-tag">{recent.length} rows</span>
          </div>
          {recent.length === 0 ? (
            <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No watcher has triggered yet.</p>
          ) : (
            <ul className="space-y-2">
              {recent.map((task) => (
                <li key={task.id} className="lh-row">
                  <div className="flex items-start gap-2">
                    <span className={`lh-dot ${statusClass(task.status)} mt-1`} />
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium truncate" style={{ color: 'var(--lh-text)' }}>{task.name || 'scheduled task'}</div>
                      <div className="lh-mono text-[10px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>
                        {task.status || 'done'} · {fmtWhen(task.run_at)}
                      </div>
                      {task.message && (
                        <div className="text-[11px] mt-1 truncate" style={{ color: 'var(--lh-text-muted)' }}>{task.message}</div>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="lh-card">
        <div className="lh-eyebrow">Coverage roadmap</div>
        <p className="text-xs mt-2 max-w-3xl" style={{ color: 'var(--lh-text-muted)' }}>
          This panel is wired to today&apos;s scheduled tasks and heartbeat. Once persistent file,
          repo, health-threshold and job watchers exist, they should appear here with the same
          due/trigger/outcome model.
        </p>
      </div>
    </div>
  )
}
