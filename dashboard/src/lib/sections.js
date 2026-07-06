// Sidebar sections — the brain's command surfaces. Brains is the primary section.
// The ids are load-bearing: they drive the hash routing (#<id>), localStorage
// persistence, the nav rail, the mobile drawer title, and the command palette.
export const SECTIONS = [
  { id: 'overview', label: 'Overview', group: 'Command' },
  { id: 'inbox', label: 'Inbox', group: 'Command' },
  { id: 'run', label: 'Run task', group: 'Command' },
  { id: 'watchlist', label: 'Watchlist', group: 'Autonomy' },
  { id: 'flows', label: 'Flows', group: 'Autonomy' },
  { id: 'approvals', label: 'Approvals', group: 'Autonomy' },
  { id: 'capabilities', label: 'Capabilities', group: 'Capability' },
  { id: 'tools', label: 'Actions', group: 'Capability' },
  { id: 'skills', label: 'Skills', group: 'Capability' },
  { id: 'brains', label: 'Brains', group: 'Capability' },
  { id: 'fleet', label: 'Readiness', group: 'Operations' },
  { id: 'reports', label: 'Analytics', group: 'Operations' },
  { id: 'activity', label: 'Activity', group: 'Operations' },
  { id: 'settings', label: 'Settings', group: 'System' },
]
