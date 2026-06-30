import { useCallback, useEffect, useMemo, useState } from 'react'

// The Settings panel — the editable runtime allowlist.
//   GET   /api/config            → { settings:[…], groups:[…], available:{…} }
//   PATCH /api/config {key,value} → { results, restart_hint, effective }
// A bad key/value is a 400 with {detail}; we surface it inline near the field and
// never lose the rest of the form. On success we adopt the returned `effective`
// model wholesale (optimistic-but-verified) and flash the restart hint.

// A short pill label per restart class (the PATCH response carries the full hint).
const RESTART_LABEL = {
  none: 'Applied live',
  app: 'Restart app to apply',
  voice: 'Restart voice to apply',
  telegram: 'Restart Telegram to apply',
}

// The group that carries the dangerous toggles (kill-switch) — tinted red.
const DANGER_GROUP = 'Budget & Safety'

// ── a tasteful on-palette toggle ──────────────────────────────────────────────
function Toggle({ on, disabled, onChange, danger }) {
  const accent = danger ? 'var(--lh-red)' : 'var(--lh-gold)'
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onChange(!on)}
      style={{
        position: 'relative',
        width: 40,
        height: 22,
        flexShrink: 0,
        borderRadius: 999,
        border: `1px solid ${on ? accent : 'var(--lh-border-strong)'}`,
        background: on
          ? (danger ? 'rgba(162,62,52,0.30)' : 'var(--lh-accent-dim)')
          : 'var(--lh-bg-2)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background 0.16s ease, border-color 0.16s ease',
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 2,
          left: on ? 19 : 2,
          width: 16,
          height: 16,
          borderRadius: 999,
          background: on ? accent : 'var(--lh-text-faint)',
          boxShadow: on ? '0 0 8px rgba(217,165,33,0.5)' : 'none',
          transition: 'left 0.16s ease, background 0.16s ease',
        }}
      />
    </button>
  )
}

