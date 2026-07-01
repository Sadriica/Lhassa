import { useState } from 'react'

const STATUS_STYLES = {
  running: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  done:    'bg-green-500/20 text-green-300 border-green-500/30',
  error:   'bg-red-500/20 text-red-300 border-red-500/30',
}

const AGENT_ICONS = {
  code:     '⚙',
  research: '🔬',
  system:   '🖥',
}

// Extract the base agent type from names like "code (claude)" → "code"
function agentIcon(name) {
  const base = name?.split(' ')[0] ?? ''
  return AGENT_ICONS[base] || '🤖'
}

function AgentCard({ agent }) {
  const [expanded, setExpanded] = useState(agent.status === 'running')
  const isRunning = agent.status === 'running'

  return (
    <div
      className="border rounded-lg overflow-hidden transition-all"
      style={{
        borderColor: isRunning ? 'rgba(217,165,33,0.4)' : 'var(--lh-border)',
        background: isRunning ? 'var(--lh-accent-dim)' : 'var(--lh-surface-2)',
      }}
    >
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left transition-colors"
        onClick={() => setExpanded(e => !e)}
      >
        <div className="flex items-center gap-3">
          <span className="text-lg">{agentIcon(agent.name)}</span>
          <div>
            <span className="text-sm font-medium" style={{ color: 'var(--lh-text)' }}>{agent.name}</span>
            <span className="text-xs ml-2" style={{ color: 'var(--lh-text-muted)' }}>#{agent.id}</span>
          </div>
          {agent.task && (
            <span className="text-xs max-w-xs truncate hidden sm:block" style={{ color: 'var(--lh-text-muted)' }}>
              {agent.task}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded border ${STATUS_STYLES[agent.status] || ''}`}>
            {agent.status}
            {isRunning && <span className="ml-1 animate-pulse">●</span>}
          </span>
          <span className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4">
          {agent.task && (
            <div className="text-xs mb-2 pb-2" style={{ color: 'var(--lh-text-muted)', borderBottom: '1px solid var(--lh-border)' }}>
              Task: {agent.task}
            </div>
          )}
          <pre
            className={`text-xs whitespace-pre-wrap font-mono max-h-64 overflow-y-auto leading-relaxed ${
              isRunning ? 'cursor-blink' : ''
            }`}
            style={{ color: 'var(--lh-text)' }}
          >
            {agent.output || (isRunning ? 'Waiting for output…' : 'No output')}
          </pre>
        </div>
      )}
    </div>
  )
}

export default function AgentsPanel({ agents }) {
  const running = agents.filter(a => a.status === 'running')
  const finished = agents.filter(a => a.status !== 'running')

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="lh-card-title">
          Agents
        </h2>
        <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--lh-text-muted)' }}>
          {running.length > 0 && (
            <span className="animate-pulse" style={{ color: 'var(--lh-accent-text)' }}>
              {running.length} running
            </span>
          )}
          <span>{agents.length} total</span>
        </div>
      </div>

      {agents.length === 0 ? (
        <div className="text-center py-8">
          <p className="text-sm" style={{ color: 'var(--lh-text-muted)' }}>
            No agents launched yet.
          </p>
          <p className="text-xs mt-1" style={{ color: 'var(--lh-text-faint)' }}>
            Tell Lhassa to delegate a task and it will appear here.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {running.map(a => <AgentCard key={a.id} agent={a} />)}
          {finished.map(a => <AgentCard key={a.id} agent={a} />)}
        </div>
      )}
    </div>
  )
}
