import { useCallback, useEffect, useRef, useState } from 'react'
import { getFlowRun, getFlowRunEvents, getFlowRuns } from '../../lib/api'
import { shortWhen } from '../../lib/format'
import { EmptyState, ErrorState, SectionHeader } from '../ui/common'

// Durable flow runs — the client for `/api/flow-runs`, which had no surface at
// all until now. The list, one run's reconstructed record, and its append-only
// event stream (RUN_CREATED → STEP_* → CHECKPOINT_SAVED → RUN_COMPLETED …).
//
// A run that has not reached a terminal status is POLLED every few seconds, so
// the event stream fills in live without a WebSocket of its own. Polling stops
// the moment the run terminates — an idle brain costs nothing to watch.
//
// Every payload value renders as a plain string child. Never
// dangerouslySetInnerHTML: an event payload is data from the kernel, not markup.

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])
const POLL_MS = 3000

function statusColor(status) {
  if (status === 'completed') return 'var(--lh-ok)'
  if (TERMINAL.has(status)) return 'var(--lh-alert)'
  return 'var(--lh-warn)'
}

function EventRow({ event }) {
  const payload = event.payload && typeof event.payload === 'object' ? event.payload : {}
  const bits = Object.entries(payload)
    .filter(([, v]) => v !== null && v !== '' && typeof v !== 'object')
    .slice(0, 4)
  return (
    <li className="lh-row">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="lh-mono text-[11px]" style={{ color: 'var(--lh-text)' }}>{event.kind}</span>
        <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
          {shortWhen(event.created_at)}
        </span>
      </div>
      {bits.length > 0 && (
        <div className="text-[11px] mt-0.5 truncate" style={{ color: 'var(--lh-text-muted)' }}>
          {bits.map(([k, v]) => `${k}=${v}`).join(' · ')}
        </div>
      )}
    </li>
  )
}

export default function DurableRunsPanel({ flowId }) {
  const [runs, setRuns] = useState([])
  const [error, setError] = useState('')
  const [openId, setOpenId] = useState(null)
  const [record, setRecord] = useState(null)
  const [events, setEvents] = useState([])
  const timer = useRef(null)

  const loadRuns = useCallback(async () => {
    try {
      const rows = await getFlowRuns({ limit: 100 })
      setRuns(flowId ? rows.filter((r) => r.flow_id === flowId) : rows)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load flow runs.')
    }
  }, [flowId])

  // Subscribe to the run list; the cancel flag keeps a late response from
  // writing into an unmounted (or re-scoped) panel.
  useEffect(() => {
    let cancelled = false
    getFlowRuns({ limit: 100 })
      .then((rows) => {
        if (cancelled) return
        setRuns(flowId ? rows.filter((r) => r.flow_id === flowId) : rows)
        setError('')
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load flow runs.')
      })
    return () => { cancelled = true }
  }, [flowId])

  const loadDetail = useCallback(async (runId) => {
    try {
      const [rec, evs] = await Promise.all([getFlowRun(runId), getFlowRunEvents(runId)])
      setRecord(rec)
      setEvents(Array.isArray(evs) ? evs : [])
      return rec
    } catch {
      return null
    }
  }, [])

  // Poll only while the open run is still moving.
  useEffect(() => {
    if (timer.current) { clearInterval(timer.current); timer.current = null }
    if (!openId) return undefined
    let cancelled = false
    const tick = async () => {
      const rec = await loadDetail(openId)
      if (cancelled) return
      if (rec && TERMINAL.has(rec.status) && timer.current) {
        clearInterval(timer.current)
        timer.current = null
        loadRuns()
      }
    }
    tick()
    timer.current = setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      if (timer.current) { clearInterval(timer.current); timer.current = null }
    }
  }, [openId, loadDetail, loadRuns])

  const open = useCallback((runId) => {
    setOpenId(runId)
    setRecord(null)
    setEvents([])
  }, [])

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div className="lh-card">
        <SectionHeader
          eyebrow="Durable"
          title={flowId ? `Runs of ${flowId}` : 'Flow runs'}
          actions={<button type="button" className="lh-btn" onClick={loadRuns}>refresh</button>}
        />
        {error ? (
          <ErrorState>{error}</ErrorState>
        ) : runs.length === 0 ? (
          <EmptyState>No durable runs recorded yet.</EmptyState>
        ) : (
          <div className="space-y-1 max-h-[60vh] overflow-y-auto">
            {runs.map((r) => (
              <button
                key={r.id}
                type="button"
                className="lh-row text-left w-full"
                style={{
                  background: r.id === openId ? 'var(--lh-surface-3, var(--lh-surface-2))' : 'var(--lh-surface-2)',
                  cursor: 'pointer',
                }}
                onClick={() => open(r.id)}
              >
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <span className="lh-mono text-[11px] truncate" style={{ color: 'var(--lh-text)' }}>{r.id}</span>
                  <span className="lh-tag" style={{ color: statusColor(r.status), background: 'transparent' }}>
                    {r.status}
                  </span>
                </div>
                <div className="text-[10px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>
                  {r.flow_id} · checkpoint {r.checkpoint_seq ?? '—'} · {shortWhen(r.updated_at)}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="lh-card">
        {!openId ? (
          <>
            <SectionHeader eyebrow="Run" title="Nothing selected" />
            <EmptyState>Pick a run to see its record and event stream.</EmptyState>
          </>
        ) : (
          <>
            <SectionHeader
              eyebrow="Run"
              title={openId}
              meta={
                record && !TERMINAL.has(record.status) ? (
                  <span className="text-[10px]" style={{ color: 'var(--lh-warn)' }}>live · polling</span>
                ) : null
              }
            />
            {record && (
              <div className="flex gap-1.5 flex-wrap mb-2">
                <span className="lh-tag" style={{ color: statusColor(record.status), background: 'transparent' }}>
                  {record.status}
                </span>
                <span className="lh-tag">{(record.steps || []).length} steps</span>
                {Number(record.cost_usd || 0) > 0 && (
                  <span className="lh-tag">${Number(record.cost_usd).toFixed(4)}</span>
                )}
              </div>
            )}
            {events.length === 0 ? (
              <EmptyState>No events recorded for this run.</EmptyState>
            ) : (
              <ul className="space-y-1 max-h-[60vh] overflow-y-auto">
                {events.map((e, i) => <EventRow key={e.id ?? i} event={e} />)}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  )
}
