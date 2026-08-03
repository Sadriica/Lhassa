function Metric({ label, value, color = 'var(--lh-accent)' }) {
  return (
    <div className="rounded-lg p-3 text-center" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="text-2xl font-bold" style={{ color }}>{value ?? 0}</div>
      <div className="text-xs mt-1" style={{ color: 'var(--lh-text-muted)' }}>{label}</div>
    </div>
  )
}

export default function MetricsPanel({ metrics }) {
  return (
    <div className="lh-card">
      <h2 className="lh-card-title mb-3">Metrics</h2>
      <div className="grid grid-cols-2 gap-2">
        <Metric label="LLM calls" value={metrics.llm_calls} color="var(--lh-accent)" />
        <Metric label="Tool calls" value={metrics.tool_calls} color="var(--lh-accent)" />
        <Metric label="Agents" value={metrics.agents_launched} color="var(--lh-ok)" />
        <Metric label="Sessions" value={metrics.sessions} color="var(--lh-accent)" />
        <Metric label="Blocked" value={metrics.blocked_actions} color="var(--lh-alert)" />
      </div>
    </div>
  )
}
