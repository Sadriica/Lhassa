import { useCallback, useEffect, useState } from 'react'
import { getArtifact, getArtifacts } from '../lib/api'
import { shortWhen } from '../lib/format'
import { EmptyState, ErrorState, SectionHeader } from './ui/common'

// The "Artifacts" section — the UP-direction of the hierarchy made visible.
// Every result that reaches the owner is registered as an artifact
// (GET /api/artifacts, GET /api/artifacts/{id}); this panel is the read surface
// for that registry. Read-only: an artifact is BORN on the delivery path, never
// created from a dashboard form.

function ArtifactRow({ artifact, onOpen, selected }) {
  const paths = artifact.paths || []
  return (
    <button
      type="button"
      className="lh-row text-left w-full"
      style={{
        background: selected ? 'var(--lh-surface-3, var(--lh-surface-2))' : 'var(--lh-surface-2)',
        cursor: 'pointer',
      }}
      onClick={() => onOpen(artifact.id)}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <span className="lh-badge">{artifact.kind || 'artifact'}</span>
          <span className="text-sm truncate" style={{ color: 'var(--lh-text)' }}>
            {artifact.titulo || artifact.title || paths[0] || artifact.id}
          </span>
        </div>
        <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
          {shortWhen(artifact.creado_en)}
        </span>
      </div>
      {paths.length > 0 && (
        <div className="lh-mono text-[11px] mt-1 truncate" style={{ color: 'var(--lh-text-muted)' }}>
          {paths.join(', ')}
        </div>
      )}
    </button>
  )
}

function ArtifactDetail({ artifact }) {
  const paths = artifact.paths || []
  const metadata = artifact.metadata || {}
  return (
    <div className="lh-card">
      <SectionHeader eyebrow="Artifact" title={artifact.kind || artifact.id} />
      <dl className="text-[11px] space-y-1 mt-1">
        <div className="flex gap-2">
          <dt className="lh-label w-24 shrink-0">id</dt>
          <dd className="lh-mono truncate" style={{ color: 'var(--lh-text-muted)' }}>{artifact.id}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="lh-label w-24 shrink-0">created</dt>
          <dd style={{ color: 'var(--lh-text-muted)' }}>{shortWhen(artifact.creado_en)}</dd>
        </div>
        {artifact.entity_id && (
          <div className="flex gap-2">
            <dt className="lh-label w-24 shrink-0">entity</dt>
            <dd className="lh-mono truncate" style={{ color: 'var(--lh-text-muted)' }}>{artifact.entity_id}</dd>
          </div>
        )}
        {artifact.origin_department && (
          <div className="flex gap-2">
            <dt className="lh-label w-24 shrink-0">from</dt>
            <dd style={{ color: 'var(--lh-text-muted)' }}>{artifact.origin_department}</dd>
          </div>
        )}
      </dl>

      <div className="mt-3">
        <div className="lh-label">paths</div>
        {paths.length === 0 ? (
          <EmptyState>No files recorded on this artifact.</EmptyState>
        ) : (
          <ul className="space-y-1 mt-1">
            {paths.map((p) => (
              <li key={p} className="lh-mono text-[11px] truncate" style={{ color: 'var(--lh-text-muted)' }}>{p}</li>
            ))}
          </ul>
        )}
      </div>

      {Object.keys(metadata).length > 0 && (
        <div className="mt-3">
          <div className="lh-label">metadata</div>
          <ul className="space-y-1 mt-1">
            {Object.entries(metadata).map(([key, value]) => (
              <li key={key} className="text-[11px] flex gap-2">
                <span className="lh-label w-24 shrink-0">{key}</span>
                <span className="truncate" style={{ color: 'var(--lh-text-muted)' }}>
                  {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function ArtifactsPanel() {
  const [artifacts, setArtifacts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('')
  const [openId, setOpenId] = useState(null)
  const [detail, setDetail] = useState(null)

  const load = useCallback(async () => {
    try {
      setArtifacts(await getArtifacts(100))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load artifacts.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const open = useCallback(async (id) => {
    setOpenId(id)
    setDetail(null)
    try {
      setDetail(await getArtifact(id))
    } catch {
      setDetail(null)
    }
  }, [])

  const needle = filter.trim().toLowerCase()
  const visible = needle
    ? artifacts.filter((a) => JSON.stringify(a).toLowerCase().includes(needle))
    : artifacts

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div className="lh-card">
        <SectionHeader
          eyebrow="Delivery"
          title="Artifacts"
          meta={<button type="button" className="lh-btn" onClick={load}>refresh</button>}
        />
        <input
          className="lh-input w-full text-xs mb-2"
          placeholder="filter artifacts…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {loading ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
        ) : error ? (
          <ErrorState>{error}</ErrorState>
        ) : visible.length === 0 ? (
          <EmptyState>
            Nothing delivered yet. Artifacts are registered when a result bubbles up the hierarchy.
          </EmptyState>
        ) : (
          <div className="space-y-1.5 max-h-[70vh] overflow-y-auto">
            {visible.map((a) => (
              <ArtifactRow key={a.id} artifact={a} onOpen={open} selected={a.id === openId} />
            ))}
          </div>
        )}
      </div>

      {detail ? (
        <ArtifactDetail artifact={detail} />
      ) : (
        <div className="lh-card">
          <SectionHeader eyebrow="Artifact" title="Nothing selected" />
          <EmptyState>Pick an artifact on the left to see its files and metadata.</EmptyState>
        </div>
      )}
    </div>
  )
}
