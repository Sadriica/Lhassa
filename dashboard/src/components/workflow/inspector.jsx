import { useState } from 'react'
import { kindMeta } from '../flowNodeKinds'

// ── the WHEN op vocabulary (mirrors flows/engine.eval_when) ──────────────────────
const WHEN_OPS = ['truthy', 'falsy', 'eq', 'ne', 'contains', 'gt', 'lt']

// ── node inspector (per kind) ───────────────────────────────────────────────────
export function NodeInspector({ node, skills, brains, onChange, onLabel, onDelete }) {
  const kind = node.data.kind
  const config = node.data.config || {}
  const setConfig = (patch) => onChange({ ...config, ...patch })

  // Task HYBRID routing mode: a pinned brain wins; else auto-by-skill.
  const routingMode = config.brain ? 'pin' : 'auto'

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="lh-mono text-sm font-semibold" style={{ color: 'var(--lh-text)' }}>{node.id}</span>
        <span
          className="lh-tag"
          style={{
            borderColor: `rgba(${kindMeta(kind).rgb},0.55)`,
            color: `rgba(${kindMeta(kind).rgb},0.95)`,
            background: 'transparent',
            textTransform: 'uppercase',
            letterSpacing: '0.1em',
            fontSize: 9,
          }}
        >
          {kindMeta(kind).label}
        </span>
      </div>

      <label className="space-y-1 block">
        <span className="lh-label">label</span>
        <input
          className="lh-input"
          value={node.data.label || ''}
          placeholder={node.id}
          onChange={(e) => onLabel(e.target.value)}
        />
      </label>

      {kind === 'trigger' && (
        <label className="space-y-1 block">
          <span className="lh-label">type</span>
          <select className="lh-select" value={config.type || 'manual'} onChange={(e) => setConfig({ type: e.target.value })}>
            <option value="manual">manual</option>
            <option value="schedule">schedule</option>
            <option value="event">event</option>
          </select>
        </label>
      )}

      {kind === 'task' && (
        <div className="space-y-2">
          <span className="lh-label">routing; HYBRID (auto by skill, or pin a brain)</span>
          <div className="flex gap-1" role="group" aria-label="routing mode">
            <button
              type="button"
              className={`lh-btn ${routingMode === 'auto' ? 'lh-btn-primary' : ''}`}
              style={{ flex: 1 }}
              onClick={() => { const { brain, ...rest } = config; void brain; onChange(rest) }}
            >
              Auto by skill
            </button>
            <button
              type="button"
              className={`lh-btn ${routingMode === 'pin' ? 'lh-btn-primary' : ''}`}
              style={{ flex: 1 }}
              onClick={() => { const { skill, ...rest } = config; void skill; onChange({ ...rest, brain: config.brain || (brains[0]?.id || '') }) }}
            >
              Pin a brain
            </button>
          </div>

          {routingMode === 'auto' ? (
            <label className="space-y-1 block">
              <span className="lh-label">skill; auto-routes through the org</span>
              <select className="lh-select" value={config.skill || ''} onChange={(e) => setConfig({ skill: e.target.value || undefined })}>
                <option value="">(any; route by prompt)</option>
                {skills.map((s) => (
                  <option key={s.domain} value={s.domain}>{s.domain}{s.name ? ` · ${s.name}` : ''}</option>
                ))}
              </select>
            </label>
          ) : (
            <label className="space-y-1 block">
              <span className="lh-label">brain; pinned target</span>
              <select className="lh-select" value={config.brain || ''} onChange={(e) => setConfig({ brain: e.target.value })}>
                <option value="">choose a brain…</option>
                {brains.map((b) => (
                  <option key={b.id} value={b.id}>{b.id}{b.department ? ` · ${b.department}` : ''}</option>
                ))}
              </select>
            </label>
          )}

          <label className="space-y-1 block">
            <span className="lh-label">prompt (optional · {'{input}'} is filled from upstream)</span>
            <textarea
              className="lh-input"
              rows={3}
              value={config.prompt || ''}
              placeholder="what should this task do?"
              onChange={(e) => setConfig({ prompt: e.target.value || undefined })}
            />
          </label>
        </div>
      )}

      {kind === 'gate' && (
        <div className="space-y-2">
          <label className="space-y-1 block">
            <span className="lh-label">type</span>
            <select className="lh-select" value={config.type || 'verify'} onChange={(e) => setConfig({ type: e.target.value })}>
              <option value="verify">verify; objective check (no LLM)</option>
              <option value="approval">approval; park for a human</option>
            </select>
          </label>
          {(config.type || 'verify') === 'verify' && (
            <SpecEditor spec={config.spec} onChange={(spec) => setConfig({ spec })} />
          )}
          {config.type === 'approval' && (
            <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
              An approval gate parks the run for a human (it does not pass on its own).
            </p>
          )}
        </div>
      )}

      {kind === 'output' && (
        <div className="space-y-2">
          <label className="space-y-1 block">
            <span className="lh-label">type</span>
            <select className="lh-select" value={config.type || 'log'} onChange={(e) => setConfig({ type: e.target.value })}>
              <option value="notify">notify</option>
              <option value="doc">doc</option>
              <option value="log">log</option>
            </select>
          </label>
          <label className="space-y-1 block">
            <span className="lh-label">target (optional)</span>
            <input
              className="lh-input"
              value={config.target || ''}
              placeholder="e.g. telegram, a path, a channel"
              onChange={(e) => setConfig({ target: e.target.value || undefined })}
            />
          </label>
        </div>
      )}

      {kind === 'branch' && (
        <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
          A branch has no config of its own; the condition lives on each OUTGOING edge&apos;s
          <span className="lh-mono"> when</span>. Click an edge leaving this node to set its condition
          (the first matching edge is taken; an unconditional edge is the <span className="lh-mono">else</span>).
        </p>
      )}

      <button
        type="button"
        className="lh-btn lh-btn-danger"
        onClick={onDelete}
        disabled={kind === 'trigger'}
        title={kind === 'trigger' ? 'a flow keeps its single trigger' : 'remove this node'}
      >
        Delete node
      </button>
    </div>
  )
}

