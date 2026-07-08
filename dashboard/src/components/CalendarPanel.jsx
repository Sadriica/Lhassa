import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from '../lib/toast'
import { requireOk } from '../lib/api'
import { SectionHeader, EmptyState, ErrorState } from './ui/common'

// The Calendar section — two parts:
//   General       → GET /api/calendar/events?hours=N, a day-grouped agenda.
//   Configuration → GET/POST/DELETE /api/calendar/calendars, POST /api/calendar/primary,
//                    POST /api/calendar/sync. Feed urls are SECRETS: the API only ever
//                    returns a masked tail, and this panel never asks for one back.
const HOURS_OPTIONS = [24, 48, 168]
const EVENTS_REFRESH_MS = 60000

function dayLabel(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'Unknown date'
  const today = new Date()
  const sameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  const tomorrow = new Date(today)
  tomorrow.setDate(today.getDate() + 1)
  if (sameDay(d, today)) return 'Today'
  if (sameDay(d, tomorrow)) return 'Tomorrow'
  return d.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' })
}

function timeLabel(iso, allDay) {
  if (allDay) return 'All day'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function groupByDay(events) {
  const groups = new Map()
  for (const ev of events) {
    if (!ev.start) continue
    const key = new Date(ev.start).toDateString()
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(ev)
  }
  return Array.from(groups.entries())
}

// ── General: the upcoming agenda ───────────────────────────────────────────────
function AgendaSection() {
  const [hours, setHours] = useState(48)
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async (h) => {
    try {
      const r = await fetch(`/api/calendar/events?hours=${h}`)
      await requireOk(r, 'Calendar events')
      const data = await r.json()
      setEvents(Array.isArray(data) ? data : [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load events.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let mounted = true
    const tick = () => { if (mounted) load(hours) }
    setLoading(true)
    tick()
    const t = setInterval(tick, EVENTS_REFRESH_MS)
    return () => { mounted = false; clearInterval(t) }
  }, [hours, load])

  const groups = useMemo(() => groupByDay(events), [events])

  return (
    <div className="lh-card">
      <SectionHeader
        eyebrow="Upcoming"
        title="Agenda"
        meta={
          <div className="flex items-center gap-1.5">
            {HOURS_OPTIONS.map((h) => (
              <button
                key={h}
                type="button"
                className={`lh-tag ${hours === h ? 'lh-tag-accent' : ''}`}
                style={{ cursor: 'pointer' }}
                onClick={() => setHours(h)}
              >
                {h}h
              </button>
            ))}
          </div>
        }
      />

      {loading ? (
        <EmptyState>Loading…</EmptyState>
      ) : error ? (
        <ErrorState>{error}</ErrorState>
      ) : groups.length === 0 ? (
        <EmptyState>
          No upcoming events in this window. Add a calendar below to see your agenda here.
        </EmptyState>
      ) : (
        <div className="space-y-3">
          {groups.map(([day, items]) => (
            <div key={day}>
              <div className="lh-label mb-1.5">{dayLabel(items[0].start)}</div>
              <ul className="space-y-1.5">
                {items.map((ev, i) => (
                  <li key={`${day}-${i}`} className="lh-row">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="lh-mono text-[11px] shrink-0" style={{ color: 'var(--lh-text-faint)' }}>
                          {timeLabel(ev.start, ev.all_day)}
                        </span>
                        <span className="text-xs font-medium truncate" style={{ color: 'var(--lh-text)' }}>
                          {ev.summary || '(no title)'}
                        </span>
                      </div>
                      {ev.calendar && <span className="lh-tag shrink-0">{ev.calendar}</span>}
                    </div>
                    {ev.location && (
                      <div className="text-[11px] mt-1" style={{ color: 'var(--lh-text-muted)' }}>
                        {ev.location}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Configuration: manage calendars, primary, sync ─────────────────────────────
function ConfigSection() {
  const [calendars, setCalendars] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState('')
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/calendar/calendars')
      await requireOk(r, 'Calendars')
      const data = await r.json()
      setCalendars(Array.isArray(data) ? data : [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load calendars.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const primary = calendars.find((c) => c.primary)

  const addCalendar = async (e) => {
    e.preventDefault()
    const trimmedName = name.trim()
    const trimmedUrl = url.trim()
    if (!trimmedName || !trimmedUrl) return
    setBusy(true)
    try {
      const r = await fetch('/api/calendar/calendars', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: trimmedName, url: trimmedUrl }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) {
        toast.error(body.detail || 'Could not add that calendar.')
        return
      }
      setCalendars(Array.isArray(body) ? body : [])
      setName('')
      setUrl('')
      toast.success(`Calendar "${trimmedName}" added.`)
    } catch {
      toast.error('Network error adding calendar.')
    } finally {
      setBusy(false)
    }
  }

  const removeCalendar = async (calName) => {
    setBusy(true)
    try {
      const r = await fetch(`/api/calendar/calendars/${encodeURIComponent(calName)}`, { method: 'DELETE' })
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        toast.error(body.detail || 'Could not remove that calendar.')
        return
      }
      await load()
      toast.success(`Calendar "${calName}" removed.`)
    } catch {
      toast.error('Network error removing calendar.')
    } finally {
      setBusy(false)
    }
  }

  const makePrimary = async (calName) => {
    setBusy(true)
    try {
      const r = await fetch('/api/calendar/primary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: calName }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) {
        toast.error(body.detail || 'Could not set that calendar as primary.')
        return
      }
      setCalendars(Array.isArray(body) ? body : [])
      toast.success(`"${calName}" is now the primary calendar.`)
    } catch {
      toast.error('Network error setting primary.')
    } finally {
      setBusy(false)
    }
  }

  const runSync = async () => {
    setSyncing(true)
    setSyncResult('')
    try {
      const r = await fetch('/api/calendar/sync', { method: 'POST' })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) {
        toast.error(body.detail || 'Sync failed.')
        return
      }
      setSyncResult(body.summary || '')
      toast.success(body.summary || 'Sync complete.')
    } catch {
      toast.error('Network error running sync.')
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <SectionHeader
          eyebrow="Master calendar"
          title="Sync"
          actions={
            <button type="button" className="lh-btn lh-btn-primary" disabled={syncing || !primary} onClick={runSync}>
              {syncing ? 'Syncing…' : 'Sync now'}
            </button>
          }
        />
        <p className="text-[11px]" style={{ color: 'var(--lh-text-muted)' }}>
          {primary
            ? `Checks every other calendar for events missing from "${primary.name}" and delivers each as a tappable .ics.`
            : 'Set a primary calendar below before syncing — there is nothing to reconcile against yet.'}
        </p>
        {syncResult && (
          <div className="lh-row mt-2 text-[11px]" style={{ color: 'var(--lh-text)' }}>{syncResult}</div>
        )}
      </div>

      <div className="lh-card">
        <SectionHeader
          eyebrow="Configuration"
          title="Calendars"
          meta={<span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>{calendars.length} configured</span>}
        />

        {loading ? (
          <EmptyState>Loading…</EmptyState>
        ) : error ? (
          <ErrorState>{error}</ErrorState>
        ) : calendars.length === 0 ? (
          <EmptyState>No calendars configured yet — add one below.</EmptyState>
        ) : (
          <ul className="space-y-1.5 mb-3">
            {calendars.map((c) => (
              <li key={c.name} className="lh-row">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-1.5 min-w-0">
                    {c.primary && <span className="lh-dot lh-dot-accent" title="Primary" />}
                    <span className="text-xs font-medium truncate" style={{ color: 'var(--lh-text)' }}>{c.name}</span>
                    <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{c.url_masked}</span>
                    {c.primary && <span className="lh-tag lh-tag-accent">primary</span>}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {!c.primary && (
                      <button type="button" className="lh-btn" disabled={busy} onClick={() => makePrimary(c.name)}>
                        Make primary
                      </button>
                    )}
                    <button type="button" className="lh-btn lh-btn-danger" disabled={busy} onClick={() => removeCalendar(c.name)}>
                      Remove
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}

        <form onSubmit={addCalendar} className="lh-row">
          <div className="lh-label mb-1.5">Add calendar</div>
          <div className="flex items-center gap-1.5 flex-wrap">
            <input
              className="lh-input"
              placeholder="name (e.g. Work)"
              value={name}
              disabled={busy}
              onChange={(e) => setName(e.target.value)}
              style={{ maxWidth: 180 }}
            />
            <input
              className="lh-input"
              placeholder="https://… feed link (a secret — never shown again)"
              value={url}
              disabled={busy}
              onChange={(e) => setUrl(e.target.value)}
              style={{ flex: 1, minWidth: 220 }}
            />
            <button type="submit" className="lh-btn lh-btn-primary" disabled={busy || !name.trim() || !url.trim()}>
              Add
            </button>
          </div>
          <p className="text-[11px] mt-1.5" style={{ color: 'var(--lh-text-faint)' }}>
            The feed url grants read access to that calendar. Lhassa stores it locally and never
            displays it again — only a masked tail is shown once added.
          </p>
        </form>
      </div>
    </div>
  )
}

export default function CalendarPanel() {
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <AgendaSection />
      <ConfigSection />
    </div>
  )
}
