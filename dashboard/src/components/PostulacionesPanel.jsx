import { useCallback, useEffect, useMemo, useState } from 'react'
import { requireOk } from '../lib/api'
import { SectionHeader, EmptyState, ErrorState } from './ui/common'

// Postulaciones: the owner's tracked internship applications, straight from the
// local `postulaciones` table (same data as the tracking_postulaciones action).
// Editable: status, category and priority change inline (PATCH to the backend)
// and the list reloads itself after each change, so "Last change" updates right
// away. Styled with the house design system (lh-* classes + CSS vars).
//
// The UI is English; the STORED vocabulary stays as the tracking_postulaciones
// action writes it, so the two never split. Every option is {value, label}:
// value is what the table holds, label is what the owner reads.

const ALL = '__all__'
const ESTADOS = [
  { value: ALL, label: 'all' },
  { value: 'sin respuesta', label: 'no response' },
  { value: 'entrevista', label: 'interview' },
  { value: 'oferta', label: 'offer' },
  { value: 'rechazada', label: 'rejected' },
]
const ESTADO_EDIT = [
  { value: 'pendiente', label: 'pending' },
  { value: 'enviada', label: 'sent' },
  { value: 'sin respuesta', label: 'no response' },
  { value: 'entrevista', label: 'interview' },
  { value: 'oferta', label: 'offer' },
  { value: 'rechazada', label: 'rejected' },
]
const CATEGORIAS = [
  { value: 'AI/LLMs', label: 'AI/LLMs' },
  { value: 'HPC/CUDA', label: 'HPC/CUDA' },
  { value: 'investigacion', label: 'research' },
  { value: 'backend', label: 'backend' },
  { value: 'general', label: 'general' },
]
const PRIORIDADES = [
  { value: 'critica', label: 'critical' },
  { value: 'alta', label: 'high' },
  { value: 'media', label: 'medium' },
  { value: 'baja', label: 'low' },
]
const PRIO_COLOR = {
  critica: 'var(--lh-alert)',
  alta: 'var(--lh-warn)',
  media: 'var(--lh-accent-text)',
  baja: 'var(--lh-text-faint)',
}

function labelFor(opts, value) {
  const hit = opts.find((o) => o.value === value)
  return hit ? hit.label : value || ''
}

