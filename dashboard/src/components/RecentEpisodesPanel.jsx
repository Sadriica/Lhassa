import { useCallback, useEffect, useMemo, useState } from 'react'
import { shortWhen } from '../lib/format'
import { requireOk } from '../lib/api'
import { EmptyState, ErrorState, SectionHeader } from './ui/common'

const IMPORTANT_RE = /(approv|error|fail|denied|blocked|escalat|stopped|flow|agent|graduat|kill.?switch)/i

function isImportant(ep) {
  const hay = `${ep.despertar_por || ''} ${ep.decision || ''} ${ep.resultado || ''}`
  return IMPORTANT_RE.test(hay)
}

export default function RecentEpisodesPanel() {
  const [reports, setReports] = useState(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [importance, setImportance] = useState('all')
  const [trigger, setTrigger] = useState('all')
  const [order, setOrder] = useState('newest')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/reports?n=0')
      await requireOk(r, 'Recent episodes')
      setReports(await r.json())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load episodes.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [load])

  const recent = useMemo(() => reports?.recent || [], [reports])
  const total = Number(reports?.total ?? recent.length)
  const importantCount = useMemo(() => recent.filter(isImportant).length, [recent])
  const triggers = useMemo(() => {
    const names = new Set()
    for (const ep of recent) names.add(ep.despertar_por || 'episode')
    return [...names].sort((a, b) => a.localeCompare(b))
  }, [recent])
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const out = recent.filter((ep) => {
      const important = isImportant(ep)
      if (importance === 'important' && !important) return false
      if (importance === 'routine' && important) return false
      if (trigger !== 'all' && (ep.despertar_por || 'episode') !== trigger) return false
      if (!q) return true
      return [ep.despertar_por, ep.decision, ep.accion, ep.resultado, ep.dominio]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q))
    })
    return [...out].sort((a, b) => {
      const av = Date.parse(a.creado_en || '') || 0
      const bv = Date.parse(b.creado_en || '') || 0
      return order === 'oldest' ? av - bv : bv - av
    })
  }, [recent, query, importance, trigger, order])

  return (
    <div className="lh-card">
      <SectionHeader
        eyebrow="Decision log"
        title="Recent episodes"
        meta={(
          <>
          <span className="lh-tag">{filtered.length} shown</span>
          <span className="lh-tag">{total} total</span>
          <span className="lh-tag">{importantCount} important</span>
          </>
        )}
        actions={(
          <button type="button" className="lh-btn" onClick={load}>Refresh</button>
        )}
      />

      {recent.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-2 mb-3">
          <input
            className="lh-input md:col-span-1"
            placeholder="Filter episodes…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select className="lh-select" value={importance} onChange={(e) => setImportance(e.target.value)}>
            <option value="all">All types</option>
            <option value="important">Important only</option>
            <option value="routine">Routine only</option>
          </select>
          <select className="lh-select" value={trigger} onChange={(e) => setTrigger(e.target.value)}>
            <option value="all">All triggers</option>
            {triggers.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <select className="lh-select" value={order} onChange={(e) => setOrder(e.target.value)}>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
        </div>
      )}

      {loading ? (
        <EmptyState>Loading episodes…</EmptyState>
      ) : error ? (
        <ErrorState>{error}</ErrorState>
      ) : recent.length === 0 ? (
        <EmptyState>No episodes recorded yet.</EmptyState>
      ) : filtered.length === 0 ? (
        <EmptyState>No episodes match “{query}”.</EmptyState>
      ) : (
        <div className="space-y-2 max-h-[70vh] overflow-y-auto">
          {filtered.map((ep) => {
            const important = isImportant(ep)
            return (
              <div
                key={ep.id}
                className="lh-row"
                style={{
                  background: 'var(--lh-surface-2)',
                  ...(important ? { borderColor: 'rgba(217,165,33,0.4)' } : {}),
                }}
              >
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="lh-tag" style={important ? { color: 'var(--lh-accent-text)', borderColor: 'var(--lh-gold-deep)' } : undefined}>
                      {ep.despertar_por || 'episode'}
                    </span>
                    <span className="text-[12px] truncate" style={{ color: 'var(--lh-text)' }}>
                      {ep.decision || ep.accion || '—'}
                    </span>
                  </div>
                  <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
                    {shortWhen(ep.creado_en)}
                  </span>
                </div>
                {ep.resultado && (
                  <div className="text-[11px] mt-1" style={{ color: 'var(--lh-text-muted)' }}>
                    {ep.resultado}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