// ── secret control: write-only, never shows a stored value ────────────────────
function SecretControl({ setting, busy, onSave }) {
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const isSet = Boolean(setting.value)

  if (!editing) {
    return (
      <div className="flex items-center gap-2">
        <span className={`lh-tag ${isSet ? 'lh-tag-accent' : ''}`}>{isSet ? 'set' : 'unset'}</span>
        <button type="button" className="lh-btn" disabled={busy} onClick={() => setEditing(true)}>
          {isSet ? 'Replace' : 'Set'}
        </button>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-1.5">
      <input
        className="lh-input"
        type="password"
        autoComplete="off"
        placeholder="enter new value…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        style={{ maxWidth: 220 }}
      />
      <button
        type="button"
        className="lh-btn lh-btn-primary"
        disabled={busy || !draft}
        onClick={() => { onSave(setting.key, draft); setDraft(''); setEditing(false) }}
      >
        Save
      </button>
      <button type="button" className="lh-btn" onClick={() => { setDraft(''); setEditing(false) }}>✕</button>
    </div>
  )
}

// ── one editable setting row ──────────────────────────────────────────────────
function SettingRow({ setting, available, status, busy, onSave }) {
  const { key, type, value, label, options, overridden, secret, restart } = setting
  // Local draft for free-text / numeric controls; committed on blur or Enter.
  const [draft, setDraft] = useState(value ?? '')

  // Enum options: prefer the setting's own list, else the matching available pool.
  const enumOptions = useMemo(() => {
    if (Array.isArray(options) && options.length) return options
    if (key === 'heartbeat_provider') return available.heartbeat_providers || []
    if (key === 'router_mode') return available.router_modes || []
    return []
  }, [options, key, available])

  // Model fields get a datalist of known opencode models (a gentle assist).
  const datalistId = key === 'opencode_model' ? `dl-${key}` : null
  const datalistVals = datalistId ? (available.opencode_models || []) : []

  const dirty = String(draft) !== String(value ?? '')
  const commit = () => { if (dirty) onSave(key, draft) }

  let control
  if (secret) {
    control = <SecretControl setting={setting} busy={busy} onSave={onSave} />
  } else if (type === 'bool') {
    control = (
      <Toggle
        on={Boolean(value)}
        disabled={busy}
        danger={key === 'llm_kill_switch'}
        onChange={(next) => onSave(key, next)}
      />
    )
  } else if (type === 'enum') {
    control = (
      <select
        className="lh-select"
        value={value ?? ''}
        disabled={busy}
        onChange={(e) => onSave(key, e.target.value)}
        style={{ maxWidth: 240 }}
      >
        {enumOptions.map((o) => <option key={o} value={o}>{o}</option>)}
        {!enumOptions.includes(value) && value != null && <option value={value}>{String(value)}</option>}
      </select>
    )
  } else {
    const numeric = type === 'int' || type === 'float'
    control = (
      <>
        <input
          className="lh-input"
          inputMode={numeric ? 'numeric' : undefined}
          list={datalistId || undefined}
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit() } }}
          style={{ maxWidth: 260 }}
        />
        {datalistId && (
          <datalist id={datalistId}>
            {datalistVals.map((v) => <option key={v} value={v} />)}
          </datalist>
        )}
      </>
    )
  }

  const st = status || {}
  const restartLabel = RESTART_LABEL[restart] || 'Applied live'

  return (
    <div className="lh-row">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            {overridden && (
              <span
                className="lh-dot lh-dot-accent"
                title="Overridden — differs from the default"
                style={{ flexShrink: 0 }}
              />
            )}
            <span className="text-[13px] font-medium" style={{ color: 'var(--lh-text)' }}>{label}</span>
          </div>
          <div className="lh-mono text-[10px] mt-0.5" style={{ color: 'var(--lh-text-faint)' }}>
            {key}
            {!secret && (
              <span> · default {String(setting.default)}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">{control}</div>
      </div>

      {st.state === 'saving' && (
        <div className="text-[11px] mt-1.5" style={{ color: 'var(--lh-text-faint)' }}>Saving…</div>
      )}
      {st.state === 'ok' && (
        <div className="mt-1.5">
          <span
            className="lh-pill"
            title={st.hint || ''}
            style={{
              color: restart === 'none' ? 'var(--lh-ok)' : 'var(--lh-warn)',
              borderColor: restart === 'none' ? 'rgba(124,154,91,0.4)' : 'rgba(217,165,33,0.4)',
            }}
          >
            <span className={`lh-dot ${restart === 'none' ? 'lh-dot-ok' : 'lh-dot-warn'}`} />
            {restartLabel}
          </span>
        </div>
      )}
      {st.state === 'err' && (
        <div className="text-[11px] mt-1.5" style={{ color: 'var(--lh-alert)' }}>{st.error}</div>
      )}
    </div>
  )
}

// ── panel ─────────────────────────────────────────────────────────────────────
export default function SettingsPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyKey, setBusyKey] = useState(null)
  const [status, setStatus] = useState({}) // key → { state, error, hint }

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/config')
      if (!r.ok) throw new Error('config fetch failed')
      setData(await r.json())
      setError('')
    } catch {
      setError('Could not load settings.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = useCallback(async (key, value) => {
    setBusyKey(key)
    setStatus((s) => ({ ...s, [key]: { state: 'saving' } }))
    try {
      const r = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) {
        setStatus((s) => ({ ...s, [key]: { state: 'err', error: body.detail || 'Could not apply.' } }))
        return
      }
      // Adopt the verified effective model wholesale.
      if (body.effective) setData(body.effective)
      setStatus((s) => ({ ...s, [key]: { state: 'ok', hint: body.restart_hint } }))
    } catch {
      setStatus((s) => ({ ...s, [key]: { state: 'err', error: 'Network error.' } }))
    } finally {
      setBusyKey(null)
    }
  }, [])

  const groups = data?.groups || []
  const available = data?.available || {}
  const byGroup = useMemo(() => {
    const m = {}
    for (const s of data?.settings || []) (m[s.group] ||= []).push(s)
    return m
  }, [data])

  if (loading) {
    return (
      <div className="lh-card">
        <h2 className="lh-card-title mb-3">Settings</h2>
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading settings…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="lh-card">
        <div className="flex items-center justify-between mb-3">
          <h2 className="lh-card-title">Settings</h2>
          <button type="button" className="lh-btn" onClick={load}>Retry</button>
        </div>
        <div className="lh-row" style={{ borderColor: 'rgba(162,62,52,0.45)', color: 'var(--lh-alert)' }}>{error}</div>
      </div>
    )
  }

  const total = data?.settings?.length || 0

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <h2 className="lh-card-title">Settings</h2>
            <span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
              {total} editable {total === 1 ? 'key' : 'keys'} · writes to settings.env
            </span>
          </div>
          <button type="button" className="lh-btn" onClick={load}>Refresh</button>
        </div>
        <p className="text-[11px] mt-2" style={{ color: 'var(--lh-text-faint)' }}>
          Changes apply live; some surfaces need a restart to take full effect — each row says which.
        </p>
      </div>

      {groups.map((group) => {
        const items = byGroup[group] || []
        if (!items.length) return null
        const danger = group === DANGER_GROUP
        return (
          <div
            key={group}
            className="lh-card"
            style={danger ? { borderColor: 'rgba(162,62,52,0.30)' } : undefined}
          >
            <div className="lh-card-head">
              <div className="flex items-center gap-2">
                <span className="lh-eyebrow" style={danger ? { color: 'var(--lh-red-soft)' } : undefined}>{group}</span>
                {danger && (
                  <span className="lh-tag" style={{ borderColor: 'rgba(162,62,52,0.45)', color: 'var(--lh-red-soft)' }}>
                    handle with care
                  </span>
                )}
              </div>
              <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
                {items.length}
              </span>
            </div>
            <div className="space-y-2">
              {items.map((s) => (
                <SettingRow
                  key={`${s.key}:${String(s.value)}`}
                  setting={s}
                  available={available}
                  status={status[s.key]}
                  busy={busyKey === s.key}
                  onSave={save}
                />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
