import { useEffect, useMemo, useState } from 'react'
import { toast } from '../lib/toast'

function Metric({ label, value, color = 'var(--lh-accent)' }) {
  return (
    <div className="rounded-lg p-3 text-center" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="text-2xl font-bold" style={{ color }}>{value}</div>
      <div className="text-xs mt-1" style={{ color: 'var(--lh-text-muted)' }}>{label}</div>
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
  const [callFilter, setCallFilter] = useState('')

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

  const postLimits = (body, label) => {
    setSaving(true)
    fetch('/api/llm/limits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(r => {
        if (!r.ok) throw new Error('Save failed')
        return r.json()
      })
      .then(data => {
        setLimits(data)
        setBudgetInput('')
        if (label) toast.success(label)
      })
      .catch(() => toast.error(label ? `${label}; failed` : 'Could not update limits'))
      .finally(() => setSaving(false))
  }

  const killOn = !!limits?.kill_switch

  const saveBudget = () => {
    const n = parseInt(budgetInput, 10)
    if (!Number.isFinite(n) || n < 0) return
    postLimits({ daily_token_budget: n }, `Daily budget set to ${fmtInt(n)}`)
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
  const budgetTone = budgetPct >= 90 ? 'var(--lh-alert)' : budgetPct >= 60 ? 'var(--lh-accent)' : 'var(--lh-ok)'

  const recent = useMemo(() => ledger?.recent || [], [ledger])

  // Client-side filter over the recent calls ledger (model / provider).
  const filteredRecent = useMemo(() => {
    const q = callFilter.trim().toLowerCase()
    if (!q) return recent
    return recent.filter((row) =>
      [row.model, row.provider].filter(Boolean).some((v) => String(v).toLowerCase().includes(q)),
    )
  }, [recent, callFilter])

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="lh-card-title">AI Usage</h2>
        {ledger && (
          <span className="text-xs" style={{ color: ledger.cache_enabled ? 'var(--lh-ok)' : 'var(--lh-text-muted)' }}>
            cache {ledger.cache_enabled ? 'on' : 'off'}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Metric label="Cost today (USD)" value={fmtUsd(cost)} color="var(--lh-ok)" />
        <Metric label="LLM calls" value={fmtInt(calls)} color="var(--lh-accent)" />
        <Metric label="Input tokens" value={fmtTokens(inputTokens)} color="var(--lh-accent)" />
        <Metric label="Output tokens" value={fmtTokens(outputTokens)} color="var(--lh-accent)" />
        <Metric
          label={cacheRate !== null ? `Cache hits (${cacheRate}%)` : 'Cache hits'}
          value={fmtInt(cacheHits)}
          color="var(--lh-accent)"
        />
        <Metric label="Tokens today" value={fmtTokens(usedToday)} color="var(--lh-text)" />
      </div>

      <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--lh-border)' }}>
        <div className="flex items-center justify-between text-xs mb-1">
          <span style={{ color: 'var(--lh-text-muted)' }}>Daily budget</span>
          <span style={{ color: 'var(--lh-text)' }}>
            {budgetUnlimited ? 'unlimited' : `${fmtInt(usedToday)} / ${fmtInt(dailyBudget)} out tok`}
          </span>
        </div>
        {!budgetUnlimited && (
          <div className="h-1.5 w-full rounded-full overflow-hidden" style={{ background: 'var(--lh-surface-3)' }}>
            <div className="h-full rounded-full" style={{ width: `${budgetPct}%`, background: budgetTone }} />
          </div>
        )}
      </div>

      <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--lh-border)' }}>
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>Cost limits</span>
          {killOn && <span className="text-xs font-semibold" style={{ color: 'var(--lh-alert)' }}>KILL-SWITCH ON</span>}
        </div>

        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>Kill-switch (hard stop)</span>
          <button
            type="button"
            disabled={saving || !limits}
            onClick={() => postLimits({ kill_switch: !killOn }, killOn ? 'Kill-switch released' : 'Kill-switch engaged')}
            className={`lh-btn ${killOn ? 'lh-btn-danger' : ''}`}
          >
            {killOn ? 'Engaged (release)' : 'Off (engage)'}
          </button>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="number"
            min="0"
            value={budgetInput}
            onChange={(e) => setBudgetInput(e.target.value)}
            placeholder={budgetUnlimited ? 'unlimited' : String(dailyBudget)}
            className="lh-input"
            style={{ flex: '1 1 auto', minWidth: 0 }}
          />
          <button
            type="button"
            disabled={saving || budgetInput === ''}
            onClick={saveBudget}
            className="lh-btn lh-btn-primary"
          >
            Set budget
          </button>
        </div>
        <div className="text-[10px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>Daily output-token budget · 0 = unlimited</div>
      </div>

      <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--lh-border)' }}>
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>Cross-harness (ccusage)</span>
          {ccusage && !ccusage.available && (
            <span className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>unavailable</span>
          )}
        </div>
        {ccusage && ccusage.available ? (
          <div className="space-y-1">
            {Object.entries(ccusage.by_harness || {}).map(([name, h]) => (
              <div key={name} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate" style={{ color: 'var(--lh-text-muted)' }} title={name}>{name}</span>
                <span className="whitespace-nowrap" style={{ color: 'var(--lh-text-muted)' }}>
                  {fmtTokens(h.tokens)} · {fmtUsd(h.cost_usd)}
                </span>
              </div>
            ))}
            <div className="flex items-center justify-between gap-2 text-xs pt-1" style={{ borderTop: '1px solid var(--lh-border)' }}>
              <span style={{ color: 'var(--lh-text-muted)' }}>Total</span>
              <span style={{ color: 'var(--lh-ok)' }}>{fmtUsd(ccusage.total_cost_usd)}</span>
            </div>
          </div>
        ) : (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>
            ccusage not detected; install it to track Claude Code / Codex / opencode spend.
          </p>
        )}
      </div>

      <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--lh-border)' }}>
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-xs" style={{ color: 'var(--lh-text-muted)' }}>Recent calls</span>
          {recent.length > 0 && (
            <input
              className="lh-input"
              style={{ width: 150, padding: '0.25rem 0.5rem', fontSize: '0.7rem' }}
              placeholder="filter model…"
              value={callFilter}
              onChange={(e) => setCallFilter(e.target.value)}
            />
          )}
        </div>
        {recent.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No calls recorded yet.</p>
        ) : filteredRecent.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No calls match “{callFilter}”.</p>
        ) : (
          <div className="space-y-1 max-h-48 overflow-y-auto">
            {filteredRecent.map((row) => (
              <div key={row.id} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate" style={{ color: 'var(--lh-text-muted)' }} title={`${row.provider} / ${row.model}`}>
                  {row.cache_hit ? '⚡ ' : ''}{row.model}
                </span>
                <span className="whitespace-nowrap" style={{ color: 'var(--lh-text-muted)' }}>
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
