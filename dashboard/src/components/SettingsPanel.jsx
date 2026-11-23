import { useCallback, useEffect, useMemo, useState } from 'react'
import { NOTICE, SOURCE_URL } from '../legal.js'

// The Settings panel; the editable runtime allowlist.
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

// The group that carries the dangerous toggles (kill-switch); tinted red.
const DANGER_GROUP = 'Budget & Safety'
const ADVANCED_KEYS = new Set([
  'opencode_model',
  'model_cheap',
  'model_smart',
  'router_mode',
  'org_departments',
])

const GROUP_META = {
  Heartbeat: {
    label: 'Autonomy loop',
    help: 'How routine checks think, which fallbacks they use, and whether the heartbeat can keep working.',
  },
  Models: {
    label: 'Engines',
    help: 'Primary chat, routine reasoning and hard reasoning choices. Exact provider/model ids are advanced details.',
  },
  'Budget & Safety': {
    label: 'Actions & safety',
    help: 'Cost limits, hard stops and privacy controls.',
  },
  Memory: {
    label: 'Memory',
    help: 'What Lhassa can retrieve, learn and carry between sessions.',
  },
  Surfaces: {
    label: 'Surfaces & notifications',
    help: 'Voice, Telegram and notification channels.',
  },
  Org: {
    label: 'Organization defaults',
    help: 'Defaults used when deriving org charts and departments.',
  },
}

const SETTING_LABELS = {
  heartbeat_provider: 'Autonomy engine',
  opencode_model: 'Routine local-CLI preset',
  fallback_providers: 'Fallback order',
  llm_provider: 'Primary interaction engine',
  model: 'Primary model preset',
  model_cheap: 'Routine reasoning model',
  model_smart: 'Hard reasoning model',
  router_mode: 'Routing policy',
  llm_kill_switch: 'Stop all model calls',
  llm_daily_token_budget: 'Daily output-token budget',
  pseudonymize_remote: 'Pseudonymize before remote engines',
  memory_layer4_enabled: 'Semantic memory retrieval',
  voice_enabled: 'Voice surface',
  telegram_poll_enabled: 'Telegram inbound polling',
  notify_channels: 'Notification channels',
  org_departments: 'Default departments',
}

const SETTING_HELP = {
  heartbeat_provider: 'Choose how scheduled autonomy checks think. Auto keeps the choice flexible and prefers local/free routes before cloud.',
  fallback_providers: 'Ordered backup engines used when the primary routine engine fails. Keep at least one option.',
  llm_provider: 'Engine for direct interaction surfaces such as CLI chat, voice and agent prompts. This can be local, CLI-backed or cloud-backed.',
  model: 'This is the dashboard control for LHASSA_MODEL. Leave it on engine default to let the selected primary engine choose its default model, or select/type an exact model id to pin it.',
  model_cheap: 'Advanced: exact model or preset for routine low-cost reasoning. Leave unset to inherit the primary model.',
  model_smart: 'Advanced: exact model or preset for harder reasoning. Leave unset to inherit the primary model.',
  opencode_model: 'Advanced: exact preset for one local CLI routine path. This is intentionally hidden in basic mode.',
}

const OPTION_LABELS = {
  auto: 'Auto',
  local: 'Local endpoint',
  cloud: 'Cloud gateway',
  default: 'Default gateway',
  opencode: 'Local CLI: OpenCode',
  claude: 'Local CLI: Claude Code',
  codex: 'Local CLI: Codex',
  groq: 'Cloud/API: Groq',
  anthropic: 'Cloud/API: Anthropic',
  openai: 'Cloud/API: OpenAI',
  openrouter: 'Cloud/API: OpenRouter',
  cerebras: 'Cloud/API: Cerebras',
  ollama: 'Local server: Ollama',
  rules: 'Rules-based routing',
  off: 'Routing off',
}

function labelForSetting(setting) {
  return SETTING_LABELS[setting.key] || setting.label
}

function helpForSetting(setting) {
  return SETTING_HELP[setting.key] || setting.help || ''
}

function labelForOption(value, advanced) {
  if (advanced) return value
  return OPTION_LABELS[value] || value
}

function engineLabel(value, advanced = false) {
  const text = String(value || '').trim()
  if (!text) return 'selected engine'
  return labelForOption(text, advanced)
}

function isDefaultLike(value) {
  const text = String(value ?? '').trim()
  return !text || (text.startsWith('(') && text.endsWith(')'))
}

