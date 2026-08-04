import { useCallback, useEffect, useState } from 'react'
import { getResearchAssessment, getResearchAssessments } from '../lib/api'
import { shortWhen } from '../lib/format'
import { EmptyState, ErrorState, SectionHeader } from './ui/common'

// The "Research" section — the idea-intake docket.
// GET /api/research/assessments (optionally filtered by status) and
// GET /api/research/assessments/{id}, which returns the row plus its `events`.
//
// Read-only. An idea enters through `/idea <text|link>` (a signed
// `research_intake` action at HUMANO trust), not through a dashboard form —
// this panel shows what the collector/analyst/skeptic/synthesist did with it.

const STATUSES = ['', 'pending', 'running', 'completed', 'failed']

function AssessmentRow({ row, onOpen, selected }) {
  return (
    <button
      type="button"
      className="lh-row text-left w-full"
      style={{
        background: selected ? 'var(--lh-surface-3, var(--lh-surface-2))' : 'var(--lh-surface-2)',
        cursor: 'pointer',
      }}
      onClick={() => onOpen(row.id)}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-sm truncate" style={{ color: 'var(--lh-text)' }}>
          {row.topic || row.titulo || row.idea || row.id}
        </span>
        <div className="flex items-center gap-1.5">
          {row.status && <span className="lh-badge">{row.status}</span>}
          <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
            {shortWhen(row.creado_en || row.created_at)}
          </span>
        </div>
      </div>
      {row.summary && (
        <div className="text-[11px] mt-1 truncate" style={{ color: 'var(--lh-text-muted)' }}>{row.summary}</div>
      )}
    </button>
  )
}

function AssessmentDetail({ assessment }) {
  const events = assessment.events || []
  const fields = Object.entries(assessment).filter(
    ([key, value]) => key !== 'events' && value !== null && value !== '' && typeof value !== 'object'
  )
  return (
    <div className="space-y-3">
      <div className="lh-card">
        <SectionHeader
          eyebrow="Assessment"
          title={assessment.topic || assessment.titulo || assessment.id}
        />
        <dl className="text-[11px] space-y-1 mt-1">
          {fields.map(([key, value]) => (
            <div key={key} className="flex gap-2">
              <dt className="lh-label w-28 shrink-0">{key}</dt>
              <dd className="truncate" style={{ color: 'var(--lh-text-muted)' }}>{String(value)}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="lh-card">
        <SectionHeader eyebrow="Trail" title={`${events.length} events`} />
        {events.length === 0 ? (
          <EmptyState>No events recorded for this assessment.</EmptyState>
        ) : (
          <ul className="space-y-1 max-h-[50vh] overflow-y-auto">
            {events.map((e, i) => (
              <li key={e.id ?? i} className="lh-row">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <span className="text-[12px]" style={{ color: 'var(--lh-text)' }}>
                    {e.stage || e.kind || e.tipo || '—'}
                  </span>
                  <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
                    {shortWhen(e.creado_en || e.created_at)}
                  </span>
                </div>
                {(e.detail || e.detalle || e.summary) && (
                  <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>
                    {e.detail || e.detalle || e.summary}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default function ResearchPanel() {
  const [rows, setRows] = useState([])
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [openId, setOpenId] = useState(null)
  const [detail, setDetail] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRows(await getResearchAssessments({ status: status || undefined, limit: 100 }))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load research assessments.')
    } finally {
      setLoading(false)
    }
  }, [status])

  useEffect(() => { load() }, [load])

  const open = useCallback(async (id) => {
    setOpenId(id)
    setDetail(null)
    try {
      setDetail(await getResearchAssessment(id))
    } catch {
      setDetail(null)
    }
  }, [])

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div className="lh-card">
        <SectionHeader
          eyebrow="Idea intake"
          title="Research assessments"
          meta={
            <select
              className="lh-select text-xs"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              {STATUSES.map((s) => (
                <option key={s || 'all'} value={s}>{s || 'all statuses'}</option>
              ))}
            </select>
          }
          actions={<button type="button" className="lh-btn" onClick={load}>refresh</button>}
        />
        {loading ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
        ) : error ? (
          <ErrorState>{error}</ErrorState>
        ) : rows.length === 0 ? (
          <EmptyState>
            No assessments yet — send an idea with `/idea &lt;text or link&gt;` in chat.
          </EmptyState>
        ) : (
          <div className="space-y-1.5 max-h-[70vh] overflow-y-auto">
            {rows.map((r) => (
              <AssessmentRow key={r.id} row={r} onOpen={open} selected={r.id === openId} />
            ))}
          </div>
        )}
      </div>

      {detail ? (
        <AssessmentDetail assessment={detail} />
      ) : (
        <div className="lh-card">
          <SectionHeader eyebrow="Assessment" title="Nothing selected" />
          <EmptyState>Pick an assessment on the left to see its trail.</EmptyState>
        </div>
      )}
    </div>
  )
}
