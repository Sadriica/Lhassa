import { MarkerType } from '@xyflow/react'

// Per-kind visual config for the typed RELATIONSHIP overlay edges. These edges sit
// ON TOP of the command spine (the solid parent→child "who-commands-whom" tree) and
// must read as clearly DIFFERENT from it: the spine is solid/stone (gold on a route),
// while every overlay edge is dashed + animated + carries its own per-kind color and
// a small kind label. Directed kinds (reads / notifies / reviews) get a single
// end-arrowhead; the symmetric `collaborates` gets an arrowhead on BOTH ends so it
// reads as two-way (peer ↔ peer), never a one-way command.
//
// Colors are picked to stay on the Lhasa palette yet distinct from the gold spine and
// the reserved sovereign red: a cool cyan (reads), gold (notifies — but dashed, so it
// won't be mistaken for the solid route), a muted brick (reviews), and a warm plum
// (collaborates).
export const KIND_STYLE = {
  reads: { color: '#4bb3c9', label: 'reads', dash: '6 4', symmetric: false },
  notifies: { color: '#d9a521', label: 'notifies', dash: '2 5', symmetric: false },
  reviews: { color: '#c8584b', label: 'reviews', dash: '9 4', symmetric: false },
  collaborates: { color: '#9b82c4', label: 'collaborates', dash: '1 6', symmetric: true },
}

// The closed kind order used as a fallback for the legend when the API hasn't loaded.
export const RELATION_KIND_ORDER = ['reads', 'notifies', 'reviews', 'collaborates']

// Style for a kind, with a safe fallback for any unknown (forward-compatible) kind.
export function kindStyle(kind) {
  return (
    KIND_STYLE[kind] || {
      color: 'var(--lh-accent)',
      label: kind,
      dash: '4 4',
      symmetric: false,
    }
  )
}

// Build a React Flow edge for one overlay relationship `{from,to,kind,note}`. The id
// is namespaced (`rel:…`) so it never collides with a command edge (`parent->child`),
// and the index keeps parallel edges between the same pair uniquely keyed.
export function relationshipEdge(rel, i) {
  const s = kindStyle(rel.kind)
  const marker = { type: MarkerType.ArrowClosed, color: s.color, width: 15, height: 15 }
  return {
    id: `rel:${rel.kind}:${rel.from}->${rel.to}:${i}`,
    source: rel.from,
    target: rel.to,
    animated: true,
    zIndex: 5, // ride above the command spine
    data: { overlay: true, kind: rel.kind, note: rel.note || '' },
    label: s.label,
    labelShowBg: true,
    labelBgPadding: [4, 2],
    labelBgBorderRadius: 4,
    labelStyle: { fill: s.color, fontSize: 9, fontWeight: 600, letterSpacing: '0.04em' },
    labelBgStyle: { fill: 'rgba(20,17,11,0.82)', stroke: s.color, strokeWidth: 0.5 },
    style: { stroke: s.color, strokeWidth: 1.6, strokeDasharray: s.dash, opacity: 0.92 },
    markerEnd: marker,
    // symmetric → arrowheads on BOTH ends (two-way), no single command direction.
    ...(s.symmetric ? { markerStart: marker } : {}),
  }
}
