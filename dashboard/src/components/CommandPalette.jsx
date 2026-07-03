import { useEffect, useMemo, useRef, useState } from 'react'

/**
 * CommandPalette — a ⌘K / Ctrl+K modal launcher.
 *
 * Props:
 *   sections   : [{ id, label }]  — the nav sections (from App).
 *   onNavigate : (id) => void     — jump to a section (updates the URL hash too).
 *
 * Commands are data-driven: one JUMP command per section, plus a few quick
 * actions that (for now) simply route to the relevant section. Extend by adding
 * to QUICK_ACTIONS or the derived list.
 */

// Quick actions — each just jumps to a section for now. `to` matches a section id.
const QUICK_ACTIONS = [
  { id: 'action:open-inbox', label: 'Review attention queue', hint: 'Inbox', to: 'inbox' },
  { id: 'action:open-watchlist', label: 'Check watchers', hint: 'Watchlist', to: 'watchlist' },
  { id: 'action:open-capabilities', label: 'Check available actions', hint: 'Capabilities', to: 'capabilities' },
  { id: 'action:run-task', label: 'Run a task', hint: 'Run', to: 'run' },
  { id: 'action:new-flow', label: 'Create automation flow', hint: 'Flows', to: 'flows' },
  { id: 'action:new-brain', label: 'Create brain', hint: 'Brains', to: 'brains' },
  { id: 'action:open-settings', label: 'Configure system', hint: 'Settings', to: 'settings' },
]

function isTypingTarget(el) {
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
}

export default function CommandPalette({ sections = [], onNavigate }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const restoreFocusRef = useRef(null)

  // Full, data-driven command set: section jumps + quick actions.
  const commands = useMemo(() => {
    const jumps = sections.map((s) => ({
      id: `jump:${s.id}`,
      label: `Go to ${s.label}`,
      hint: s.group || 'Section',
      to: s.id,
    }))
    return [...jumps, ...QUICK_ACTIONS]
  }, [sections])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return commands
    // Substring match across label + hint (space-separated terms all must hit).
    const terms = q.split(/\s+/)
    return commands.filter((c) => {
      const hay = `${c.label} ${c.hint || ''}`.toLowerCase()
      return terms.every((t) => hay.includes(t))
    })
  }, [commands, query])

  // Open helper — remember focus, reset the query/selection, then open. Doing the
  // resets here (not in an effect) keeps the effect free of cascading setState.
  const openPalette = () => {
    restoreFocusRef.current = document.activeElement
    setQuery('')
    setActive(0)
    setOpen(true)
  }

  // Global ⌘K / Ctrl+K toggle. Works even while typing in a field (only this combo).
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        if (open) setOpen(false)
        else openPalette()
        return
      }
      // Bare Escape closes only when open; otherwise ignore (don't hijack typing).
      if (e.key === 'Escape' && open) {
        setOpen(false)
      }
    }
    // A UI affordance (e.g. the ⌘K hint) can open the palette by dispatching this.
    const onOpenEvent = () => { if (!open) openPalette() }
    window.addEventListener('keydown', onKey)
    window.addEventListener('lh-cmdk-open', onOpenEvent)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('lh-cmdk-open', onOpenEvent)
    }
  }, [open])

  // Focus the input on open; restore focus to the prior element on close.
  useEffect(() => {
    if (open) {
      const id = requestAnimationFrame(() => inputRef.current?.focus())
      return () => cancelAnimationFrame(id)
    }
    const prev = restoreFocusRef.current
    if (prev && typeof prev.focus === 'function' && !isTypingTarget(document.activeElement)) {
      prev.focus()
    }
    return undefined
  }, [open])

  // Clamp the highlight to the current results at render time (no state sync).
  const safeActive = filtered.length ? Math.min(active, filtered.length - 1) : 0

  const run = (cmd) => {
    if (!cmd) return
    setOpen(false)
    onNavigate?.(cmd.to)
  }

  const onInputKey = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => (filtered.length ? (a + 1) % filtered.length : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => (filtered.length ? (a - 1 + filtered.length) % filtered.length : 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      run(filtered[safeActive])
    }
  }

  // Scroll the active row into view on keyboard navigation.
  useEffect(() => {
    if (!open || !listRef.current) return
    const node = listRef.current.querySelector('[data-active="true"]')
    node?.scrollIntoView({ block: 'nearest' })
  }, [safeActive, open])

  if (!open) return null

  return (
    <div className="lh-cmdk-backdrop" onMouseDown={() => setOpen(false)}>
      <div
        className="lh-cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="lh-cmdk-search">
          <svg
            className="lh-cmdk-search-icon"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.2-3.2" />
          </svg>
          <input
            ref={inputRef}
            className="lh-cmdk-input"
            type="text"
            placeholder="Search commands…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setActive(0)
            }}
            onKeyDown={onInputKey}
            aria-label="Search commands"
            aria-controls="lh-cmdk-list"
          />
          <kbd className="lh-cmdk-kbd">Esc</kbd>
        </div>

        <ul className="lh-cmdk-list" id="lh-cmdk-list" role="listbox" ref={listRef}>
          {filtered.length === 0 && (
            <li className="lh-cmdk-empty">No matching commands</li>
          )}
          {filtered.map((c, i) => (
            <li key={c.id} role="option" aria-selected={i === safeActive}>
              <button
                type="button"
                className={`lh-cmdk-item ${i === safeActive ? 'active' : ''}`}
                data-active={i === safeActive}
                onMouseEnter={() => setActive(i)}
                onClick={() => run(c)}
              >
                <span className="lh-cmdk-item-label">{c.label}</span>
                {c.hint && <span className="lh-cmdk-item-hint">{c.hint}</span>}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
