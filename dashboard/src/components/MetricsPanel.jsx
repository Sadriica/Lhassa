function Metric({ label, value, color = 'text-purple-400' }) {
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 text-center">
      <div className={`text-2xl font-bold ${color}`}>{value ?? 0}</div>
      <div className="text-xs text-slate-500 mt-1">{label}</div>
    </div>
  )
}

export default function MetricsPanel({ metrics }) {
  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
      <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Metrics</h2>
      <div className="grid grid-cols-2 gap-2">
        <Metric label="LLM calls" value={metrics.llm_calls} color="text-purple-400" />
        <Metric label="Tool calls" value={metrics.tool_calls} color="text-blue-400" />
        <Metric label="Agents" value={metrics.agents_launched} color="text-green-400" />
        <Metric label="Sessions" value={metrics.sessions} color="text-amber-400" />
        <Metric label="Blocked" value={metrics.blocked_actions} color="text-rose-400" />
      </div>
    </div>
  )
}
