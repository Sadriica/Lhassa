import { useCallback, useEffect, useState } from 'react'
import {
  getProject,
  getProjectArtifacts,
  getProjectEpisodes,
  getProjectRuns,
  getProjects,
} from '../lib/api'
import { shortWhen } from '../lib/format'
import { EmptyState, ErrorState, SectionHeader } from './ui/common'

// The "Projects" section: the first-class workspace entity (§core.projects).
// Lists projects (GET /api/projects) and opens a detail view for one — its
// scoped runs, delivered artifacts, and recent episodes
// (GET /api/projects/{id}/{runs,artifacts,episodes}). Read-only; creation is a
// CLI/chat act (`lhassa project create`), not a dashboard form.

// A run/task's evidence rung (prepared < observed < verified) computed
// server-side (lhassa/memory/evidence.py) is mirrored here only as a label —
// this panel just reads the `evidence` field runs already carry via the
// analytics seam, or derives nothing itself.
const EVIDENCE_LABEL = {
  prepared: 'prepared',
  observed: 'observed',
  verified: 'verified',
}

function evidenceOf(run) {
  // Same three-rung contract as lhassa/memory/evidence.py, mirrored client-side
  // so the badge does not depend on the API echoing a computed field back.
  const status = String(run.status || '').toLowerCase()
  const prepared = new Set(['queued', 'running', 'pendiente', 'esperando_aprobacion', 'waiting_approval', 'waiting_retry'])
  const observed = new Set(['completed', 'complete', 'finished', 'hecha', 'done', 'failed', 'fallida', 'error', 'cancelled', 'canceled', 'timed_out', 'abandoned'])
  if (prepared.has(status) || !status) return 'prepared'
  if (!observed.has(status)) return 'prepared'
  const verify = run.verify || run.verification || (run.raw && (run.raw.verify || run.raw.verification))
  if (verify && (verify.passed || verify.ok)) return 'verified'
  return 'observed'
}

function ProjectRow({ project, onOpen }) {
  return (
    <button
      type="button"
      className="lh-row text-left w-full"
      style={{ background: 'var(--lh-surface-2)', cursor: 'pointer' }}
      onClick={() => onOpen(project.id)}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-semibold truncate" style={{ color: 'var(--lh-text)' }}>{project.name}</span>
          <span className="lh-badge">{project.kind}</span>
          {project.status !== 'active' && <span className="lh-badge">{project.status}</span>}
        </div>
        <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{project.id}</span>
      </div>
      {project.path && (
        <div className="text-[11px] mt-1 truncate" style={{ color: 'var(--lh-text-muted)' }}>{project.path}</div>
      )}
    </button>
  )
}

function EvidenceBadge({ state }) {
  return <span className="lh-badge" title="evidence">{EVIDENCE_LABEL[state] || state}</span>
}

