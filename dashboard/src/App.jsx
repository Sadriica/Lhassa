import { useEffect, useState, useRef, useCallback } from 'react'
import StatusBar from './components/StatusBar'
import OverviewPanel from './components/OverviewPanel'
import BrainsPanel from './components/BrainsPanel'
import SkillsPanel from './components/SkillsPanel'
import AgentsPanel from './components/AgentsPanel'
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
  approvals: 'M9 12.5 11 14.5 15.5 10 M12 3 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-3Z',
  tools:     'M14.7 6.3a4 4 0 0 0-5.2 5.2L3 18l3 3 6.5-6.5a4 4 0 0 0 5.2-5.2l-2.6 2.6-2.5-2.5 2.6-2.6Z',
  reports:   'M5 19V5 M5 19h14 M9 16v-5 M13 16V8 M17 16v-3',
  fleet:     'M4 7h16 M4 12h16 M4 17h16 M7 7v10 M13 7v10',
  activity:  'M3 12h4l3 8 4-16 3 8h4',
  settings:  'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z M19.4 13a7.8 7.8 0 0 0 0-2l2-1.5-2-3.5-2.4 1a7.8 7.8 0 0 0-1.7-1l-.3-2.5h-4l-.3 2.5a7.8 7.8 0 0 0-1.7 1l-2.4-1-2 3.5 2 1.5a7.8 7.8 0 0 0 0 2l-2 1.5 2 3.5 2.4-1a7.8 7.8 0 0 0 1.7 1l.3 2.5h4l.3-2.5a7.8 7.8 0 0 0 1.7-1l2.4 1 2-3.5-2-1.5Z',
}

// Sidebar sections — the brain's command surfaces. Brains is the primary section.
const SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'brains', label: 'Brains' },
  { id: 'skills', label: 'Skills' },
  { id: 'approvals', label: 'Approvals' },
  { id: 'tools', label: 'Tools' },
  { id: 'reports', label: 'Reports' },
  { id: 'fleet', label: 'Fleet' },
  { id: 'activity', label: 'Activity' },
  { id: 'settings', label: 'Settings' },
]

export default function App() {
  const [section, setSection] = useState('overview')
  const [connected, setConnected] = useState(false)
  const [voiceActive, setVoiceActive] = useState(false)
  const [metrics, setMetrics] = useState({ llm_calls: 0, tool_calls: 0, sessions: 0, agents_launched: 0 })
  const [agents, setAgents] = useState({}) // run_id → agent data
  const [scheduledTick, setScheduledTick] = useState(0) // incremented when a task fires
  const [auditEvents, setAuditEvents] = useState([])
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)

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
      reconnectRef.current = setTimeout(connect, 3000)
    }

    ws.onmessage = (e) => {
      const event = JSON.parse(e.data)
      handleEvent(event)
    }
  }, [])

  const handleEvent = (event) => {
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
  }

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  const agentList = Object.values(agents).sort((a, b) => (b.started_at || 0) - (a.started_at || 0))

  return (
    <div className="min-h-screen p-4" style={{ background: 'var(--lh-bg)', color: 'var(--lh-text)' }}>
      <StatusBar connected={connected} voiceActive={voiceActive} />

      <div className="flex flex-col lg:flex-row gap-4">
        {/* Sidebar nav — a slim, elegant rail with icon + section label */}
        <nav className="lg:w-52 lg:shrink-0">
          <div
            className="lg:sticky lg:top-4 p-2 rounded-[12px]"
            style={{ background: 'var(--lh-surface)', border: '1px solid var(--lh-border)', boxShadow: 'var(--lh-shadow-sm)' }}
          >
            <div className="lh-eyebrow px-2 pt-1.5 pb-2 hidden lg:block">Sections</div>
            <div className="flex lg:flex-col gap-1 flex-row flex-wrap">
              {SECTIONS.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setSection(s.id)}
                  className={`lh-nav-item ${section === s.id ? 'active' : ''}`}
                  aria-current={section === s.id ? 'page' : undefined}
                >
                  <Icon d={ICONS[s.id]} />
                  <span>{s.label}</span>
                </button>
              ))}
            </div>
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
  )
}