function fmtDefault(setting, advanced) {
  if (setting.secret) return null
  if (!advanced) return setting.overridden ? 'customized' : 'default'
  return `default ${String(setting.default)}`
}

function rowWarning(setting) {
  if (setting.key === 'llm_kill_switch' && setting.value) {
    return 'Model calls are stopped. Autonomous reasoning and cloud-backed surfaces will degrade.'
  }
  if (setting.key === 'llm_daily_token_budget' && Number(setting.value || 0) === 0) {
    return 'No daily token ceiling is set. Add a budget if this instance may use paid engines.'
  }
  if (setting.key === 'fallback_providers' && !String(setting.value || '').trim()) {
    return 'No fallback order is configured. A provider failure can stop routine reasoning.'
  }
  return ''
}

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

const CUSTOM = '__custom__'

// ── model field: a select of known ids + a "Custom…" path that reveals an input ──
// The owner asked for drop-lists, not free typing; but a custom model id must
// still be possible. So: a <select> populated from the setting's options (falling
// back to the available pools) plus a "Custom…" entry that swaps in a text field.
function ModelCombo({ setting, options, busy, onSave, advanced, defaultLabel = 'engine default' }) {
  const value = setting.value ?? ''
  const defaultLike = !setting.overridden || isDefaultLike(value)
  const known = options.includes(value)
  // Custom mode when the current value isn't a known option (and isn't empty),
  // or when the user explicitly picks "Custom…".
  const [custom, setCustom] = useState(Boolean(value) && !defaultLike && !known)
  const [draft, setDraft] = useState(defaultLike ? '' : value)

  if (custom) {
    const dirty = String(draft) !== String(value)
    const commit = () => { if (dirty && draft.trim()) onSave(setting.key, draft.trim()) }
    return (
      <div className="flex items-center gap-1.5">
        <input
          className="lh-input"
          placeholder="exact model id…"
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit() } }}
          style={{ maxWidth: 240 }}
        />
        <button
          type="button"
          className="lh-btn"
          title="Back to the list"
          disabled={busy}
          onClick={() => { setCustom(false); setDraft(value) }}
        >
          ▾
        </button>
      </div>
    )
  }
  return (
    <select
      className="lh-select"
      value={defaultLike ? '__default__' : known ? value : ''}
      disabled={busy}
      onChange={(e) => {
        if (e.target.value === CUSTOM) { setDraft(value || ''); setCustom(true) }
        else if (e.target.value === '__default__') onSave(setting.key, '')
        else onSave(setting.key, e.target.value)
      }}
      style={{ maxWidth: 260 }}
    >
      <option value="__default__">{defaultLabel}</option>
      {options.map((o) => <option key={o} value={o}>{advanced ? o : `Preset: ${o}`}</option>)}
      <option value={CUSTOM}>Custom…</option>
    </select>
  )
}

// ── multi-select: checkbox chips for an ordered provider chain (CSV-backed) ──────
function MultiSelect({ setting, options, busy, onSave, advanced }) {
  const current = useMemo(
    () => String(setting.value ?? '').split(',').map((s) => s.trim()).filter(Boolean),
    [setting.value],
  )
  const toggle = (opt) => {
    const next = current.includes(opt)
      ? current.filter((x) => x !== opt)   // remove
      : [...current, opt]                  // append (keeps chain priority order)
    onSave(setting.key, next)              // backend accepts a list
  }
  return (
    <div className="flex flex-col items-end gap-1.5" style={{ maxWidth: 320 }}>
      <div className="flex flex-wrap gap-1.5 justify-end">
        {options.map((opt) => {
          const on = current.includes(opt)
          const idx = current.indexOf(opt)
          return (
            <button
              key={opt}
              type="button"
              disabled={busy}
              onClick={() => toggle(opt)}
              className={`lh-tag ${on ? 'lh-tag-accent' : ''}`}
              style={{ cursor: busy ? 'not-allowed' : 'pointer' }}
              title={on ? `#${idx + 1} in the chain; click to remove` : 'Click to add to the chain'}
            >
              {on ? `${idx + 1}· ` : ''}{labelForOption(opt, advanced)}
            </button>
          )
        })}
      </div>
      <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
        {current.length ? `order: ${current.map((v) => labelForOption(v, advanced)).join(' → ')}` : 'none selected'}
      </span>
    </div>
  )
}

