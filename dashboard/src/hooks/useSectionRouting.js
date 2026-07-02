import { useEffect, useState, useRef, useCallback } from 'react'
import { SECTIONS } from '../lib/sections'

// Valid section keys + persistence — the active section deep-links via the URL
// hash (#flows, #brains…) and remembers the last one in localStorage.
const SECTION_IDS = new Set(SECTIONS.map((s) => s.id))
const DEFAULT_SECTION = 'overview'
const SECTION_STORAGE_KEY = 'lh-section'

// The hash is `#<section>` with an optional `/<sub-route>` tail so a specific
// flow's editor is deep-linkable (`#flows/<flowId>`). `section` is the part
// before the first `/` (gated on the known section ids); `subRoute` is the
// decoded part after it (or null). Only sections carry a sub-route today.
function parseHash() {
  const raw = window.location.hash.replace(/^#/, '')
  const slash = raw.indexOf('/')
  const rawSection = slash === -1 ? raw : raw.slice(0, slash)
  const rawSub = slash === -1 ? '' : raw.slice(slash + 1)
  const section = SECTION_IDS.has(rawSection) ? rawSection : null
  let subRoute = null
  if (section && rawSub) {
    try { subRoute = decodeURIComponent(rawSub) } catch { subRoute = rawSub }
  }
  return { section, subRoute }
}

function initialSection() {
  const fromHash = parseHash().section
  if (fromHash) return fromHash
  try {
    const stored = localStorage.getItem(SECTION_STORAGE_KEY)
    if (stored && SECTION_IDS.has(stored)) return stored
  } catch {
    // localStorage may be unavailable (private mode) — fall through to default.
  }
  return DEFAULT_SECTION
}

/**
 * useSectionRouting — the active `section` (+ optional `subRoute` deep-link tail)
 * mirrored to the URL hash and localStorage, with Back/Forward support.
 *
 * Returns { section, subRoute, goToSection, onFlowRoute }. `goToSection` switches
 * section and clears the sub-route (so you never land on `#brains/<id>`);
 * `onFlowRoute` drives the sub-route (null = the list). Nav-drawer closing is a
 * separate concern layered on top by the caller.
 */
export default function useSectionRouting() {
  const [section, setSection] = useState(initialSection)
  const [subRoute, setSubRoute] = useState(() => parseHash().subRoute) // optional deep-link tail
  const hashInitRef = useRef(false) // first hash sync normalizes without a history push

  // Section (+ optional sub-route) ↔ URL hash + localStorage. On change, mirror
  // the section — and any `#<section>/<sub>` tail — into the hash (so it's
  // bookmarkable) and remember the SECTION only (never the sub-route). The first
  // sync normalizes the URL without adding a junk history entry; later changes
  // push one so the browser Back/Forward buttons walk between sections *and*
  // between the flow list and an open flow.
  useEffect(() => {
    try { localStorage.setItem(SECTION_STORAGE_KEY, section) } catch { /* ignore */ }
    const target = subRoute ? `${section}/${encodeURIComponent(subRoute)}` : section
    const current = window.location.hash.replace(/^#/, '')
    if (current === target) { hashInitRef.current = true; return }
    if (!hashInitRef.current) {
      hashInitRef.current = true
      window.history.replaceState(null, '', `#${target}`)
    } else {
      window.location.hash = target
    }
  }, [section, subRoute])

  // Back/Forward (and manual hash edits) move between sections AND sub-routes.
  useEffect(() => {
    const onHashChange = () => {
      const { section: s, subRoute: sub } = parseHash()
      if (s) { setSection(s); setSubRoute(sub) }
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  // Switching sections clears the sub-route so you never land on `#brains/<id>`.
  const goToSection = (id) => { setSection(id); setSubRoute(null) }
  // The Flows panel opens/closes a flow by driving the sub-route (null = list).
  const onFlowRoute = useCallback((idOrNull) => setSubRoute(idOrNull || null), [])

  return { section, subRoute, goToSection, onFlowRoute }
}
