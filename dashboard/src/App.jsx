import { useEffect, useState, useRef, useCallback } from 'react'
import StatusBar from './components/StatusBar'
import BrainsPanel from './components/BrainsPanel'
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
import ErrorBoundary from './components/ErrorBoundary'
import './index.css'

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`

// Sidebar sections (Warp/Oz "blocks" navigation). Brains is the primary section.
const SECTIONS = [
  { id: 'brains', label: 'Brains' },
  { id: 'approvals', label: 'Approvals' },
  { id: 'tools', label: 'Tools' },
  { id: 'reports', label: 'Reports' },
  { id: 'fleet', label: 'Fleet' },
  { id: 'activity', label: 'Activity' },
]

export default function App() {
  const [section, setSection] = useState('brains')
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
        {/* Sidebar nav */}
        <nav className="lg:w-48 lg:shrink-0">
          <div className="flex lg:flex-col gap-1 flex-row flex-wrap">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setSection(s.id)}
                className={`lh-nav-item ${section === s.id ? 'active' : ''}`}
              >
                <span>{s.label}</span>
              </button>
            ))}
          </div>
        </nav>

        {/* Main content — one section's blocks at a time */}
        <main className="flex-1 min-w-0 space-y-4">
          {section === 'brains' && (
            <ErrorBoundary><BrainsPanel /></ErrorBoundary>
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
        </main>
      </div>
    </div>
  )
}