// ── one editable setting row ──────────────────────────────────────────────────
function SettingRow({ setting, settingsByKey, available, status, busy, onSave, advanced }) {
  const { key, type, value, options, overridden, secret, restart, allow_custom } = setting
  // Local draft for free-text / numeric controls; committed on blur or Enter.
  const [draft, setDraft] = useState(value ?? '')

  // Enum options: prefer the setting's own list, else the matching available pool.
  const enumOptions = useMemo(() => {
    if (Array.isArray(options) && options.length) return options
    if (key === 'heartbeat_provider') return available.heartbeat_providers || []
    if (key === 'router_mode') return available.router_modes || []
    return []
  }, [options, key, available])

  // Model-combo option pool: the setting's own options, else the matching pool.
  const modelOptions = useMemo(() => {
    if (Array.isArray(options) && options.length) return options
    if (key === 'opencode_model') return available.opencode_models || []
    return available.model_options || []
  }, [options, key, available])

  // Multi-select option pool (fallback_providers).
  const multiOptions = useMemo(() => {
    if (Array.isArray(options) && options.length) return options
    return available.fallback_providers || []
  }, [options, available])

  const dirty = String(draft) !== String(value ?? '')
  const commit = () => { if (dirty) onSave(key, draft) }
  const primaryEngine = settingsByKey?.llm_provider?.value
  const primaryEngineLabel = engineLabel(primaryEngine, advanced)
  const modelDefaultLabel = key === 'model'
    ? `Use ${primaryEngineLabel} default`
    : 'Use engine default'

  // A str field with a known option pool (and a custom escape hatch) → model combo.
  const isModelCombo = type === 'str' && (allow_custom || (Array.isArray(options) && options.length))

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
  } else if (type === 'multi') {
    control = <MultiSelect setting={setting} options={multiOptions} busy={busy} onSave={onSave} advanced={advanced} />
  } else if (type === 'enum') {
    control = (
      <select
        className="lh-select"
        value={value ?? ''}
        disabled={busy}
        onChange={(e) => onSave(key, e.target.value)}
        style={{ maxWidth: 240 }}
      >
        {enumOptions.map((o) => <option key={o} value={o}>{labelForOption(o, advanced)}</option>)}
        {!enumOptions.includes(value) && value != null && <option value={value}>{String(value)}</option>}
      </select>
    )
  } else if (isModelCombo) {
    control = (
      <ModelCombo
        key={String(value)}
        setting={setting}
        options={modelOptions}
        busy={busy}
        onSave={onSave}
        advanced={advanced}
        defaultLabel={modelDefaultLabel}
      />
    )
  } else {
    const numeric = type === 'int' || type === 'float'
    control = (
      <input
        className="lh-input"
        inputMode={numeric ? 'numeric' : undefined}
        value={draft}
        disabled={busy}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit() } }}
        style={{ maxWidth: 260 }}
      />
    )
  }

  const st = status || {}
  const restartLabel = RESTART_LABEL[restart] || 'Applied live'
  const help = helpForSetting(setting)
  const warning = rowWarning(setting)
  const defaultLabel = fmtDefault(setting, advanced)
  const defaultLike = !overridden || isDefaultLike(value)

  return (
    <div className="lh-row">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            {overridden && (
              <span
                className="lh-dot lh-dot-accent"
                title="Overridden; differs from the default"
                style={{ flexShrink: 0 }}
              />
            )}
            <span className="text-[13px] font-medium" style={{ color: 'var(--lh-text)' }}>{labelForSetting(setting)}</span>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap mt-1">
            {defaultLabel && <span className="lh-tag">{defaultLabel}</span>}
            {overridden && <span className="lh-tag lh-tag-accent">overridden</span>}
            <span className="lh-tag">{RESTART_LABEL[restart] || 'Applied live'}</span>
            {advanced && (
              <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>{key}</span>
            )}
          </div>
          {help && (
            <p className="text-[11px] mt-1.5 max-w-2xl" style={{ color: 'var(--lh-text-muted)' }}>{help}</p>
          )}
          {key === 'model' && (
            <div className="text-[11px] mt-1.5 max-w-2xl" style={{ color: 'var(--lh-accent-text)' }}>
              Effective choice: {defaultLike
                ? `${primaryEngineLabel} default -> ${String(value || 'provider decides at runtime')} (LHASSA_MODEL is not pinned)`
                : `pinned to ${String(value)} for ${primaryEngineLabel}`}.
            </div>
          )}
          {advanced && !secret && (
            <div className="lh-mono text-[10px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>
              env {setting.env_var} · current {String(value ?? '')}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">{control}</div>
      </div>

      {warning && (
        <div className="text-[11px] mt-2 lh-row" style={{ color: 'var(--lh-warn)', borderColor: 'rgba(217,165,33,0.35)' }}>
          {warning}
        </div>
      )}

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
  const [advanced, setAdvanced] = useState(false)

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
    for (const s of data?.settings || []) {
      if (!advanced && ADVANCED_KEYS.has(s.key)) continue
      ;(m[s.group] ||= []).push(s)
    }
    return m
  }, [data, advanced])
  const settingsByKey = useMemo(() => {
    const m = {}
    for (const s of data?.settings || []) m[s.key] = s
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
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className="lh-btn"
              onClick={() => setAdvanced((v) => !v)}
              style={advanced ? { borderColor: 'var(--lh-gold-deep)', color: 'var(--lh-accent-text)' } : undefined}
              aria-pressed={advanced}
            >
              {advanced ? 'Advanced on' : 'Advanced'}
            </button>
            <button type="button" className="lh-btn" onClick={load}>Refresh</button>
          </div>
        </div>
        <p className="text-[11px] mt-2" style={{ color: 'var(--lh-text-faint)' }}>
          Basic mode shows operational choices. Advanced mode exposes exact keys, model presets,
          defaults and environment variables for precise administration.
        </p>
      </div>

      <div className="lh-card" style={{ borderColor: 'rgba(217,165,33,0.22)' }}>
        <div className="lh-eyebrow">Configuration model</div>
        <p className="text-xs mt-2 max-w-3xl" style={{ color: 'var(--lh-text-muted)' }}>
          Prefer <strong style={{ color: 'var(--lh-text)' }}>Auto</strong> for routine work, configure
          local endpoints or local CLIs when available, and keep cloud-specific model ids in Advanced.
          Lhassa should be able to swap engines without the dashboard becoming tied to one service.
          Empty model fields mean “inherit the selected engine default”; customized fields pin an exact id.
        </p>
      </div>

      {groups.map((group) => {
        const items = byGroup[group] || []
        if (!items.length) return null
        const danger = group === DANGER_GROUP
        const meta = GROUP_META[group] || { label: group, help: '' }
        return (
          <div
            key={group}
            className="lh-card"
            style={danger ? { borderColor: 'rgba(162,62,52,0.30)' } : undefined}
          >
            <div className="lh-card-head">
              <div>
                <div className="flex items-center gap-2">
                  <span className="lh-eyebrow" style={danger ? { color: 'var(--lh-red-soft)' } : undefined}>{meta.label}</span>
                  {advanced && <span className="lh-tag">{group}</span>}
                </div>
                {meta.help && (
                  <p className="text-[11px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>{meta.help}</p>
                )}
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
                  settingsByKey={settingsByKey}
                  available={available}
                  status={status[s.key]}
                  busy={busyKey === s.key}
                  onSave={save}
                  advanced={advanced}
                />
              ))}
            </div>
          </div>
        )
      })}

      <AboutCard />
    </div>
  )
}

