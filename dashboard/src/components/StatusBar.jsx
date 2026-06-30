import { useEffect, useState } from 'react'

// The slim top bar: Lhassa wordmark + a provider/health status pill (pulled from
// the existing /api/health) + live voice and WebSocket pills. A quiet, refined
// chrome strip — warm gradient, mono for the machine-y bits, gold on the mark.
export default function StatusBar({ connected, voiceActive }) {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    let mounted = true
    const load = async () => {
      try {
        const r = await fetch('/api/health')
        if (!r.ok) return
        const d = await r.json()
        if (mounted) setHealth(d)
      } catch {
        /* health is best-effort chrome; ignore */
      }
    }
    load()
    const t = setInterval(load, 15000)
    return () => { mounted = false; clearInterval(t) }
  }, [])

  return (
    <header
      className="flex items-center justify-between px-4 py-2.5 mb-4 rounded-[12px]"
      style={{
        background: 'var(--lh-grad-top)',
        border: '1px solid var(--lh-border)',
        boxShadow: 'var(--lh-shadow-sm)',
      }}
    >
      <div className="flex items-center gap-2.5">
        <span
          className="grid place-items-center rounded-[8px]"
          style={{
            width: 26, height: 26,
            background: 'var(--lh-grad-gold)',
            color: 'var(--lh-accent-on)',
            boxShadow: '0 0 0 1px rgba(162,62,52,0.45), var(--lh-glow-gold)',
            fontWeight: 800, fontSize: 13,
          }}
          aria-hidden="true"
        >
          L
        </span>
        <span
          className="font-bold text-lg tracking-[0.2em]"
          style={{ color: 'var(--lh-accent)' }}
        >
          LHASSA
        </span>
        <span className="lh-eyebrow hidden sm:inline" style={{ marginLeft: 2 }}>
          orchestrator
        </span>
      </div>

      <div className="flex items-center gap-2">
        {health && (
          <span className="lh-pill" title="LLM provider / model">
            <span className="lh-dot lh-dot-accent" />
            <span className="lh-mono">{health.llm_provider || 'provider'}</span>
            {health.model && (
              <span className="lh-mono" style={{ color: 'var(--lh-text-faint)' }}>
                · {health.model}
              </span>
            )}
          </span>
        )}

        <span className="lh-pill">
          <span className={`lh-dot ${voiceActive ? 'lh-dot-ok' : ''}`} />
          {voiceActive ? 'Voice active' : 'Voice idle'}
        </span>

        <span className="lh-pill">
          <span className={`lh-dot ${connected ? 'lh-dot-accent' : 'lh-dot-alert'}`} />
          {connected ? 'Connected' : 'Reconnecting…'}
        </span>
      </div>
    </header>
  )
}
