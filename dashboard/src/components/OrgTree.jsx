// The org hierarchy view: root at top, children beneath, connector lines so the
// owner SEES who commands whom. Pure presentation — it takes the topology nodes
// and renders them recursively; selection + the route-preview highlight are
// driven from BrainsPanel. The root is marked ("commands everyone").

function NodeRow({ node, root, selectedId, routePath, onSelect }) {
  const isRoot = node.id === root
  const inRoute = routePath.includes(node.id)
  const isRouteLeaf = routePath.length > 0 && routePath[routePath.length - 1] === node.id
  const selected = node.id === selectedId

  const limits = node.limits || {}
  const limitKeys = Object.keys(limits)

  return (
    <button
      type="button"
      onClick={() => onSelect(node.id)}
      className="lh-row w-full text-left transition-colors"
      style={{
        borderColor: selected
          ? 'rgba(45, 212, 191, 0.5)'
          : inRoute
            ? 'rgba(251, 191, 36, 0.4)'
            : 'var(--lh-border)',
        background: selected ? 'var(--lh-accent-dim)' : 'var(--lh-surface-2)',
        marginBottom: '0.4rem',
      }}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="lh-mono text-[13px] font-semibold" style={{ color: 'var(--lh-text)' }}>
          {node.id}
        </span>
        {isRoot && (
          <span className="lh-tag lh-tag-accent" title="Commands everyone">★ root</span>
        )}
        {isRouteLeaf && (
          <span
            className="lh-tag"
            style={{ borderColor: 'rgba(251,191,36,0.5)', color: 'var(--lh-warn)' }}
            title="Where this goal would land"
          >
            route target
          </span>
        )}
        {node.is_leaf && !isRoot && (
          <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>leaf</span>
        )}
      </div>

      <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>
        {node.role || 'department'}
        {node.department ? ` · ${node.department}` : ''}
      </div>

      {(node.skills?.length > 0 || limitKeys.length > 0) && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {(node.skills || []).map((s) => (
            <span key={s} className="lh-tag lh-tag-accent">{s}</span>
          ))}
          {limitKeys.map((k) => (
            <span key={k} className="lh-badge" title="limit">
              {k}={formatLimit(limits[k])}
            </span>
          ))}
        </div>
      )}
    </button>
  )
}

function formatLimit(v) {
  if (Array.isArray(v)) return v.join(',')
  if (v === null || v === undefined) return '—'
  return String(v)
}

function Subtree({ id, byId, ...rest }) {
  const node = byId[id]
  if (!node) return null
  const children = node.children || []
  return (
    <li>
      <NodeRow node={node} {...rest} />
      {children.length > 0 && (
        <ul
          className="mt-1 space-y-0"
          style={{
            marginLeft: '0.85rem',
            paddingLeft: '0.85rem',
            borderLeft: '1px solid var(--lh-border-strong)',
          }}
        >
          {children.map((cid) => (
            <Subtree key={cid} id={cid} byId={byId} {...rest} />
          ))}
        </ul>
      )}
    </li>
  )
}

export default function OrgTree({ topology, selectedId, routePath = [], onSelect }) {
  const nodes = topology?.nodes || []
  if (nodes.length === 0) {
    return (
      <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>
        No brains yet. Create the first one to start the org chart.
      </p>
    )
  }
  const byId = {}
  nodes.forEach((n) => { byId[n.id] = n })
  const root = topology.root

  // Anything not reachable from root (defensive — a valid chart is one tree).
  const reachable = new Set()
  const walk = (id) => {
    if (!byId[id] || reachable.has(id)) return
    reachable.add(id)
    ;(byId[id].children || []).forEach(walk)
  }
  walk(root)
  const orphans = nodes.filter((n) => !reachable.has(n.id))

  const rowProps = { root, selectedId, routePath, onSelect }

  return (
    <div>
      <ul className="space-y-0">
        <Subtree id={root} byId={byId} {...rowProps} />
      </ul>
      {orphans.length > 0 && (
        <div className="mt-3">
          <div className="lh-label mb-1">standalone</div>
          <ul className="space-y-0">
            {orphans.map((n) => (
              <li key={n.id}><NodeRow node={n} {...rowProps} /></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
