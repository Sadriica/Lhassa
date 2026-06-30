import { useEffect, useState } from 'react'

export default function MemoryPanel() {
  const [facts, setFacts] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/memory/facts')
      .then(r => r.json())
      .then(data => { setFacts(data); setLoading(false) })
      .catch(() => setLoading(false))

    // Refresh every 60s
    const t = setInterval(() => {
      fetch('/api/memory/facts')
        .then(r => r.json())
        .then(data => setFacts(data))
        .catch(() => {})
    }, 60000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="lh-card">
      <h2 className="lh-card-title mb-3">
        Memory — Facts
      </h2>

      {loading ? (
        <p className="text-slate-600 text-xs">Loading…</p>
      ) : facts.length === 0 ? (
        <p className="text-slate-600 text-xs">No facts stored yet.</p>
      ) : (
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {facts.map((f, i) => (
            <div key={i} className="flex gap-2 text-xs">
              <span className="text-purple-400 shrink-0 font-medium">{f.key}:</span>
              <span className="text-slate-400">{f.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
