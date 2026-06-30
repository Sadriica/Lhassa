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
        {/* Lhassa mark — the same gold stupa used for the favicon / app icon */}
        <svg
          width="26" height="26" viewBox="0 0 48 48"
          className="rounded-[8px]"
          style={{ boxShadow: 'var(--lh-glow-gold)', flexShrink: 0 }}
          aria-hidden="true"
        >
          <rect width="48" height="48" rx="11" fill="#15120d" />
          <rect x="0.6" y="0.6" width="46.8" height="46.8" rx="10.4" fill="none" stroke="#d9a521" strokeOpacity="0.22" />
          <circle cx="24" cy="7.2" r="1.7" fill="#a23e34" />
          <path fill="#d9a521" d="M24 9.5l4.4 5.2h-8.8L24 9.5z" />
          <path fill="#d9a521" d="M18.6 17h10.8l1.7 4.4H16.9L18.6 17z" />
          <path fill="#ece4d3" d="M17.4 23.4h13.2l2.1 5.3H15.3l2.1-5.3z" />
          <path fill="#d9a521" d="M14.6 30.7h18.8l2.4 8.3H12.2l2.4-8.3z" />
        </svg>
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
