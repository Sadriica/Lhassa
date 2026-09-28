// Right-click "Move under ▸" context menu for the org FLOW canvas. A floating panel
// listing the VALID new parents for a right-clicked node (validity computed by the
// caller via isValidConnection; no cycles, not itself, not its current parent, admin
// anchored). Purely presentational: the menu/selection state, the valid-target
// computation, and dismissal all live in the composition (OrgFlow.jsx); this just
// renders it and calls back `onMove` / `onClose`.
export function ContextMenu({ menu, menuIsAdmin, menuTargets, onMove, onClose }) {
  if (!menu) return null
  return (
    <div
      onClick={(e) => e.stopPropagation()}
      role="menu"
      style={{
        position: 'fixed',
        top: menu.y,
        left: menu.x,
        zIndex: 50,
        minWidth: 190,
        maxHeight: 320,
        overflowY: 'auto',
        background: 'var(--lh-surface)',
        border: '1px solid var(--lh-border)',
        borderRadius: 'var(--lh-radius)',
        boxShadow: 'var(--lh-shadow)',
        padding: '0.35rem',
      }}
    >
      <div
        style={{
          fontSize: 9,
          fontWeight: 600,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          color: 'var(--lh-text-muted)',
          padding: '0.15rem 0.4rem 0.35rem',
        }}
      >
        <span className="lh-mono" style={{ textTransform: 'none', letterSpacing: 0, color: 'var(--lh-text)' }}>
          {menu.nodeId}
        </span>
        {' · move under ▸'}
      </div>
      {menuIsAdmin ? (
        <div style={{ fontSize: 11, color: 'var(--lh-text-faint)', padding: '0.3rem 0.4rem' }}>
          Lhassa is anchored at the root; it can&apos;t be moved.
        </div>
      ) : menuTargets.length === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--lh-text-faint)', padding: '0.3rem 0.4rem' }}>
          No valid parent to move under.
        </div>
      ) : (
        menuTargets.map((pid) => (
          <button
            key={pid}
            type="button"
            role="menuitem"
            className="lh-mono"
            onClick={() => { onMove(menu.nodeId, pid); onClose() }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--lh-bg-2)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
            style={{
              display: 'block',
              width: '100%',
              textAlign: 'left',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--lh-text)',
              fontSize: 12,
              padding: '0.3rem 0.4rem',
              borderRadius: 6,
            }}
          >
            {pid}
          </button>
        ))
      )}
    </div>
  )
}
