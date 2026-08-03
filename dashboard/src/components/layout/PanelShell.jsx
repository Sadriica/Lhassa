import StatusBar from '../StatusBar'
import NavRail from './NavRail'
import MobileNav from './MobileNav'

/**
 * PanelShell — the app chrome + responsive layout frame. Renders the
 * `lh-shell`/`lh-app-chrome` wrapper and the `flex` sidebar+main layout,
 * composing the StatusBar, the MobileNav top bar/scrim, the NavRail sidebar, and
 * the `{children}` main content. App-level overlays (ToastHost, CommandPalette)
 * are passed through as `overlays` so they stay inside `lh-shell` (identical DOM)
 * while App still owns/mounts them.
 *
 * Props:
 *   connected, voiceActive : StatusBar state.
 *   sections               : [{ id, label }]  — nav sections.
 *   section                : string           — active section id.
 *   onNavigate             : (id) => void      — select a section.
 *   navOpen                : boolean           — mobile drawer open state.
 *   onOpenNav / onCloseNav : () => void        — drawer open/close.
 *   overlays               : node             — app-level surfaces (fixed).
 *   children               : node             — the active section's content.
 */
export default function PanelShell({
  connected,
  voiceActive,
  sections,
  section,
  onNavigate,
  navOpen,
  onOpenNav,
  onCloseNav,
  overlays,
  children,
}) {
  return (
    <div className="lh-shell" style={{ color: 'var(--lh-text)' }}>
      <div className="lh-app-chrome">
        <StatusBar connected={connected} voiceActive={voiceActive} />

        <MobileNav
          sections={sections}
          section={section}
          navOpen={navOpen}
          onOpen={onOpenNav}
          onClose={onCloseNav}
        />

        <div className="flex flex-col lg:flex-row gap-4">
          {/* Sidebar nav — a static rail ≥1024px; a slide-in drawer below it */}
          <NavRail
            sections={sections}
            section={section}
            onNavigate={onNavigate}
            navOpen={navOpen}
            onClose={onCloseNav}
          />

          {/* Main content — one section's blocks at a time */}
          <main className="flex-1 min-w-0 space-y-4">
            {children}
          </main>
        </div>
      </div>

      {/* App-level usability surfaces — mounted once. */}
      {overlays}
    </div>
  )
}
