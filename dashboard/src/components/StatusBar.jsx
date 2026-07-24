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
          <g transform="translate(3.36,3.36) scale(0.86)">
            <circle cx="24" cy="6" r="1.6" fill="#a23e34" />
            <rect x="23.4" y="8.4" width="1.2" height="3.1" rx="0.6" fill="#d9a521" />
            <path fill="#d9a521" d="M17.6 12.9h12.8l1.3 2.7H16.3l1.3-2.7z" />
            <path fill="#a23e34" d="M18.6 17h10.8l0.9 6.4H17.7L18.6 17z" />
            <path fill="#ece4d3" d="M15.2 24.8h17.6l1.1 5H14.1l1.1-5z" />
            <path fill="#ece4d3" d="M12.4 31.2h23.2l1.3 5.4H11.1l1.3-5.4z" />
            <path fill="#ece4d3" d="M9.6 38h28.8l1.5 5.6H8.1L9.6 38z" />
          </g>
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
