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
  const [limits, setLimits] = useState(null)
  const [ccusage, setCcusage] = useState(null)
  const [budgetInput, setBudgetInput] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let mounted = true

    const load = () => {
      fetch('/api/llm/ledger')
        .then(r => r.json())
        .then(data => {
          if (mounted) setLedger(data)
        })
        .catch(() => {})
      fetch('/api/llm/limits')
        .then(r => r.json())
        .then(data => {
          if (mounted) setLimits(data)
        })
        .catch(() => {})
      fetch('/api/llm/ccusage')
        .then(r => r.json())
        .then(data => {
          if (mounted) setCcusage(data)
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

  const postLimits = (body) => {
    setSaving(true)
    fetch('/api/llm/limits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(data => {
        setLimits(data)
        setBudgetInput('')
      })
      .catch(() => {})
      .finally(() => setSaving(false))
  }

  const killOn = !!limits?.kill_switch

  const saveBudget = () => {
    const n = parseInt(budgetInput, 10)
    if (!Number.isFinite(n) || n < 0) return
    postLimits({ daily_token_budget: n })
  }

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
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-slate-500">Cost limits</span>
          {killOn && <span className="text-xs text-rose-400 font-semibold">KILL-SWITCH ON</span>}
        </div>

        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-xs text-slate-400">Kill-switch (hard stop)</span>
          <button
            type="button"
            disabled={saving || !limits}
            onClick={() => postLimits({ kill_switch: !killOn })}
            className={`text-xs px-2 py-1 rounded font-medium transition-colors disabled:opacity-50 ${
              killOn
                ? 'bg-rose-600 hover:bg-rose-500 text-white'
                : 'bg-slate-700 hover:bg-slate-600 text-slate-200'
            }`}
          >
            {killOn ? 'Engaged — release' : 'Off — engage'}
          </button>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="number"
            min="0"
            value={budgetInput}
            onChange={(e) => setBudgetInput(e.target.value)}
            placeholder={budgetUnlimited ? 'unlimited' : String(dailyBudget)}
            className="flex-1 min-w-0 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 placeholder-slate-600"
          />
          <button
            type="button"
            disabled={saving || budgetInput === ''}
            onClick={saveBudget}
            className="text-xs px-2 py-1 rounded bg-purple-600 hover:bg-purple-500 text-white font-medium disabled:opacity-50"
          >
            Set budget
          </button>
        </div>
        <div className="text-[10px] text-slate-600 mt-1">Daily output-token budget · 0 = unlimited</div>
      </div>

      <div className="mt-3 pt-3 border-t border-slate-800">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-slate-500">Cross-harness (ccusage)</span>
          {ccusage && !ccusage.available && (
            <span className="text-xs text-slate-600">unavailable</span>
          )}
        </div>
        {ccusage && ccusage.available ? (
          <div className="space-y-1">
            {Object.entries(ccusage.by_harness || {}).map(([name, h]) => (
              <div key={name} className="flex items-center justify-between gap-2 text-xs">
                <span className="text-slate-400 truncate" title={name}>{name}</span>
                <span className="text-slate-500 whitespace-nowrap">
                  {fmtTokens(h.tokens)} · {fmtUsd(h.cost_usd)}
                </span>
              </div>
            ))}
            <div className="flex items-center justify-between gap-2 text-xs pt-1 border-t border-slate-800/60">
              <span className="text-slate-500">Total</span>
              <span className="text-emerald-400">{fmtUsd(ccusage.total_cost_usd)}</span>
            </div>
          </div>
        ) : (
          <p className="text-slate-600 text-xs">
            ccusage not detected — install it to track Claude Code / Codex / opencode spend.
          </p>
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
