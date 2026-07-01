import { useEffect, useRef, useState } from 'react'
import { toast } from '../lib/toast'

function Item({ label, value, tone = 'var(--lh-text)' }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span style={{ color: 'var(--lh-text-muted)' }}>{label}</span>
      <span className="text-right" style={{ color: tone }}>{String(value)}</span>
    </div>
  )
}

function fmtUptime(seconds = 0) {
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  if (mins < 60) return `${mins}m ${secs}s`
  const hours = Math.floor(mins / 60)
  return `${hours}h ${mins % 60}m`
}

export default function HealthPanel() {
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [message, setMessage] = useState('')
  const fileRef = useRef(null)

  useEffect(() => {
    let mounted = true

    const load = () => {
      fetch('/api/health')
        .then(r => r.json())
        .then(data => {
          if (!mounted) return
          setHealth(data)
          setLoading(false)
        })
        .catch(() => {
          if (!mounted) return
          setLoading(false)
        })
    }

    load()
    const t = setInterval(load, 10000)
    return () => {
      mounted = false
      clearInterval(t)
    }
  }, [])

  const handleImportClick = () => {
    fileRef.current?.click()
  }

  const handleImportFile = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return

    try {
      const text = await file.text()
      const payload = JSON.parse(text)

      setImporting(true)
      setMessage('')

      const previewResponse = await fetch('/api/import?dry_run=1', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const preview = await previewResponse.json()
      if (!previewResponse.ok) {
        throw new Error(preview.detail || 'Import validation failed')
      }

      const confirmed = window.confirm(
        `Import snapshot?\n\nAudit: ${preview.audit_events}\nFacts: ${preview.facts}\nSessions: ${preview.sessions}\nScheduled tasks: ${preview.scheduled_tasks}\n\nThis replaces current stored state.`
      )
      if (!confirmed) {
        setImporting(false)
        return
      }

      const response = await fetch('/api/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Import failed')
      }

      const summary = `Import complete: ${data.audit_events} audit, ${data.facts} facts, ${data.sessions} sessions, ${data.scheduled_tasks} tasks`
      setMessage(summary)
      toast.success(summary)
    } catch (error) {
      const msg = error instanceof Error ? error.message : 'Import failed'
      setMessage(msg)
      toast.error(msg)
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="lh-card-title">Runtime</h2>
        {!loading && health && (
          <span className="text-xs" style={{ color: health.voice_active ? 'var(--lh-ok)' : 'var(--lh-text-muted)' }}>
            {health.voice_active ? 'awake' : 'sleeping'}
          </span>
        )}
      </div>

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : !health ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Runtime info unavailable.</p>
      ) : (
        <div className="space-y-2">
          <Item label="Uptime" value={fmtUptime(health.uptime_seconds)} />
          <Item label="Provider" value={`${health.llm_provider} / ${health.model}`} />
          <Item label="Agents" value={health.agent_backend} />
          <Item label="Vision" value={health.vision_provider} />
          <Item label="Language" value={health.stt_language} />
          <Item label="WebSocket clients" value={health.ws_clients} />
          <Item label="Agent runs" value={health.agent_runs} />
          <Item
            label="Dashboard"
            value={health.dashboard_built ? 'built' : 'not built'}
            tone={health.dashboard_built ? 'var(--lh-ok)' : 'var(--lh-accent)'}
          />
          <Item
            label="Destructive shell"
            value={health.allow_destructive_shell ? 'allowed' : 'blocked'}
            tone={health.allow_destructive_shell ? 'var(--lh-accent)' : 'var(--lh-ok)'}
          />
          <Item
            label="Write root"
            value={health.file_write_root || 'unrestricted'}
            tone={health.file_write_root ? 'var(--lh-accent-text)' : 'var(--lh-accent)'}
          />
          <Item label="API" value={`${health.api_host}:${health.api_port}`} />
          <div className="pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
            <div className="flex items-center gap-3">
              <a
                href="/api/export"
                target="_blank"
                rel="noreferrer"
                className="text-xs"
                style={{ color: 'var(--lh-accent-text)' }}
              >
                Open JSON export
              </a>
              <button
                type="button"
                onClick={handleImportClick}
                disabled={importing}
                className="text-xs"
                style={{ color: importing ? 'var(--lh-text-faint)' : 'var(--lh-accent-text)' }}
              >
                {importing ? 'Importing…' : 'Import JSON snapshot'}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="application/json"
                className="hidden"
                onChange={handleImportFile}
              />
            </div>
            {message && (
              <div className="text-xs mt-2" style={{ color: 'var(--lh-text-muted)' }}>
                {message}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
