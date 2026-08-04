import { useEffect, useState } from 'react'
import { getFacts } from '../lib/api'

export default function MemoryPanel() {
  const [facts, setFacts] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const load = (first) =>
      getFacts()
        .then((data) => { if (!cancelled) setFacts(Array.isArray(data) ? data : []) })
        .catch(() => {})
        .finally(() => { if (first && !cancelled) setLoading(false) })

    load(true)
    // Refresh every 60s
    const t = setInterval(() => load(false), 60000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  return (
    <div className="lh-card">
      <h2 className="lh-card-title mb-3">
        Memory — Facts
      </h2>

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : facts.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>No facts stored yet.</p>
      ) : (
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {facts.map((f, i) => (
            <div key={i} className="flex gap-2 text-xs">
              <span className="shrink-0 font-medium" style={{ color: 'var(--lh-accent-text)' }}>{f.key}:</span>
              <span style={{ color: 'var(--lh-text-muted)' }}>{f.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
