import { useEffect } from 'react'

/**
 * MobileNav — the < 1024px nav chrome that sits alongside NavRail: the hamburger
 * top bar (with the current section's title) and the scrim behind the open
 * drawer. The drawer itself is the shared `lh-nav` element rendered by NavRail;
 * this component only renders the mobile-only affordances (both are `lg:hidden`
 * via CSS media queries) and owns the Esc-to-close behavior.
 *
 * Props:
 *   sections : [{ id, label }]  — used to resolve the active section's title.
 *   section  : string           — the active section id.
 *   navOpen  : boolean          — drawer open state.
 *   onOpen   : () => void       — open the drawer (hamburger).
 *   onClose  : () => void       — close the drawer (scrim / Esc).
 */
export default function MobileNav({ sections, section, navOpen, onOpen, onClose }) {
  // Esc closes the mobile nav drawer. Selecting a section also closes it (via onNavigate).
  useEffect(() => {
    if (!navOpen) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen, onClose])

  return (
    <>
      {/* Mobile top bar — hamburger + current section (sidebar is a drawer < 1024px) */}
      <div className="lh-topbar lg:hidden">
        <button
          type="button"
          className="lh-topbar-btn"
          onClick={onOpen}
          aria-label="Open navigation"
          aria-expanded={navOpen}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M4 6h16 M4 12h16 M4 18h16" />
          </svg>
        </button>
        <span className="lh-topbar-title">
          {sections.find((s) => s.id === section)?.label || 'Lhassa'}
        </span>
      </div>

      {/* Scrim behind the open drawer (mobile only) */}
      <div
        className={`lh-nav-scrim ${navOpen ? 'open' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />
    </>
  )
}
