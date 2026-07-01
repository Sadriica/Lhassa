import { useEffect, useState, useRef, useCallback } from 'react'
import StatusBar from './components/StatusBar'
import OverviewPanel from './components/OverviewPanel'
import BrainsPanel from './components/BrainsPanel'
import SkillsPanel from './components/SkillsPanel'
import AgentsPanel from './components/AgentsPanel'
import AgenticPanel from './components/AgenticPanel'
import WorkflowsPanel from './components/WorkflowsPanel'
import MetricsPanel from './components/MetricsPanel'
import AIUsagePanel from './components/AIUsagePanel'
import MemoryPanel from './components/MemoryPanel'
import HistoryPanel from './components/HistoryPanel'
import ScheduledPanel from './components/ScheduledPanel'
import AuditPanel from './components/AuditPanel'
import ApprovalQueuePanel from './components/ApprovalQueuePanel'
import QuarantinePanel from './components/QuarantinePanel'
import ReportsPanel from './components/ReportsPanel'
import FleetPanel from './components/FleetPanel'
import HealthPanel from './components/HealthPanel'
import SettingsPanel from './components/SettingsPanel'
import ErrorBoundary from './components/ErrorBoundary'
import ToastHost from './components/ToastHost'
import CommandPalette from './components/CommandPalette'
import './index.css'

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`

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

// Sidebar sections — the brain's command surfaces. Brains is the primary section.
const SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'brains', label: 'Brains' },
  { id: 'skills', label: 'Skills' },
  { id: 'flows', label: 'Flows' },
  { id: 'run', label: 'Run' },
  { id: 'approvals', label: 'Approvals' },
  { id: 'tools', label: 'Tools' },
  { id: 'reports', label: 'Reports' },
  { id: 'fleet', label: 'Fleet' },
  { id: 'activity', label: 'Activity' },
  { id: 'settings', label: 'Settings' },
]

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

export default function App() {
  const [section, setSection] = useState(initialSection)
  const [subRoute, setSubRoute] = useState(() => parseHash().subRoute) // optional deep-link tail
  const [navOpen, setNavOpen] = useState(false) // mobile drawer (≥1024px: static sidebar)
  const [connected, setConnected] = useState(false)
  const [voiceActive, setVoiceActive] = useState(false)
  const [metrics, setMetrics] = useState({ llm_calls: 0, tool_calls: 0, sessions: 0, agents_launched: 0 })
  const [agents, setAgents] = useState({}) // run_id → agent data
  const [scheduledTick, setScheduledTick] = useState(0) // incremented when a task fires
  const [auditEvents, setAuditEvents] = useState([])
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)
  const connectRef = useRef(null)
  const hashInitRef = useRef(false) // first hash sync normalizes without a history push

  const handleEvent = useCallback((event) => {
    switch (event.type) {
      case 'snapshot':
        setVoiceActive(event.voice_active)
        setMetrics(event.metrics || {})
        setAuditEvents(event.audit || [])
        if (event.agents) {
          const map = {}
          event.agents.forEach(a => { map[a.id] = a })
          setAgents(map)
        }
        break

      case 'voice_state':
        setVoiceActive(Boolean(event.voice_active))
        break

      case 'audit_event':
        if (event.event) {
          setAuditEvents(prev => [event.event, ...prev].slice(0, 100))
        }
        break

      case 'agent_started':
        setAgents(prev => ({
          ...prev,
          [event.run_id]: { id: event.run_id, name: event.agent, status: 'running', output: '', task: event.task },
        }))
        setMetrics(prev => ({ ...prev, agents_launched: (prev.agents_launched || 0) + 1 }))
        break

      case 'agent_token':
        setAgents(prev => {
          const a = prev[event.run_id]
          if (!a) return prev
          return { ...prev, [event.run_id]: { ...a, output: a.output + event.token } }
        })
        break

      case 'agent_done':
        setAgents(prev => {
          const a = prev[event.run_id]
          if (!a) return prev
          return { ...prev, [event.run_id]: { ...a, status: 'done', output: event.output } }
        })
        break

      case 'agent_error':
        setAgents(prev => {
          const a = prev[event.run_id]
          if (!a) return prev
          return { ...prev, [event.run_id]: { ...a, status: 'error', output: event.error } }
        })
        break

      case 'scheduled_task_fired':
        setScheduledTick(t => t + 1)
        break

      case 'process_done':
      case 'file_event':
      case 'command_done':
        setMetrics(prev => ({ ...prev, tool_calls: (prev.tool_calls || 0) + 1 }))
        break

      default:
        break
    }
  }, [])

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      clearTimeout(reconnectRef.current)
    }

    ws.onclose = () => {
      setConnected(false)
      reconnectRef.current = setTimeout(() => connectRef.current?.(), 3000)
    }

    ws.onmessage = (e) => {
      const event = JSON.parse(e.data)
      handleEvent(event)
    }
  }, [handleEvent])

  useEffect(() => {
    connectRef.current = connect
    connect()
    return () => {
      clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  // Esc closes the mobile nav drawer. Selecting a section also closes it (below).
  useEffect(() => {
    if (!navOpen) return
    const onKey = (e) => { if (e.key === 'Escape') setNavOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOpen])

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
  const goToSection = (id) => { setSection(id); setSubRoute(null); setNavOpen(false) }
  // The Flows panel opens/closes a flow by driving the sub-route (null = list).
  const handleFlowRoute = useCallback((idOrNull) => setSubRoute(idOrNull || null), [])

  const agentList = Object.values(agents).sort((a, b) => (b.started_at || 0) - (a.started_at || 0))

  return (
    <div className="lh-shell" style={{ color: 'var(--lh-text)' }}>
      <div className="lh-app-chrome">
        <StatusBar connected={connected} voiceActive={voiceActive} />

        {/* Mobile top bar — hamburger + current section (sidebar is a drawer < 1024px) */}
        <div className="lh-topbar lg:hidden">
          <button
            type="button"
            className="lh-topbar-btn"
            onClick={() => setNavOpen(true)}
            aria-label="Open navigation"
            aria-expanded={navOpen}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M4 6h16 M4 12h16 M4 18h16" />
            </svg>
          </button>
          <span className="lh-topbar-title">
            {SECTIONS.find((s) => s.id === section)?.label || 'Lhassa'}
          </span>
        </div>

        {/* Scrim behind the open drawer (mobile only) */}
        <div
          className={`lh-nav-scrim ${navOpen ? 'open' : ''}`}
          onClick={() => setNavOpen(false)}
          aria-hidden="true"
        />

        <div className="flex flex-col lg:flex-row gap-4">
          {/* Sidebar nav — a static rail ≥1024px; a slide-in drawer below it */}
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
                  onClick={() => setNavOpen(false)}
                  aria-label="Close navigation"
                >
                  ✕
                </button>
              </div>

              <div className="flex flex-col gap-1">
                {SECTIONS.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => goToSection(s.id)}
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

          {/* Main content — one section's blocks at a time */}
          <main className="flex-1 min-w-0 space-y-4">
            {section === 'overview' && (
              <ErrorBoundary><OverviewPanel /></ErrorBoundary>
            )}

            {section === 'brains' && (
              <ErrorBoundary><BrainsPanel /></ErrorBoundary>
            )}

            {section === 'skills' && (
              <ErrorBoundary><SkillsPanel /></ErrorBoundary>
            )}

            {section === 'flows' && (
              <ErrorBoundary><WorkflowsPanel route={subRoute} onRoute={handleFlowRoute} /></ErrorBoundary>
            )}

            {section === 'run' && (
              <ErrorBoundary><AgenticPanel agents={agents} /></ErrorBoundary>
            )}

            {section === 'approvals' && (
              <ErrorBoundary><ApprovalQueuePanel /></ErrorBoundary>
            )}

            {section === 'tools' && (
              <ErrorBoundary><QuarantinePanel /></ErrorBoundary>
            )}

            {section === 'reports' && (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <ErrorBoundary><ReportsPanel /></ErrorBoundary>
                <div className="space-y-4">
                  <ErrorBoundary><AIUsagePanel metrics={metrics} /></ErrorBoundary>
                  <ErrorBoundary><MetricsPanel metrics={metrics} /></ErrorBoundary>
                </div>
              </div>
            )}

            {section === 'fleet' && (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <ErrorBoundary><FleetPanel /></ErrorBoundary>
                <ErrorBoundary><HealthPanel /></ErrorBoundary>
              </div>
            )}

            {section === 'activity' && (
              <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                <div className="xl:col-span-2 space-y-4">
                  <ErrorBoundary><AgentsPanel agents={agentList} /></ErrorBoundary>
                  <ErrorBoundary><HistoryPanel /></ErrorBoundary>
                </div>
                <div className="space-y-4">
                  <ErrorBoundary><AuditPanel seed={auditEvents} /></ErrorBoundary>
                  <ErrorBoundary><ScheduledPanel refreshTick={scheduledTick} /></ErrorBoundary>
                  <ErrorBoundary><MemoryPanel /></ErrorBoundary>
                </div>
              </div>
            )}

            {section === 'settings' && (
              <ErrorBoundary><SettingsPanel /></ErrorBoundary>
            )}
          </main>
        </div>
      </div>

      {/* App-level usability surfaces — mounted once. */}
      <ToastHost />
      <CommandPalette sections={SECTIONS} onNavigate={goToSection} />
    </div>
  )
}
