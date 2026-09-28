import { useEffect, useState, useRef, useCallback } from 'react'

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`

/**
 * useWebSocketEvents; the single `/ws` connection the dashboard lives on.
 *
 * Owns the live state the socket feeds (connection, voice, metrics, agents,
 * scheduled ticks, audit) and dispatches every inbound event into it. The
 * reconnect shape is deliberate: `handleEvent` is a stable useCallback so
 * `connect` only depends on it, and `connect` reaches the latest connect via a
 * `connectRef` (never itself) so the 3s reconnect loop never re-declares the
 * callback; that is what keeps this hook free of a use-before-declare / an
 * exhaustive-deps lint error. Do not "simplify" the ref away.
 */
export default function useWebSocketEvents() {
  const [connected, setConnected] = useState(false)
  const [voiceActive, setVoiceActive] = useState(false)
  const [metrics, setMetrics] = useState({ llm_calls: 0, tool_calls: 0, sessions: 0, agents_launched: 0 })
  const [agents, setAgents] = useState({}) // run_id → agent data
  const [scheduledTick, setScheduledTick] = useState(0) // incremented when a task fires
  const [auditEvents, setAuditEvents] = useState([])
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)
  const connectRef = useRef(null)

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

  const agentList = Object.values(agents).sort((a, b) => (b.started_at || 0) - (a.started_at || 0))

  return { connected, voiceActive, metrics, agents, agentList, scheduledTick, auditEvents }
}