// A small JSON spec editor for a verify gate (free-form object, parsed on the fly).
function SpecEditor({ spec, onChange }) {
  const [text, setText] = useState(() => {
    try { return spec && Object.keys(spec).length ? JSON.stringify(spec, null, 2) : '' }
    catch { return '' }
  })
  const [err, setErr] = useState('')
  return (
    <label className="space-y-1 block">
      <span className="lh-label">spec; verify rule (JSON, optional)</span>
      <textarea
        className="lh-input lh-mono"
        rows={3}
        value={text}
        placeholder='e.g. {"contains": "ok"}'
        onChange={(e) => {
          const v = e.target.value
          setText(v)
          if (!v.trim()) { setErr(''); onChange({}); return }
          try { onChange(JSON.parse(v)); setErr('') }
          catch { setErr('not valid JSON yet; last good value kept') }
        }}
      />
      {err && <span className="text-[10px]" style={{ color: 'var(--lh-warn)' }}>{err}</span>}
    </label>
  )
}

// ── edge inspector: the optional `when` condition (used by branches / gates) ─────
export function EdgeInspector({ edge, onChange, onDelete }) {
  const when = edge.data?.when || null
  const enabled = Boolean(when && when.op)
  const set = (patch) => onChange({ ...(when || { op: 'truthy' }), ...patch })
  const valueless = !enabled || when.op === 'truthy' || when.op === 'falsy'
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="lh-mono text-[12px]" style={{ color: 'var(--lh-text)' }}>{edge.source} → {edge.target}</span>
        <span className="lh-tag" style={{ fontSize: 9 }}>edge</span>
      </div>
      <label className="flex items-center gap-2 text-[12px]" style={{ color: 'var(--lh-text-muted)' }}>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onChange(e.target.checked ? { field: when?.field || '', op: 'truthy' } : null)}
          style={{ accentColor: 'var(--lh-accent)' }}
        />
        conditional (when); otherwise unconditional / the else path
      </label>
      {enabled && (
        <div className="space-y-2">
          <label className="space-y-1 block">
            <span className="lh-label">field; a key in the upstream result (e.g. passed, status)</span>
            <input className="lh-input lh-mono" value={when.field || ''} onChange={(e) => set({ field: e.target.value })} placeholder="passed" />
          </label>
          <label className="space-y-1 block">
            <span className="lh-label">op</span>
            <select className="lh-select" value={when.op} onChange={(e) => set({ op: e.target.value })}>
              {WHEN_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
            </select>
          </label>
          {!valueless && (
            <label className="space-y-1 block">
              <span className="lh-label">value</span>
              <input className="lh-input lh-mono" value={when.value ?? ''} onChange={(e) => set({ value: e.target.value })} placeholder="ok" />
            </label>
          )}
        </div>
      )}
      <button type="button" className="lh-btn lh-btn-danger" onClick={onDelete}>Delete edge</button>
    </div>
  )
}
