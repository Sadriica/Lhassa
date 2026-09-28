import { useCallback, useEffect, useMemo, useState } from 'react'
import { shortWhen } from '../lib/format'
import { getReports, searchEpisodes } from '../lib/api'
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
  // Deep recall: the filters above only see the loaded window. This asks the
  // server's FTS5 index (GET /api/episodes/search) about the WHOLE log.
  const [recall, setRecall] = useState(null)
  const [recallBusy, setRecallBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setReports(await getReports(0))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'I could not read the episode log right now.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [load])

  const deepRecall = useCallback(async () => {
    const q = query.trim()
    if (!q) return
    setRecallBusy(true)
    try {
      setRecall(await searchEpisodes(q, 50))
    } catch {
      setRecall({ query: q, count: 0, results: [] })
    } finally {
      setRecallBusy(false)
    }
  }, [query])

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
        eyebrow="Episode trail"
        title="Recent episodes"
        meta={(
          <>
          <span className="lh-tag">{filtered.length} in view</span>
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
            placeholder="Search the trail…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select className="lh-select" value={importance} onChange={(e) => setImportance(e.target.value)}>
            <option value="all">All types</option>
            <option value="important">Needs attention</option>
            <option value="routine">Routine quiet</option>
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

      {query.trim() && (
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <button type="button" className="lh-btn" onClick={deepRecall} disabled={recallBusy}>
            {recallBusy ? 'recalling…' : 'search the whole log'}
          </button>
          <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
            the filters above only see the loaded window; this asks the FTS5 index
          </span>
        </div>
      )}

      {recall && (
        <div className="lh-row mb-3" style={{ background: 'var(--lh-surface-2)' }}>
          <div className="flex items-center justify-between gap-2 mb-1">
            <span className="lh-label" style={{ margin: 0 }}>
              recall “{recall.query}”; {recall.count} match{recall.count === 1 ? '' : 'es'}
            </span>
            <button type="button" className="lh-btn" onClick={() => setRecall(null)}>close</button>
          </div>
          {(recall.results || []).length === 0 ? (
            <EmptyState>Nothing in the whole log matches that.</EmptyState>
          ) : (
            <ul className="space-y-1 max-h-64 overflow-y-auto">
              {recall.results.map((ep) => (
                <li key={ep.id} className="lh-row">
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <span className="text-[11px]" style={{ color: 'var(--lh-text)' }}>
                      {ep.accion || ep.decision || ep.despertar_por || '—'}
                    </span>
                    <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
                      {shortWhen(ep.creado_en)}
                    </span>
                  </div>
                  {ep.snippet && (
                    <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>
                      {ep.snippet}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {loading ? (
        <EmptyState>I am reading the episode trail…</EmptyState>
      ) : error ? (
        <ErrorState>{error}</ErrorState>
      ) : recent.length === 0 ? (
        <EmptyState>I have not recorded an episode yet.</EmptyState>
      ) : filtered.length === 0 ? (
        <EmptyState>I found no episode matching “{query}”.</EmptyState>
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
