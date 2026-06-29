import { useEffect, useState } from 'react'

function Metric({ label, value, color = 'text-purple-400' }) {
  return (
    <div className="bg-slate-800/50 rounded-lg p-3 text-center">
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
      <div className="text-xs text-slate-500 mt-1">{label}</div>
    </div>
  )
}

function fmtInt(n = 0) {
  return Number(n || 0).toLocaleString()
}

function fmtUsd(n = 0) {
  const v = Number(n || 0)
  if (v > 0 && v < 0.01) return `$${v.toFixed(4)}`
  return `$${v.toFixed(2)}`
}

function fmtTokens(n = 0) {
  const v = Number(n || 0)
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`
  return String(v)
}

export default function AIUsagePanel({ metrics = {} }) {
  const [ledger, setLedger] = useState(null)

  useEffect(() => {
    let mounted = true

    const load = () => {
      fetch('/api/llm/ledger')
        .then(r => r.json())
        .then(data => {
          if (mounted) setLedger(data)
        })
        .catch(() => {})
    }

    load()
    const t = setInterval(load, 10000)
    return () => {
      mounted = false
      clearInterval(t)
    }
  }, [])

  // Headline numbers prefer the live websocket metrics; fall back to ledger totals.
  const totals = ledger?.totals || {}
  const calls = metrics.llm_calls ?? totals.llm_calls ?? 0
  const inputTokens = metrics.llm_input_tokens ?? totals.llm_input_tokens ?? 0
  const outputTokens = metrics.llm_output_tokens ?? totals.llm_output_tokens ?? 0
  const cost = metrics.llm_cost_usd ?? totals.llm_cost_usd ?? 0
  const cacheHits = metrics.llm_cache_hits ?? totals.llm_cache_hits ?? 0

  // Cache-hit rate over total attempts (live calls + cache hits).
  const attempts = Number(calls || 0) + Number(cacheHits || 0)
  const cacheRate = attempts > 0 ? Math.round((Number(cacheHits || 0) / attempts) * 100) : null

  const budget = ledger?.budget || {}
  const dailyBudget = Number(budget.daily_token_budget || 0)
  const usedToday = Number(budget.output_tokens_today || 0)
  const budgetUnlimited = dailyBudget <= 0
  const budgetPct = budgetUnlimited ? 0 : Math.min(100, Math.round((usedToday / dailyBudget) * 100))
  const budgetTone = budgetPct >= 90 ? 'bg-rose-500' : budgetPct >= 60 ? 'bg-amber-500' : 'bg-green-500'

  const recent = ledger?.recent || []

  return (
    <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">AI Usage</h2>
        {ledger && (
          <span className={ledger.cache_enabled ? 'text-xs text-green-400' : 'text-xs text-slate-500'}>
            cache {ledger.cache_enabled ? 'on' : 'off'}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Metric label="Est. cost (USD)" value={fmtUsd(cost)} color="text-emerald-400" />
        <Metric label="LLM calls" value={fmtInt(calls)} color="text-purple-400" />
        <Metric label="Input tokens" value={fmtTokens(inputTokens)} color="text-blue-400" />
        <Metric label="Output tokens" value={fmtTokens(outputTokens)} color="text-cyan-400" />
        <Metric
          label={cacheRate !== null ? `Cache hits (${cacheRate}%)` : 'Cache hits'}
          value={fmtInt(cacheHits)}
          color="text-amber-400"
        />
        <Metric label="Tokens today" value={fmtTokens(usedToday)} color="text-slate-300" />
      </div>

      <div className="mt-3 pt-3 border-t border-slate-800">
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="text-slate-500">Daily budget</span>
          <span className="text-slate-400">
            {budgetUnlimited ? 'unlimited' : `${fmtInt(usedToday)} / ${fmtInt(dailyBudget)} out tok`}
          </span>
        </div>
        {!budgetUnlimited && (
          <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
            <div className={`h-full ${budgetTone} rounded-full`} style={{ width: `${budgetPct}%` }} />
          </div>
        )}
      </div>

      <div className="mt-3 pt-3 border-t border-slate-800">
        <div className="text-xs text-slate-500 mb-2">Recent calls</div>
        {recent.length === 0 ? (
          <p className="text-slate-600 text-xs">No calls recorded yet.</p>
        ) : (
          <div className="space-y-1 max-h-48 overflow-y-auto">
            {recent.map((row) => (
              <div key={row.id} className="flex items-center justify-between gap-2 text-xs">
                <span className="text-slate-400 truncate" title={`${row.provider} / ${row.model}`}>
                  {row.cache_hit ? '⚡ ' : ''}{row.model}
                </span>
                <span className="text-slate-500 whitespace-nowrap">
                  {fmtTokens(row.input_tokens)}/{fmtTokens(row.output_tokens)} · {fmtUsd(row.cost_usd)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
