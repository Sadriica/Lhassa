// Minimal stroked line icons for the nav rail (warm, calm, one family).
const Icon = ({ d, fill }) => (
  <svg className="lh-nav-icon" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true">
    {fill ? <path d={d} fill="currentColor" stroke="none" /> : <path d={d} />}
  </svg>
)
const ICONS = {
  overview:  'M4 13h7V4H4v9Z M13 20h7v-9h-7v9Z M13 4v5h7V4h-7Z M4 20h7v-5H4v5Z',
  brains:    'M12 3a4 4 0 0 0-4 4 3 3 0 0 0-1 5.8V17a3 3 0 0 0 5 2 3 3 0 0 0 5-2v-4.2A3 3 0 0 0 16 7a4 4 0 0 0-4-4Z',
  skills:    'M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-4Z',
  flows:     'M5 6a2 2 0 1 1 0-4 2 2 0 0 1 0 4Z M19 13a2 2 0 1 1 0-4 2 2 0 0 1 0 4Z M5 22a2 2 0 1 1 0-4 2 2 0 0 1 0 4Z M7 5h6a4 4 0 0 1 4 4 M7 20h4a4 4 0 0 0 4-4v-2',
  approvals: 'M9 12.5 11 14.5 15.5 10 M12 3 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-3Z',
  tools:     'M14.7 6.3a4 4 0 0 0-5.2 5.2L3 18l3 3 6.5-6.5a4 4 0 0 0 5.2-5.2l-2.6 2.6-2.5-2.5 2.6-2.6Z',
  reports:   'M5 19V5 M5 19h14 M9 16v-5 M13 16V8 M17 16v-3',
  fleet:     'M4 7h16 M4 12h16 M4 17h16 M7 7v10 M13 7v10',
  activity:  'M3 12h4l3 8 4-16 3 8h4',
  run:       'M4 5h16v14H4z M7 9l2.5 2L7 13 M12 14h4',
  settings:  'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z M19.4 13a7.8 7.8 0 0 0 0-2l2-1.5-2-3.5-2.4 1a7.8 7.8 0 0 0-1.7-1l-.3-2.5h-4l-.3 2.5a7.8 7.8 0 0 0-1.7 1l-2.4-1-2 3.5 2 1.5a7.8 7.8 0 0 0 0 2l-2 1.5 2 3.5 2.4-1a7.8 7.8 0 0 0 1.7 1l.3 2.5h4l.3-2.5a7.8 7.8 0 0 0 1.7-1l2.4 1 2-3.5-2-1.5Z',
}

/**
 * NavRail — the sidebar nav. One element that is a static rail ≥1024px and a
 * slide-in drawer below it (the `open` class + the `lh-nav-close` button are the
 * drawer affordances). Driven by the SECTIONS list + the active section.
 *
 * Props:
 *   sections   : [{ id, label }]  — the nav sections.
 *   section    : string           — the active section id.
 *   onNavigate : (id) => void     — select a section (also closes the drawer).
 *   navOpen    : boolean          — drawer open state (mobile only).
 *   onClose    : () => void       — close the drawer.
 */
export default function NavRail({ sections, section, onNavigate, navOpen, onClose }) {
  return (
    <nav className={`lh-nav lg:w-64 lg:shrink-0 ${navOpen ? 'open' : ''}`}>
      <div className="lh-sidebar lh-nav-panel lg:sticky lg:top-4 p-2 rounded-[10px]">
        <div className="lh-brand-plate">
          <div className="lh-brand-mark flex items-center justify-center">
            <svg width="20" height="20" viewBox="0 0 48 48" aria-hidden="true">
              <circle cx="24" cy="7.4" r="1.8" fill="#a23e34" />
              <path fill="#d9a521" d="M24 9.6l4.6 5.4h-9.2L24 9.6Z" />
              <path fill="#e8bf55" d="M18.4 17.4h11.2l2 4.8H16.4l2-4.8Z" />
              <path fill="#ece4d3" d="M16.6 24.4h14.8l2.4 8.2H14.2l2.4-8.2Z" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <div className="lh-brand-name">Lhassa</div>
            <div className="lh-brand-rule" />
          </div>
          {/* Close affordance — only meaningful in the mobile drawer */}
          <button
            type="button"
            className="lh-nav-close lg:hidden"
            onClick={onClose}
            aria-label="Close navigation"
          >
            ✕
          </button>
        </div>

        <div className="flex flex-col gap-1">
          {sections.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onNavigate(s.id)}
              className={`lh-nav-item ${section === s.id ? 'active' : ''}`}
              aria-current={section === s.id ? 'page' : undefined}
            >
              <Icon d={ICONS[s.id]} />
              <span>{s.label}</span>
            </button>
          ))}
        </div>

        {/* Subtle command-palette affordance — opens the ⌘K launcher. */}
        <button
          type="button"
          className="lh-cmdk-hint"
          onClick={() => window.dispatchEvent(new Event('lh-cmdk-open'))}
          aria-label="Open command palette"
        >
          <span>Command palette</span>
          <kbd className="lh-cmdk-kbd">⌘K</kbd>
        </button>
      </div>
    </nav>
  )
}