// ── about footer; the flag, the identity sentence, a pointer to the docs ──────
// Settings is the one panel every owner opens sooner or later and reads slowly
// (it's a form, not a stream), so it is the quiet place for Lhassa to say what it
// is; once, without repeating it on every screen.
function AboutCard() {
  return (
    <div className="lh-card flex items-center gap-3 flex-wrap" style={{ borderColor: 'var(--lh-border)' }}>
      <svg
        width="28" height="28" viewBox="0 0 48 48"
        className="rounded-[8px]"
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
      <div className="min-w-0">
        <p className="text-[13px]" style={{ color: 'var(--lh-text)' }}>
          <strong style={{ color: 'var(--lh-accent-text)' }}>Build the brain, rent the body.</strong>{' '}
          I am Lhassa. I live on this machine; I watch quietly, remember what
          matters, and think only when it is worth the spend.
        </p>
        <p className="text-[11px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>
          More on how I'm told: <span className="lh-mono">README.md</span> in the repo.
        </p>
        {/* Appropriate Legal Notice; required by the AGPL-3.0 section 7(b)
            term in LICENSE. Rendered from src/legal.js, asserted by
            tests/test_legal_notice.py; do not inline or remove it. */}
        <p className="text-[11px] mt-2" style={{ color: 'var(--lh-text-faint)' }}>
          {NOTICE}{' '}
          <a href={SOURCE_URL} target="_blank" rel="noreferrer" className="underline">
            Source
          </a>
        </p>
      </div>
    </div>
  )
}
