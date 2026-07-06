import { useCallback, useEffect, useMemo, useState } from 'react'
import { fmtDuration, fmtPct, fmtUsd, shortWhen } from '../lib/format'
import { requireOk } from '../lib/api'
import { EmptyState, ErrorState, MetricCard, SectionHeader } from './ui/common'

function Leaderboard({ title, items, empty }) {
  return (
    <div className="lh-card">
      <div className="lh-card-head">
        <span className="lh-eyebrow">{title}</span>
        <span className="lh-tag">{items.length} rows</span>
      </div>
      {items.length === 0 ? (
        <EmptyState>{empty}</EmptyState>
      ) : (
        <div className="space-y-2">
          {items.slice(0, 6).map((item) => (
            <div key={item.entity_id} className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium truncate" style={{ color: 'var(--lh-text)' }} title={item.label}>
                    {item.label || item.entity_id}
                  </div>
                  <div className="lh-mono text-[10px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>
                    last {item.last_status || '—'} · {shortWhen(item.last_run_at)}
                  </div>
                </div>
                <div className="flex gap-1 flex-wrap justify-end">
                  <span className="lh-tag">{item.runs} runs</span>
                  <span className="lh-tag">{fmtPct(item.success_rate)}</span>
                  <span className="lh-tag">{fmtDuration(item.avg_duration_ms)}</span>
                  {Number(item.total_cost_usd || 0) > 0 && <span className="lh-tag">{fmtUsd(item.total_cost_usd)}</span>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ExecutionAnalyticsPanel() {
  const [range, setRange] = useState('7d')
  const [summary, setSummary] = useState(null)
  const [flows, setFlows] = useState([])
  const [brains, setBrains] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [summaryRes, flowsRes, brainsRes, runsRes] = await Promise.all([
        fetch(`/api/analytics/summary?range=${encodeURIComponent(range)}`),
        fetch(`/api/analytics/flows?range=${encodeURIComponent(range)}`),
        fetch(`/api/analytics/brains?range=${encodeURIComponent(range)}`),
        fetch('/api/analytics/runs?limit=20'),
      ])
      await requireOk(summaryRes, 'Execution summary')
      await requireOk(flowsRes, 'Flow analytics')
      await requireOk(brainsRes, 'Brain analytics')
      await requireOk(runsRes, 'Run history')
      setSummary(await summaryRes.json())
      setFlows((await flowsRes.json()).items || [])
      setBrains((await brainsRes.json()).items || [])
      setRuns((await runsRes.json()).items || [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load analytics.')
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => {
    load()
  }, [load])

  const activeFlows = useMemo(() => flows.filter((f) => f.runs > 0), [flows])
  const total = summary?.total || {}

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <SectionHeader
          eyebrow="Execution analytics"
          title="Runs, workflows and brains"
          actions={(
            <>
            {['24h', '7d', '30d', 'all'].map((id) => (
              <button
                key={id}
                type="button"
                className="lh-btn"
                onClick={() => setRange(id)}
                style={range === id ? { borderColor: 'var(--lh-gold-deep)', color: 'var(--lh-accent-text)' } : undefined}
                aria-pressed={range === id}
              >
                {id}
              </button>
            ))}
            <button type="button" className="lh-btn" onClick={load}>Refresh</button>
            </>
          )}
        />

        {error ? (
          <ErrorState>{error}</ErrorState>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <MetricCard label="runs" value={total.runs || 0} sub={loading ? 'loading…' : `${range} window`} />
            <MetricCard label="success" value={fmtPct(total.success_rate)} sub={`${total.completed || 0} completed`} />
            <MetricCard label="avg duration" value={fmtDuration(total.avg_duration_ms)} sub={`${fmtDuration(total.total_duration_ms)} total`} />
            <MetricCard label="cost" value={fmtUsd(total.total_cost_usd)} sub="recorded execution cost" />
            <MetricCard label="failures" value={(total.failed || 0) + (total.stopped || 0)} sub={`${total.failed || 0} failed · ${total.stopped || 0} stopped`} />
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Leaderboard title="Top workflows" items={activeFlows} empty="No workflow runs recorded for this range." />
        <Leaderboard title="Top brains" items={brains} empty="No brain task steps recorded for this range." />
      </div>

      <div className="lh-card">
        <div className="lh-card-head">
          <span className="lh-eyebrow">Recent runs</span>
          <span className="lh-tag">{runs.length} rows</span>
        </div>
        {runs.length === 0 ? (
          <EmptyState>No durable runs recorded yet.</EmptyState>
        ) : (
          <div className="space-y-1 max-h-56 overflow-y-auto">
            {runs.map((run) => (
              <div key={run.id} className="lh-row">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex gap-2 items-center">
                      <span className="lh-mono text-[11px]" style={{ color: 'var(--lh-text)' }}>{run.id}</span>
                      <span className="lh-tag">{run.kind}</span>
                      <span className="lh-tag">{run.status}</span>
                    </div>
                    <div className="text-[11px] mt-1 truncate" style={{ color: 'var(--lh-text-muted)' }}>
                      {run.entity_id || '—'} · {shortWhen(run.started_at)}
                    </div>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <span className="lh-tag">{fmtDuration(run.duration_ms)}</span>
                    {Number(run.cost_usd || 0) > 0 && <span className="lh-tag">{fmtUsd(run.cost_usd)}</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
