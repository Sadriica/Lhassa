import { useCallback, useEffect, useState } from 'react'
import { getMemoryEntities, getMemoryGraph, searchEpisodes } from '../lib/api'
import { shortWhen } from '../lib/format'
import { EmptyState, ErrorState, SectionHeader } from './ui/common'

// The "Knowledge" section — three $0 read models that had no surface at all:
//
//   GET /api/memory/entities   every entity the brain has extracted, by mentions
//   GET /api/memory/graph?q=   what it knows about ONE entity (facts, the
//                              episodes that mention it, co-occurring terms,
//                              its time span)
//   GET /api/episodes/search   BM25 FTS5 recall over the whole episode log
//
// SCOPE NOTE, deliberate: the graph is rendered as ranked LISTS (entities,
// facts, co-occurring terms, episodes), not as a node-link diagram. The
// underlying read model is a projection over facts + episodes, and a force
// layout would add a dependency and a lot of pixels without adding a fact the
// lists do not already state. A canvas can be layered on later — the client
// function (`getMemoryGraph`) is the same either way.
//
// All model/episode text renders as plain React children. Never
// dangerouslySetInnerHTML: an episode snippet is untrusted text from the log.

function EntityRow({ entity, onPick, selected }) {
  return (
    <button
      type="button"
      className="lh-row text-left w-full"
      style={{
        background: selected ? 'var(--lh-surface-3, var(--lh-surface-2))' : 'var(--lh-surface-2)',
        cursor: 'pointer',
      }}
      onClick={() => onPick(entity.term)}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] truncate" style={{ color: 'var(--lh-text)' }}>{entity.term}</span>
        <div className="flex items-center gap-1.5">
          <span className="lh-badge">{entity.source}</span>
          <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
            {entity.mentions}×
          </span>
        </div>
      </div>
    </button>
  )
}

function EpisodeRow({ episode }) {
  return (
    <li className="lh-row">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-[11px]" style={{ color: 'var(--lh-text)' }}>
          {episode.accion || episode.decision || episode.despertar_por || '—'}
        </span>
        <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
          {shortWhen(episode.creado_en)}
        </span>
      </div>
      {episode.snippet && (
        <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>{episode.snippet}</div>
      )}
    </li>
  )
}

export default function KnowledgePanel() {
  const [entities, setEntities] = useState([])
  const [entitiesError, setEntitiesError] = useState('')
  const [query, setQuery] = useState('')
  const [term, setTerm] = useState('')
  const [graph, setGraph] = useState(null)
  const [recall, setRecall] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    getMemoryEntities()
      .then((data) => { if (!cancelled) setEntities(data?.entities || []) })
      .catch((e) => {
        if (!cancelled) setEntitiesError(e instanceof Error ? e.message : 'Could not load entities.')
      })
    return () => { cancelled = true }
  }, [])

  // One query drives BOTH reads: "what do I know about X" and "what did I do
  // about X" — the same pairing the TUI's Memory screen makes.
  const ask = useCallback(async (value) => {
    const q = (value ?? '').trim()
    if (!q) return
    setTerm(q)
    setBusy(true)
    setError('')
    try {
      const [g, r] = await Promise.all([getMemoryGraph(q), searchEpisodes(q, 25)])
      setGraph(g)
      setRecall(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The query failed.')
    } finally {
      setBusy(false)
    }
  }, [])

  const pick = useCallback((value) => { setQuery(value); ask(value) }, [ask])

  const needle = query.trim().toLowerCase()
  const visibleEntities = needle
    ? entities.filter((e) => String(e.term).toLowerCase().includes(needle))
    : entities

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <SectionHeader eyebrow="Memory" title="Knowledge" />
        <form
          className="flex gap-2"
          onSubmit={(e) => { e.preventDefault(); ask(query) }}
        >
          <input
            className="lh-input flex-1 text-xs"
            placeholder="an entity, a person, a project… (filters the list, and asks on enter)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className="lh-btn" disabled={busy || !query.trim()}>
            {busy ? 'asking…' : 'what do I know?'}
          </button>
        </form>
        {error && <div className="mt-2"><ErrorState>{error}</ErrorState></div>}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="lh-card">
          <SectionHeader eyebrow="Extracted" title={`${visibleEntities.length} entities`} />
          {entitiesError ? (
            <ErrorState>{entitiesError}</ErrorState>
          ) : visibleEntities.length === 0 ? (
            <EmptyState>No entities extracted yet — the brain needs some history first.</EmptyState>
          ) : (
            <div className="space-y-1 max-h-[60vh] overflow-y-auto">
              {visibleEntities.map((e) => (
                <EntityRow key={e.term} entity={e} onPick={pick} selected={e.term === term} />
              ))}
            </div>
          )}
        </div>

        <div className="lh-card xl:col-span-2">
          {!graph ? (
            <>
              <SectionHeader eyebrow="Graph" title="Nothing asked yet" />
              <EmptyState>Pick an entity, or type a query and press enter.</EmptyState>
            </>
          ) : (
            <>
              <SectionHeader
                eyebrow="Graph"
                title={graph.query || term}
                meta={
                  <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
                    {graph.first_seen ? `${shortWhen(graph.first_seen)} → ${shortWhen(graph.last_seen)}` : 'no time span'}
                  </span>
                }
              />
              <div className="mt-1">
                <div className="lh-label">co-occurring terms</div>
                {(graph.related_terms || []).length === 0 ? (
                  <EmptyState>Nothing co-occurs with this yet.</EmptyState>
                ) : (
                  <div className="flex gap-1 flex-wrap mt-1">
                    {graph.related_terms.map((t) => (
                      <button key={t} type="button" className="lh-badge" onClick={() => pick(t)}>
                        {t}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="mt-3">
                <div className="lh-label">facts ({(graph.facts || []).length})</div>
                {(graph.facts || []).length === 0 ? (
                  <EmptyState>No stored fact mentions this.</EmptyState>
                ) : (
                  <ul className="space-y-1 mt-1 max-h-52 overflow-y-auto">
                    {graph.facts.map((f) => (
                      <li key={f.id} className="lh-row">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[11px]" style={{ color: 'var(--lh-text)' }}>{f.contenido}</span>
                          <span className="lh-badge">{f.tipo}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="mt-3">
                <div className="lh-label">
                  episodic recall ({recall?.count ?? (graph.episodes || []).length})
                </div>
                {((recall?.results || graph.episodes || []).length === 0) ? (
                  <EmptyState>No episode mentions this.</EmptyState>
                ) : (
                  <ul className="space-y-1 mt-1 max-h-64 overflow-y-auto">
                    {(recall?.results || graph.episodes).map((e) => (
                      <EpisodeRow key={e.id} episode={e} />
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
