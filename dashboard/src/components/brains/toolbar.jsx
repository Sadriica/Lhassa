// ── toolbar / header ─────────────────────────────────────────────────────────
// The panel header: the Brains title + count, the Flow/Tree/Templates view switch,
// Refresh, and + Create brain. Purely presentational; all state lives in the
// composition (BrainsPanel) via the data hook; this just renders + calls back.
export function Toolbar({ nodes, isStandalone, view, setView, onRefresh, onToggleCreate, busy }) {
  return (
    <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
      <div className="flex items-center gap-2">
        <h2 className="lh-card-title">Brains</h2>
        <span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
          {nodes.length} brain{nodes.length === 1 ? '' : 's'}
          {isStandalone ? ' · standalone' : ''}
        </span>
      </div>
      <div className="flex gap-2 items-center">
        <div className="flex gap-1" role="group" aria-label="view">
          <button
            type="button"
            className={`lh-btn ${view === 'flow' ? 'lh-btn-primary' : ''}`}
            onClick={() => setView('flow')}
          >
            Flow
          </button>
          <button
            type="button"
            className={`lh-btn ${view === 'tree' ? 'lh-btn-primary' : ''}`}
            onClick={() => setView('tree')}
          >
            Tree
          </button>
          <button
            type="button"
            className={`lh-btn ${view === 'templates' ? 'lh-btn-primary' : ''}`}
            onClick={() => setView('templates')}
          >
            Templates
          </button>
        </div>
        <button type="button" className="lh-btn" onClick={onRefresh} disabled={busy}>Refresh</button>
        <button type="button" className="lh-btn lh-btn-primary" onClick={onToggleCreate}>
          + Create brain
        </button>
      </div>
    </div>
  )
}

// ── route preview; where would a goal land? ──────────────────────────────────
// The goal input + Preview button + the resolved root→leaf path (last hop warned).
export function RoutePreview({ goal, setGoal, previewRoute, routeErr, route }) {
  return (
    <div className="lh-row mb-3" style={{ background: 'var(--lh-surface-2)' }}>
      <span className="lh-label">route preview; where would a goal land?</span>
      <div className="flex gap-1.5 mt-1.5">
        <input
          className="lh-input" placeholder="e.g. optimize the database query"
          value={goal} onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') previewRoute() }}
        />
        <button type="button" className="lh-btn" onClick={previewRoute} disabled={!goal.trim()}>Preview</button>
      </div>
      {routeErr && <div className="text-[11px] mt-1.5" style={{ color: 'var(--lh-alert)' }}>{routeErr}</div>}
      {route && (
        <div className="text-[12px] mt-2 lh-mono flex items-center flex-wrap gap-1">
          {route.path.map((id, i) => (
            <span key={`${id}-${i}`}>
              <span style={{ color: i === route.path.length - 1 ? 'var(--lh-warn)' : 'var(--lh-accent-text)' }}>{id}</span>
              {i < route.path.length - 1 && <span style={{ color: 'var(--lh-text-faint)' }}> → </span>}
            </span>
          ))}
          <span style={{ color: 'var(--lh-text-faint)' }}> · dept {route.department}</span>
        </div>
      )}
    </div>
  )
}
