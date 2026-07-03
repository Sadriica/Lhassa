import { useEffect, useState } from 'react'

const ENGINE_LABELS = {
  claude: 'Claude CLI',
  codex: 'Codex CLI',
  opencode: 'OpenCode CLI',
  ollama: 'Ollama local',
  groq: 'Groq API',
  anthropic: 'Anthropic API',
  openai: 'OpenAI API',
  openrouter: 'OpenRouter API',
  cerebras: 'Cerebras API',
}

function engineLabel(provider) {
  return ENGINE_LABELS[provider] || provider || 'engine'
}

// The slim top bar: Lhassa wordmark + a provider/health status pill (pulled from
// the existing /api/health) + live voice and WebSocket pills. A quiet, flat chrome
// strip — matte, a hairline bottom divider, mono for the machine-y bits, a small
// gold stupa mark with a monk-red finial.
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
      className="flex items-center justify-between gap-3 px-3 py-3 mb-5 flex-wrap"
      style={{
        background: 'transparent',
        borderBottom: '1px solid var(--lh-hairline)',
      }}
    >
      <div className="flex items-center gap-3">
        {/* Lhassa mark — the gold stupa with a monk-red finial */}
        <svg
          width="32" height="32" viewBox="0 0 48 48"
          className="rounded-[9px]"
          style={{ flexShrink: 0, border: '1px solid rgba(217,165,33,0.24)' }}
          aria-hidden="true"
        >
          <rect width="48" height="48" rx="9" fill="#16140f" />
          <circle cx="24" cy="7.2" r="1.7" fill="#a23e34" />
          <path fill="#d9a521" d="M24 9.5l4.4 5.2h-8.8L24 9.5z" />
          <path fill="#d9a521" d="M18.6 17h10.8l1.7 4.4H16.9L18.6 17z" />
          <path fill="#ece4d3" d="M17.4 23.4h13.2l2.1 5.3H15.3l2.1-5.3z" />
          <path fill="#d9a521" d="M14.6 30.7h18.8l2.4 8.3H12.2l2.4-8.3z" />
        </svg>
        <div>
          <div className="lh-brand-name" style={{ fontSize: '1.2rem' }}>
            Lhassa
          </div>
          <div className="lh-brand-rule" />
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap justify-end">
        {health && (
          <span
            className="lh-pill"
            title="Primary interaction engine from /api/health. Model details live in Settings."
          >
            <span className="lh-dot lh-dot-accent" />
            <span style={{ color: 'var(--lh-text-faint)' }}>Interaction</span>
            <span className="lh-mono">{engineLabel(health.llm_provider)}</span>
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