function ProjectDetail({ project, runs, artifacts, episodes, onBack }) {
  return (
    <div className="space-y-3">
      <div className="lh-card">
        <SectionHeader
          eyebrow="Project"
          title={project.name}
          actions={<button type="button" className="lh-btn" onClick={onBack}>← back</button>}
        />
        <div className="flex gap-1.5 flex-wrap mt-1">
          <span className="lh-badge">{project.kind}</span>
          <span className="lh-badge">{project.status}</span>
          {project.owner_department && <span className="lh-badge">{project.owner_department}</span>}
        </div>
        {project.path && (
          <div className="lh-mono text-[11px] mt-2" style={{ color: 'var(--lh-text-muted)' }}>{project.path}</div>
        )}
      </div>

      <div className="lh-card">
        <SectionHeader eyebrow="Runs" title={`${runs.length} recorded`} />
        {runs.length === 0 ? (
          <EmptyState>No runs recorded for this project yet.</EmptyState>
        ) : (
          <ul className="space-y-1 max-h-64 overflow-y-auto">
            {runs.map((r) => (
              <li key={r.id} className="lh-row">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <span className="text-[12px]" style={{ color: 'var(--lh-text)' }}>{r.kind} · {r.status}</span>
                  <div className="flex items-center gap-1.5">
                    <EvidenceBadge state={evidenceOf(r)} />
                    <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{shortWhen(r.started_at)}</span>
                  </div>
                </div>
                {r.summary && (
                  <div className="text-[11px] mt-0.5 truncate" style={{ color: 'var(--lh-text-muted)' }}>{r.summary}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="lh-card">
        <SectionHeader eyebrow="Artifacts" title={`${artifacts.length} delivered`} />
        {artifacts.length === 0 ? (
          <EmptyState>No artifacts delivered for this project yet.</EmptyState>
        ) : (
          <ul className="space-y-1 max-h-64 overflow-y-auto">
            {artifacts.map((a) => (
              <li key={a.id} className="lh-row">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <span className="text-[12px]" style={{ color: 'var(--lh-text)' }}>{a.kind}</span>
                  <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{shortWhen(a.creado_en)}</span>
                </div>
                {(a.paths || []).length > 0 && (
                  <div className="lh-mono text-[11px] mt-0.5 truncate" style={{ color: 'var(--lh-text-muted)' }}>
                    {a.paths.join(', ')}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="lh-card">
        <SectionHeader eyebrow="Episodes" title={`${episodes.length} recent`} />
        {episodes.length === 0 ? (
          <EmptyState>No episodes mention this project yet.</EmptyState>
        ) : (
          <ul className="space-y-1 max-h-64 overflow-y-auto">
            {episodes.map((e) => (
              <li key={e.id} className="lh-row">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px]" style={{ color: 'var(--lh-text)' }}>{e.decision || e.despertar_por || '—'}</span>
                  <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{shortWhen(e.creado_en)}</span>
                </div>
                {e.resultado && (
                  <div className="text-[11px] mt-0.5 truncate" style={{ color: 'var(--lh-text-muted)' }}>{e.resultado}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default function ProjectsPanel() {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [openId, setOpenId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState('')

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await getProjects(showAll))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load projects.')
    } finally {
      setLoading(false)
    }
  }, [showAll])

  useEffect(() => { loadProjects() }, [loadProjects])

  const openProject = useCallback(async (id) => {
    setOpenId(id)
    setDetail(null)
    setDetailError('')
    try {
      // The project itself must load (its failure is the user's error); the three
      // scoped reads are supporting detail and degrade to empty independently.
      const project = await getProject(id)
      const [runs, artifacts, episodes] = await Promise.all([
        getProjectRuns(id).catch(() => []),
        getProjectArtifacts(id).catch(() => []),
        getProjectEpisodes(id).catch(() => []),
      ])
      setDetail({ project, runs, artifacts, episodes })
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : 'Could not load project detail.')
    }
  }, [])

  if (openId) {
    return (
      <div>
        {detailError && <ErrorState>{detailError}</ErrorState>}
        {!detail && !detailError && (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
        )}
        {detail && (
          <ProjectDetail
            project={detail.project}
            runs={detail.runs}
            artifacts={detail.artifacts}
            episodes={detail.episodes}
            onBack={() => { setOpenId(null); setDetail(null) }}
          />
        )}
      </div>
    )
  }

  return (
    <div className="lh-card">
      <SectionHeader
        eyebrow="Workspaces"
        title="Projects"
        meta={
          <button type="button" className="lh-btn" onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'active only' : 'show archived'}
          </button>
        }
      />
      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : error ? (
        <ErrorState>{error}</ErrorState>
      ) : projects.length === 0 ? (
        <EmptyState>No projects yet — create one with `lhassa project create &lt;name&gt;`.</EmptyState>
      ) : (
        <div className="space-y-1.5">
          {projects.map((p) => (
            <ProjectRow key={p.id} project={p} onOpen={openProject} />
          ))}
        </div>
      )}
    </div>
  )
}
