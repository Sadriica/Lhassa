import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import OrgTree from './OrgTree'
import TemplatesPicker from './TemplatesPicker'
import { Toolbar, RoutePreview } from './brains/toolbar'
import { CreateForm } from './brains/createForm'
import { Inspector, InspectorPopup } from './brains/inspector'
import { useBrainsData } from './brains/useBrainsData'
import { fmtDuration, fmtPct } from '../lib/format'
import { requireOk } from '../lib/api'
import { EmptyState, ErrorState, SectionHeader } from './ui/common'

// #12 — code-split the heavy React-Flow canvas out of the initial bundle. OrgFlow
// (and all of @xyflow/react) only loads when the Flow view is actually rendered.
const OrgFlow = lazy(() => import('./OrgFlow'))

function BrainPerformancePanel() {
  const [items, setItems] = useState([])
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/analytics/brains?range=30d')
      await requireOk(r, 'Brain analytics')
      const d = await r.json()
      setItems((d.items || []).filter((item) => item.runs > 0).slice(0, 6))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Brain analytics unavailable.')
    }
  }, [])

  useEffect(() => {
    const t = setTimeout(load, 0)
    return () => clearTimeout(t)
  }, [load])

  return (
    <div className="lh-card">
      <SectionHeader
        eyebrow="Brain performance"
        title="Usage, speed and reliability"
        actions={<button type="button" className="lh-btn" onClick={load}>Refresh</button>}
      />
      {error ? (
        <ErrorState>{error}</ErrorState>
      ) : items.length === 0 ? (
        <EmptyState>
          No brain task runs recorded yet. Flow task nodes will populate this once they execute.
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
          {items.map((item) => (
            <div key={item.entity_id} className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
              <div className="text-[13px] font-medium truncate" style={{ color: 'var(--lh-text)' }} title={item.label}>
                {item.label || item.entity_id}
              </div>
              <div className="flex gap-1 flex-wrap mt-2">
                <span className="lh-tag">{item.runs} runs</span>
                <span className="lh-tag">{fmtPct(item.success_rate)} success</span>
                <span className="lh-tag">avg {fmtDuration(item.avg_duration_ms)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// The Brains management panel — the centerpiece. It is a clean composition: the
// data layer lives in useBrainsData (topology/overlay/selection state + every
// fetch/mutate/optimistic-undo function), and the UI is the Toolbar + view switch
// (OrgFlow / OrgTree / TemplatesPicker) + the dismissible Inspector popup.

// ── panel ───────────────────────────────────────────────────────────────────
export default function BrainsPanel() {
  const {
    topology, setTopology,
    skills,
    skillNotice,
    loading, error, busy,
    selectedId, setSelectedId,
    showCreate, setShowCreate,
    goal, setGoal, route, routeErr,
    view, setView,
    overlay, relKinds,
    installedSet,
    loadTopology,
    installSkill,
    createBrain, patchBrain,
    removeSkillFromBrain,
    moveBrain, promoteBrain,
    addSkillToBrain,
    deleteBrain,
    addRelationship, removeRelationship,
    previewRoute,
    nodes, allIds, departments, selected, routePath, isStandalone,
  } = useBrainsData()

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <Toolbar
          nodes={nodes} isStandalone={isStandalone} view={view} setView={setView}
          onRefresh={loadTopology} onToggleCreate={() => setShowCreate((v) => !v)} busy={busy}
        />

        {/* Route preview */}
        <RoutePreview
          goal={goal} setGoal={setGoal} previewRoute={previewRoute} routeErr={routeErr} route={route}
        />

        {showCreate && (
          <div className="mb-3">
            <CreateForm
              allIds={allIds} departments={departments}
              defaultParent={selectedId || topology?.admin || topology?.root} skills={skills}
              onCreate={createBrain} onClose={() => setShowCreate(false)} busy={busy}
            />
          </div>
        )}

        {error && <ErrorState>{error}</ErrorState>}

        {loading ? (
          <EmptyState>Loading the org chart…</EmptyState>
        ) : view === 'templates' ? (
          <TemplatesPicker
            onApplied={(chart) => {
              if (chart && chart.nodes) setTopology(chart)
              setView('flow')
            }}
          />
        ) : (
          <div>
            <div className="lh-label mb-2">
              {view === 'flow'
                ? 'flow — drag a node onto another to re-parent it · click a brain to inspect'
                : 'hierarchy — who commands whom · click a brain to inspect'}
            </div>
            {/* The canvas spans the full panel width; the inspector floats over its
              right edge (desktop) or as a bottom sheet (mobile) when a node is picked. */}
            <div className="lh-canvas-stage">
              {view === 'flow' ? (
                <Suspense
                  fallback={(
                    <div
                      className="lh-flow-canvas"
                      style={{
                        height: 'clamp(440px, 72vh, 880px)',
                        border: '1px solid var(--lh-border)',
                        borderRadius: 'var(--lh-radius)',
                        background: 'var(--lh-bg)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        color: 'var(--lh-text-faint)',
                        fontSize: 12,
                      }}
                    >
                      Loading canvas…
                    </div>
                  )}
                >
                  <OrgFlow
                    topology={topology} selectedId={selectedId} routePath={routePath}
                    relationships={overlay.relationships} kindMeta={relKinds}
                    onSelect={(id) => setSelectedId(id)} onMove={moveBrain}
                    onCreate={() => setShowCreate(true)}
                  />
                </Suspense>
              ) : (
                <OrgTree
                  topology={topology} selectedId={selectedId} routePath={routePath}
                  onSelect={(id) => setSelectedId(id)}
                  onCreate={() => setShowCreate(true)}
                />
              )}

              {/* Inspector — a dismissible popup, only while a brain is selected. */}
              {selected && (
                <InspectorPopup onClose={() => setSelectedId(null)}>
                  <Inspector
                    key={selected.id} node={selected} nodes={nodes} departments={departments}
                    installedSet={installedSet} onInstall={installSkill} onAddSkill={addSkillToBrain}
                    onRemoveSkill={removeSkillFromBrain}
                    onPatch={patchBrain} onMove={moveBrain} onPromote={promoteBrain}
                    onDelete={deleteBrain}
                    relationships={overlay.relationships} relKinds={relKinds}
                    onAddRel={addRelationship} onRemoveRel={removeRelationship}
                    busy={busy}
                  />
                  {skillNotice && (
                    <div className="text-[11px] mt-2 lh-row" style={{ color: 'var(--lh-accent-text)' }}>
                      {skillNotice}
                    </div>
                  )}
                </InspectorPopup>
              )}
            </div>
          </div>
        )}
      </div>
      <BrainPerformancePanel />
    </div>
  )
}
