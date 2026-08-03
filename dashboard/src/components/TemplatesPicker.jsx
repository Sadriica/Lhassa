import { useCallback, useEffect, useState } from 'react'

// The flow-templates gallery — named, multi-brain org presets the owner can apply
// in one move.
//   GET  /api/org/templates                  the catalog
//   POST /api/org/templates/{id}/apply       create its brains under Lhassa
// Apply MUTATES the org chart, so we confirm first. The response carries
// { template, created, skipped, chart } — we hand the chart back to the Brains
// panel so the new brains appear in the tree/flow immediately. An unknown id (or
// a bad template) is a 4xx with {detail}, surfaced on the card.

// The multi-level showcase — gets a touch more presence.
const SHOWCASE_ID = 'software-factory'

function TemplateCard({ tpl, onApply, confirming, onConfirm, onCancel, applying, result, errorText }) {
  const showcase = tpl.id === SHOWCASE_ID
  const brainCount = Array.isArray(tpl.brains) ? tpl.brains.length : (tpl.brains || 0)

  return (
    <div
      className="lh-card"
      style={{
        background: 'var(--lh-surface-2)',
        ...(showcase
          ? { borderColor: 'rgba(217,165,33,0.42)' }
          : {}),
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          {showcase && <div className="lh-eyebrow mb-1" style={{ color: 'var(--lh-accent-text)' }}>Showcase</div>}
          <h3 className="lh-mono text-sm font-semibold" style={{ color: 'var(--lh-text)' }}>{tpl.name}</h3>
        </div>
        <span className={`lh-tag ${showcase ? 'lh-tag-accent' : ''}`}>{tpl.category}</span>
      </div>

      <p className="text-[12px] mt-2 leading-snug" style={{ color: 'var(--lh-text-muted)' }}>{tpl.description}</p>

      <div className="flex items-center gap-2 mt-3 flex-wrap">
        <span
          className="lh-badge"
          title="Brains this template ADDS (namespaced under Lhassa) — not the live org total"
        >
          adds {brainCount} brain{brainCount === 1 ? '' : 's'}
        </span>
        {Array.isArray(tpl.brains) && tpl.brains.slice(0, 4).map((b) => (
          <span key={b.id} className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{b.id}</span>
        ))}
        {Array.isArray(tpl.brains) && tpl.brains.length > 4 && (
          <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
            +{tpl.brains.length - 4} more
          </span>
        )}
      </div>

      <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--lh-hairline)' }}>
        {confirming ? (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px]" style={{ color: 'var(--lh-warn)' }}>
              Create {brainCount} brain{brainCount === 1 ? '' : 's'} under Lhassa?
            </span>
            <button type="button" className="lh-btn lh-btn-primary" disabled={applying} onClick={() => onApply(tpl.id)}>
              {applying ? 'Applying…' : 'Confirm apply'}
            </button>
            <button type="button" className="lh-btn" disabled={applying} onClick={onCancel}>Cancel</button>
          </div>
        ) : (
          <button type="button" className={`lh-btn ${showcase ? 'lh-btn-primary' : ''}`} onClick={() => onConfirm(tpl.id)}>
            Apply template
          </button>
        )}
      </div>

      {errorText && (
        <div className="text-[11px] mt-2" style={{ color: 'var(--lh-alert)' }}>{errorText}</div>
      )}
      {result && (
        <div className="lh-row mt-2" style={{ background: 'var(--lh-surface-3)' }}>
          <div className="text-[11px]" style={{ color: 'var(--lh-ok)' }}>
            Created {result.created.length} · skipped {result.skipped.length}
          </div>
          {result.created.length > 0 && (
            <div className="lh-mono text-[10px] mt-1" style={{ color: 'var(--lh-text-muted)' }}>
              + {result.created.join(', ')}
            </div>
          )}
          {result.skipped.length > 0 && (
            <div className="lh-mono text-[10px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>
              already present: {result.skipped.join(', ')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function TemplatesPicker({ onApplied }) {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmId, setConfirmId] = useState(null)
  const [applyingId, setApplyingId] = useState(null)
  const [results, setResults] = useState({}) // id → { created, skipped }
  const [errors, setErrors] = useState({})   // id → detail

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/org/templates')
      if (!r.ok) throw new Error('templates fetch failed')
      const d = await r.json()
      setTemplates(Array.isArray(d) ? d : [])
      setError('')
    } catch {
      setError('Could not load templates.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const apply = useCallback(async (id) => {
    setApplyingId(id)
    setErrors((e) => ({ ...e, [id]: undefined }))
    try {
      const r = await fetch(`/api/org/templates/${encodeURIComponent(id)}/apply`, { method: 'POST' })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) {
        setErrors((e) => ({ ...e, [id]: body.detail || 'Apply failed.' }))
        return
      }
      setResults((res) => ({ ...res, [id]: { created: body.created || [], skipped: body.skipped || [] } }))
      setConfirmId(null)
      // Hand the fresh chart to the Brains panel so the new brains show at once.
      if (body.chart && onApplied) onApplied(body.chart)
    } catch {
      setErrors((e) => ({ ...e, [id]: 'Network error.' }))
    } finally {
      setApplyingId(null)
    }
  }, [onApplied])

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="lh-label">templates — apply a ready-made org in one move</div>
        <button type="button" className="lh-btn" onClick={load}>Refresh</button>
      </div>

      {error && (
        <div className="lh-row mb-3" style={{ borderColor: 'rgba(162,62,52,0.45)', color: 'var(--lh-alert)' }}>{error}</div>
      )}

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading templates…</p>
      ) : templates.length === 0 ? (
        <p className="text-xs lh-row" style={{ color: 'var(--lh-text-faint)' }}>No templates in the catalog.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {templates.map((tpl) => (
            <TemplateCard
              key={tpl.id}
              tpl={tpl}
              confirming={confirmId === tpl.id}
              applying={applyingId === tpl.id}
              result={results[tpl.id]}
              errorText={errors[tpl.id]}
              onConfirm={(id) => { setConfirmId(id); setErrors((e) => ({ ...e, [id]: undefined })) }}
              onCancel={() => setConfirmId(null)}
              onApply={apply}
            />
          ))}
        </div>
      )}
    </div>
  )
}
