// Sidebar sections; the brain's command surfaces. Brains is the primary section.
// The ids are load-bearing: they drive the hash routing (#<id>), localStorage
// persistence, the nav rail, the mobile drawer title, and the command palette.
//
// `pack` (optional) names the capability pack a section needs (the `name` of a
// manifest in `GET /api/packs`). A section with no `pack` is core and always shows.
export const SECTIONS = [
  { id: 'overview', label: 'Overview', group: 'Command' },
  { id: 'inbox', label: 'Inbox', group: 'Command' },
  { id: 'run', label: 'Run task', group: 'Command' },
  { id: 'watchlist', label: 'Watchlist', group: 'Autonomy' },
  { id: 'flows', label: 'Flows', group: 'Autonomy' },
  { id: 'flow-runs', label: 'Flow runs', group: 'Autonomy' },
  { id: 'projects', label: 'Projects', group: 'Autonomy' },
  { id: 'approvals', label: 'Approvals', group: 'Autonomy' },
  { id: 'capabilities', label: 'Capabilities', group: 'Capability' },
  { id: 'tools', label: 'Actions', group: 'Capability' },
  { id: 'skills', label: 'Skills', group: 'Capability' },
  { id: 'brains', label: 'Brains', group: 'Capability' },
  { id: 'knowledge', label: 'Knowledge', group: 'Capability' },
  { id: 'artifacts', label: 'Artifacts', group: 'Operations' },
  { id: 'research', label: 'Research', group: 'Operations', pack: 'research' },
  { id: 'integrity', label: 'Integrity', group: 'Operations' },
  { id: 'fleet', label: 'Readiness', group: 'Operations' },
  { id: 'calendar', label: 'Calendar', group: 'Operations', pack: 'calendar' },
  { id: 'postulaciones', label: 'Postulaciones', group: 'Operations', pack: 'cv' },
  { id: 'reports', label: 'Analytics', group: 'Operations' },
  { id: 'activity', label: 'Activity', group: 'Operations' },
  { id: 'settings', label: 'Settings', group: 'System' },
]

// The sections to show given the loaded packs. `packs` is a Set of pack names, or
// null when unknown (`/api/packs` failed or has not answered yet): then every
// section shows, so a stale backend never hides anything.
export function visibleSections(sections, packs) {
  if (!packs) return sections
  return sections.filter((s) => !s.pack || packs.has(s.pack))
}
