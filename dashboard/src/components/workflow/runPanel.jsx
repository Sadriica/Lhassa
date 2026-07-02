import { kindMeta } from '../flowNodeKinds'

// ── run-record renderer ─────────────────────────────────────────────────────────
function StepResult({ step }) {
  // A short, kind-aware one-liner of what a step produced.
  if (step.error) return <span style={{ color: 'var(--lh-alert)' }}>{step.error}</span>
  if (step.kind === 'task') {
    const r = step.result
    const summary = r && typeof r === 'object' ? (r.summary || r.output || JSON.stringify(r)) : r
    return (
      <span>
        <span className="lh-mono" style={{ color: 'var(--lh-accent-text)' }}>{step.brain}</span>
        <span style={{ color: 'var(--lh-text-faint)' }}>{step.pinned ? ' · pinned' : ' · auto'}</span>
        {summary ? <span style={{ color: 'var(--lh-text-muted)' }}> — {String(summary).slice(0, 120)}</span> : null}
      </span>
    )
  }
  if (step.kind === 'gate') {
    if (step.needs_approval) return <span style={{ color: 'var(--lh-warn)' }}>parked for approval</span>
    return <span style={{ color: 'var(--lh-text-muted)' }}>{step.gate} · {step.passed ? 'passed' : 'failed'}{step.detail ? ` — ${String(step.detail).slice(0, 100)}` : ''}</span>
  }
  if (step.kind === 'output') return <span style={{ color: 'var(--lh-text-muted)' }}>{step.output_type}{step.target ? ` → ${step.target}` : ''}</span>
  if (step.kind === 'trigger') return <span style={{ color: 'var(--lh-text-muted)' }}>{step.trigger}</span>
  if (step.kind === 'branch') return <span style={{ color: 'var(--lh-text-muted)' }}>chose {step.chosen_edge || '— (no edge matched)'}</span>
  return null
}

export function RunRecord({ run, live }) {
  if (!run) return null
  const ok = run.status === 'completed'
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="lh-label" style={{ margin: 0 }}>run</span>
        <span
          className="lh-tag"
          style={{
            borderColor: ok ? 'rgba(124,154,91,0.5)' : 'rgba(162,62,52,0.5)',
            color: ok ? 'var(--lh-ok)' : 'var(--lh-alert)',
            background: 'transparent',
          }}
        >
          {ok ? '✓ completed' : '✗ failed'}
        </span>
        {!live && (
          <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
            offline / stubbed — set LHASSA_ORG_LIVE for a live run
          </span>
        )}
      </div>
      <div className="space-y-1">
        {(run.steps || []).map((s, i) => (
          <div
            key={i}
            className="lh-row"
            style={{ background: 'var(--lh-surface-2)', display: 'flex', gap: 8, alignItems: 'baseline' }}
          >
            <span style={{ color: s.ok ? 'var(--lh-ok)' : 'var(--lh-alert)', fontSize: 12 }}>{s.ok ? '✓' : '✗'}</span>
            <span className="lh-mono" style={{ fontSize: 11, color: 'var(--lh-text)' }}>{s.node ?? '—'}</span>
            <span
              className="lh-tag"
              style={{
                fontSize: 8,
                textTransform: 'uppercase',
                letterSpacing: '0.1em',
                borderColor: `rgba(${kindMeta(s.kind).rgb},0.5)`,
                color: `rgba(${kindMeta(s.kind).rgb},0.95)`,
                background: 'transparent',
              }}
            >
              {s.kind || '—'}
            </span>
            <span className="text-[11px]" style={{ flex: 1, minWidth: 0 }}><StepResult step={s} /></span>
          </div>
        ))}
      </div>
      <div className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
        <span className="lh-label">output</span>
        <pre className="lh-mono" style={{ fontSize: 11, color: 'var(--lh-text-muted)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
          {typeof run.output === 'string' ? run.output : JSON.stringify(run.output, null, 2)}
        </pre>
      </div>
    </div>
  )
}