function fmtFecha(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

// An unknown stored value stays selectable, so editing never silently rewrites it.
function optsWithValue(opts, value) {
  if (value && !opts.some((o) => o.value === value)) return [...opts, { value, label: value }]
  return opts
}

export default function PostulacionesPanel() {
  const [estado, setEstado] = useState(ALL)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState({}) // id -> field being saved

  const load = useCallback(async (est, silent) => {
    try {
      if (!silent) {
        setLoading(true)
        setError('')
      }
      const q = est && est !== ALL ? `?estado=${encodeURIComponent(est)}` : ''
      const resp = await fetch(`/api/postulaciones${q}`)
      await requireOk(resp, 'postulaciones')
      const data = await resp.json()
      setItems(Array.isArray(data?.items) ? data.items : [])
    } catch (e) {
      setError(e.message || 'Could not load applications.')
      setItems([])
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => { load(estado) }, [estado, load])

  const patch = useCallback(async (id, field, value) => {
    setSaving((s) => ({ ...s, [id]: field }))
    try {
      const resp = await fetch(`/api/postulaciones/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      })
      await requireOk(resp, 'postulaciones')
      // Silent reload: the backend bumps actualizado_en, so "Last change"
      // reflects the edit at once without flashing the table.
      await load(estado, true)
    } catch (e) {
      setError(e.message || 'Could not save the change.')
    } finally {
      setSaving((s) => {
        const n = { ...s }
        delete n[id]
        return n
      })
    }
  }, [estado, load])

  const porEstado = useMemo(() => {
    const counts = {}
    for (const it of items) counts[it.estado] = (counts[it.estado] || 0) + 1
    return counts
  }, [items])

  const th = {
    padding: '0.5rem 0.75rem',
    textAlign: 'left',
    fontSize: '0.6875rem',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    color: 'var(--lh-text-muted)',
    fontWeight: 500,
  }
  const td = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem', verticalAlign: 'top' }
  const sel = { fontSize: '0.8125rem' }

  return (
    <div className="space-y-4">
      <SectionHeader
        eyebrow="Practice"
        title="Applications"
        actions={
          <div className="flex flex-wrap gap-1.5">
            {ESTADOS.map((e) => (
              <button
                key={e.value}
                type="button"
                onClick={() => setEstado(e.value)}
                className="lh-btn"
                style={
                  estado === e.value
                    ? { borderColor: 'var(--lh-gold)', color: 'var(--lh-accent-text)' }
                    : undefined
                }
              >
                {e.label}
              </button>
            ))}
            <button type="button" className="lh-btn" onClick={() => load(estado)} title="Reload">
              ↻
            </button>
          </div>
        }
      />

      {error ? (
        <ErrorState>{error}</ErrorState>
      ) : loading ? (
        <p className="text-xs animate-pulse" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      ) : items.length === 0 ? (
        <EmptyState>No applications tracked yet.</EmptyState>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <span className="lh-badge">Total: {items.length}</span>
            {Object.entries(porEstado).map(([k, v]) => (
              <span key={k} className="lh-badge">
                {labelFor(ESTADO_EDIT, k)}: {v}
              </span>
            ))}
          </div>

          <div className="lh-card" style={{ padding: 0, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--lh-border)' }}>
                  <th style={th}>Company</th>
                  <th style={th}>Role</th>
                  <th style={th}>Category</th>
                  <th style={th}>Priority</th>
                  <th style={th}>Status</th>
                  <th style={th}>Last change</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.id} style={{ borderBottom: '1px solid var(--lh-border)', background: 'transparent' }}>
                    <td style={td}>
                      {it.link ? (
                        <a
                          href={it.link}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: 'var(--lh-accent-text)', fontWeight: 500 }}
                        >
                          {it.empresa}
                        </a>
                      ) : (
                        <span style={{ color: 'var(--lh-text)', fontWeight: 500 }}>{it.empresa}</span>
                      )}
                    </td>
                    <td style={{ ...td, color: 'var(--lh-text)' }}>{it.rol || '-'}</td>
                    <td style={td}>
                      <select
                        className="lh-select"
                        style={sel}
                        value={it.categoria || ''}
                        disabled={!!saving[it.id]}
                        onChange={(e) => patch(it.id, 'categoria', e.target.value)}
                      >
                        <option value="">-</option>
                        {optsWithValue(CATEGORIAS, it.categoria).map((c) => (
                          <option key={c.value} value={c.value}>{c.label}</option>
                        ))}
                      </select>
                    </td>
                    <td style={td}>
                      <select
                        className="lh-select"
                        style={{ ...sel, color: PRIO_COLOR[it.prioridad] || 'var(--lh-text-faint)' }}
                        value={it.prioridad || ''}
                        disabled={!!saving[it.id]}
                        onChange={(e) => patch(it.id, 'prioridad', e.target.value)}
                      >
                        <option value="">-</option>
                        {optsWithValue(PRIORIDADES, it.prioridad).map((p) => (
                          <option key={p.value} value={p.value} style={{ color: PRIO_COLOR[p.value] }}>
                            {p.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td style={td}>
                      <select
                        className="lh-select"
                        style={sel}
                        value={it.estado || 'pendiente'}
                        disabled={!!saving[it.id]}
                        onChange={(e) => patch(it.id, 'estado', e.target.value)}
                      >
                        {optsWithValue(ESTADO_EDIT, it.estado).map((e) => (
                          <option key={e.value} value={e.value}>{e.label}</option>
                        ))}
                      </select>
                    </td>
                    <td className="lh-mono" style={{ ...td, color: 'var(--lh-text-faint)' }}>
                      {saving[it.id] ? '…' : fmtFecha(it.actualizado_en || it.creado_en)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>
            Changes save on selection and the table refreshes automatically.
          </p>
        </>
      )}
    </div>
  )
}
