import { useCallback, useEffect, useMemo, useState } from 'react'

// SKILLS admin panel — first-class SKILL.md skills. Drives the skills API:
//   GET    /api/skills                    list installed
//   GET    /api/skills/recommend?context= offline catalog minus installed
//   POST   /api/skills                    create from a form (or "add" a rec)
//   POST   /api/skills/ai      {brief}     draft WITH AI → {skill, saved, error}
//   POST   /api/skills/{n}/improve         improve WITH AI → {skill, saved, error}
//   GET    /api/skills/{n}/brains          which brains carry it → {skill, brains}
//   POST   /api/skills/{n}/assign {brain_id}
//   DELETE /api/skills/{n}
// Brains for the assign selector come from /api/org/topology. AI calls degrade to
// a clear error (no model configured) — we surface {detail}/{error} verbatim.

// Pull a body of text from an API error response ({detail} or {error}).
async function errText(r, fallback) {
  const d = await r.json().catch(() => ({}))
  return d.detail || d.error || fallback
}

function MetaBadges({ metadata }) {
  const m = metadata || {}
  const domain = m.domain
  const level = m.level
  const tags = Array.isArray(m.tags) ? m.tags : []
  if (!domain && level === undefined && tags.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {domain && <span className="lh-tag lh-tag-accent">{domain}</span>}
      {level !== undefined && level !== null && level !== '' && (
        <span className="lh-badge">level={String(level)}</span>
      )}
      {tags.map((t) => <span key={t} className="lh-tag">{t}</span>)}
    </div>
  )
}

// ── installed-skill card ──────────────────────────────────────────────────────
function SkillCard({ skill, brainIds, busy, onImprove, onDelete, onAssign, onLoadBrains, brains }) {
  const [open, setOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [assignTo, setAssignTo] = useState('')
  const instr = skill.instructions || ''
  const preview = instr.length > 220 ? `${instr.slice(0, 220)}…` : instr

  return (
    <div className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="lh-mono text-[13px] font-semibold" style={{ color: 'var(--lh-text)' }}>
            {skill.name}
          </span>
          {skill.description && (
            <p className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>{skill.description}</p>
          )}
          <MetaBadges metadata={skill.metadata} />
        </div>
        <div className="flex gap-1 shrink-0">
          <button type="button" className="lh-btn" disabled={busy} onClick={() => onImprove(skill.name)} title="Improve with AI">
            ✦ Improve
          </button>
        </div>
      </div>

      {instr && (
        <div className="mt-2">
          <button
            type="button"
            className="text-[11px]"
            style={{ color: 'var(--lh-accent-text)', cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? '▾ hide instructions' : '▸ instructions'}
          </button>
          {open ? (
            <pre
              className="lh-mono text-[11px] mt-1.5 whitespace-pre-wrap"
              style={{ color: 'var(--lh-text-muted)', maxHeight: 240, overflowY: 'auto' }}
            >{instr}</pre>
          ) : (
            <p className="text-[11px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>{preview}</p>
          )}
        </div>
      )}

      {/* assign + which brains */}
      <div className="flex flex-wrap items-center gap-1.5 mt-2 pt-2" style={{ borderTop: '1px solid var(--lh-border)' }}>
        <select className="lh-select" style={{ width: 'auto', flex: '1 1 140px' }} value={assignTo} onChange={(e) => setAssignTo(e.target.value)}>
          <option value="">assign to brain…</option>
          {brainIds.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
        <button
          type="button" className="lh-btn" disabled={busy || !assignTo}
          onClick={() => { onAssign(skill.name, assignTo); setAssignTo('') }}
        >
          Assign
        </button>
        <button type="button" className="lh-btn" disabled={busy} onClick={() => onLoadBrains(skill.name)}>
          Which brains?
        </button>
        {confirmDelete ? (
          <span className="flex gap-1.5">
            <button type="button" className="lh-btn lh-btn-danger" disabled={busy} onClick={() => onDelete(skill.name)}>
              Confirm
            </button>
            <button type="button" className="lh-btn" onClick={() => setConfirmDelete(false)}>Cancel</button>
          </span>
        ) : (
          <button type="button" className="lh-btn lh-btn-danger" disabled={busy} onClick={() => setConfirmDelete(true)}>
            Delete
          </button>
        )}
      </div>

      {brains !== undefined && (
        <div className="text-[11px] mt-1.5 lh-mono" style={{ color: 'var(--lh-text-muted)' }}>
          {brains.length === 0 ? (
            <span style={{ color: 'var(--lh-text-faint)' }}>No brains carry this skill.</span>
          ) : (
            <>brains: {brains.map((b, i) => (
              <span key={b}>
                <span style={{ color: 'var(--lh-accent-text)' }}>{b}</span>
                {i < brains.length - 1 ? ', ' : ''}
              </span>
            ))}</>
          )}
        </div>
      )}
    </div>
  )
}

// ── create form (plain) ───────────────────────────────────────────────────────
function CreateSkillForm({ busy, onCreate, onClose }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [instructions, setInstructions] = useState('')
  const [domain, setDomain] = useState('')
  const [tags, setTags] = useState('')

  const submit = () => {
    const metadata = {}
    if (domain.trim()) metadata.domain = domain.trim()
    const tagList = tags.split(',').map((t) => t.trim()).filter(Boolean)
    if (tagList.length) metadata.tags = tagList
    onCreate({
      name: name.trim(),
      description: description.trim() || undefined,
      instructions: instructions || undefined,
      metadata,
    })
  }

  return (
    <div className="lh-card" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="lh-card-title">New skill</h3>
        <button type="button" className="lh-btn" onClick={onClose}>✕</button>
      </div>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1">
            <span className="lh-label">name *</span>
            <input className="lh-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. summarize" />
          </label>
          <label className="space-y-1">
            <span className="lh-label">domain</span>
            <input className="lh-input" value={domain} onChange={(e) => setDomain(e.target.value)} />
          </label>
        </div>
        <label className="space-y-1 block">
          <span className="lh-label">description</span>
          <input className="lh-input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="space-y-1 block">
          <span className="lh-label">tags (comma-separated)</span>
          <input className="lh-input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="writing, docs" />
        </label>
        <label className="space-y-1 block">
          <span className="lh-label">instructions (SKILL.md body)</span>
          <textarea
            className="lh-input" rows={5} value={instructions}
            onChange={(e) => setInstructions(e.target.value)} style={{ resize: 'vertical' }}
          />
        </label>
        <button type="button" className="lh-btn lh-btn-primary" disabled={busy || !name.trim()} onClick={submit}>
          Create skill
        </button>
      </div>
    </div>
  )
}

// ── create-with-AI ────────────────────────────────────────────────────────────
function AIForm({ busy, onDraft, result, onClose }) {
  const [brief, setBrief] = useState('')
  return (
    <div className="lh-card" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="lh-card-title">Draft a skill with AI</h3>
        <button type="button" className="lh-btn" onClick={onClose}>✕</button>
      </div>
      <div className="space-y-2">
        <textarea
          className="lh-input" rows={3} placeholder="Brief — what should this skill do?"
          value={brief} onChange={(e) => setBrief(e.target.value)} style={{ resize: 'vertical' }}
        />
        <button type="button" className="lh-btn lh-btn-primary" disabled={busy || !brief.trim()} onClick={() => onDraft(brief.trim())}>
          {busy ? 'Drafting…' : 'Draft with AI'}
        </button>
        {result && (
          <div className="lh-row mt-1" style={{ background: 'var(--lh-surface)' }}>
            {result.error ? (
              <div className="text-[11px]" style={{ color: 'var(--lh-alert)' }}>{result.error}</div>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <span className="lh-mono text-[13px] font-semibold" style={{ color: 'var(--lh-text)' }}>
                    {result.skill?.name}
                  </span>
                  <span className="lh-pill">
                    <span className={`lh-dot ${result.saved ? 'lh-dot-ok' : 'lh-dot-warn'}`} />
                    {result.saved ? 'saved' : 'not saved'}
                  </span>
                </div>
                {result.skill?.description && (
                  <p className="text-[11px] mt-1" style={{ color: 'var(--lh-text-muted)' }}>{result.skill.description}</p>
                )}
                <MetaBadges metadata={result.skill?.metadata} />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── panel ─────────────────────────────────────────────────────────────────────
export default function SkillsPanel() {
  const [skills, setSkills] = useState([])
  const [recommended, setRecommended] = useState([])
  const [brainIds, setBrainIds] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [showAI, setShowAI] = useState(false)
  const [aiResult, setAiResult] = useState(null)
  const [recContext, setRecContext] = useState('')
  const [skillBrains, setSkillBrains] = useState({}) // name → [brain ids]

  const loadSkills = useCallback(async () => {
    try {
      const r = await fetch('/api/skills')
      if (!r.ok) throw new Error('skills fetch failed')
      setSkills(await r.json())
      setError('')
    } catch {
      setError('Could not load skills.')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadRecommended = useCallback(async (context = '') => {
    try {
      const r = await fetch(`/api/skills/recommend?context=${encodeURIComponent(context)}`)
      setRecommended(r.ok ? await r.json() : [])
    } catch {
      setRecommended([])
    }
  }, [])

  const loadBrainIds = useCallback(async () => {
    try {
      const r = await fetch('/api/org/topology')
      const d = r.ok ? await r.json() : null
      setBrainIds((d?.nodes || []).map((n) => n.id))
    } catch {
      setBrainIds([])
    }
  }, [])

  useEffect(() => {
    loadSkills()
    loadRecommended('')
    loadBrainIds()
  }, [loadSkills, loadRecommended, loadBrainIds])

  // Run a mutating call, refresh installed + recommended, surface {detail}/{error}.
  const run = useCallback(async (label, doFetch, after) => {
    setBusy(true); setError(''); setNotice('')
    try {
      const r = await doFetch()
      if (!r.ok) throw new Error(await errText(r, `${label} failed`))
      const d = await r.json().catch(() => ({}))
      if (after) await after(d)
      await loadSkills()
      await loadRecommended(recContext)
      return d
    } catch (e) {
      setError(e instanceof Error ? e.message : `${label} failed`)
      return null
    } finally {
      setBusy(false)
    }
  }, [loadSkills, loadRecommended, recContext])

  const createSkill = async (payload) => {
    const d = await run('Create', () =>
      fetch('/api/skills', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }))
    if (d) { setShowCreate(false); setNotice(`Created ${d.name}.`) }
  }

  const draftWithAI = async (brief) => {
    setBusy(true); setError(''); setNotice(''); setAiResult(null)
    try {
      const r = await fetch('/api/skills/ai', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ brief }),
      })
      if (!r.ok) throw new Error(await errText(r, 'AI draft failed'))
      const d = await r.json()
      setAiResult(d) // {skill, saved, error}
      if (d.saved) await loadSkills()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'AI draft failed')
    } finally {
      setBusy(false)
    }
  }

  const improveSkill = (name) =>
    run('Improve', () => fetch(`/api/skills/${encodeURIComponent(name)}/improve`, { method: 'POST' }),
      (d) => {
        if (d.error) setError(d.error)
        else setNotice(`Improved ${name}${d.saved ? '' : ' (not saved)'}.`)
      })

  const deleteSkill = (name) => run('Delete', () =>
    fetch(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' }))

  const assignSkill = (name, brainId) =>
    run('Assign', () =>
      fetch(`/api/skills/${encodeURIComponent(name)}/assign`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ brain_id: brainId }),
      }),
      () => setNotice(`Assigned ${name} → ${brainId}.`))

  const addRecommended = (rec) => createSkill({
    name: rec.name,
    description: rec.description || undefined,
    metadata: {
      ...(rec.domain ? { domain: rec.domain } : {}),
      ...(rec.tags?.length ? { tags: rec.tags } : {}),
    },
  })

  const loadSkillBrains = async (name) => {
    try {
      const r = await fetch(`/api/skills/${encodeURIComponent(name)}/brains`)
      const d = r.ok ? await r.json() : { brains: [] }
      setSkillBrains((prev) => ({ ...prev, [name]: d.brains || [] }))
    } catch {
      setSkillBrains((prev) => ({ ...prev, [name]: [] }))
    }
  }

  const recList = useMemo(() => recommended.slice(0, 12), [recommended])

  return (
    <div className="lh-card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <h2 className="lh-card-title">Skills</h2>
          <span className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
            {skills.length} installed
          </span>
        </div>
        <div className="flex gap-2">
          <button type="button" className="lh-btn" onClick={() => { loadSkills(); loadRecommended(recContext) }} disabled={busy}>
            Refresh
          </button>
          <button type="button" className="lh-btn" onClick={() => { setShowAI((v) => !v); setShowCreate(false) }}>
            ✦ Create with AI
          </button>
          <button type="button" className="lh-btn lh-btn-primary" onClick={() => { setShowCreate((v) => !v); setShowAI(false) }}>
            + Create skill
          </button>
        </div>
      </div>

      {showCreate && (
        <div className="mb-3">
          <CreateSkillForm busy={busy} onCreate={createSkill} onClose={() => setShowCreate(false)} />
        </div>
      )}
      {showAI && (
        <div className="mb-3">
          <AIForm busy={busy} onDraft={draftWithAI} result={aiResult} onClose={() => { setShowAI(false); setAiResult(null) }} />
        </div>
      )}

      {error && (
        <div className="lh-row mb-3" style={{ borderColor: 'rgba(248,113,113,0.4)', color: 'var(--lh-alert)' }}>{error}</div>
      )}
      {notice && (
        <div className="lh-row mb-3" style={{ borderColor: 'rgba(45,212,191,0.4)', color: 'var(--lh-accent-text)' }}>{notice}</div>
      )}

      {loading ? (
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading skills…</p>
      ) : (
        <>
          {/* installed */}
          <div className="lh-label mb-2">installed</div>
          {skills.length === 0 ? (
            <p className="text-xs lh-row" style={{ color: 'var(--lh-text-faint)' }}>
              No skills installed yet. Create one, draft one with AI, or add a recommended skill below.
            </p>
          ) : (
            <div className="space-y-2">
              {skills.map((s) => (
                <SkillCard
                  key={s.name} skill={s} brainIds={brainIds} busy={busy}
                  onImprove={improveSkill} onDelete={deleteSkill} onAssign={assignSkill}
                  onLoadBrains={loadSkillBrains} brains={skillBrains[s.name]}
                />
              ))}
            </div>
          )}

          {/* recommended */}
          <div className="mt-4">
            <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
              <div className="lh-label">recommended — not yet installed</div>
              <div className="flex gap-1.5">
                <input
                  className="lh-input" style={{ width: 200 }} placeholder="context (optional)"
                  value={recContext} onChange={(e) => setRecContext(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') loadRecommended(recContext) }}
                />
                <button type="button" className="lh-btn" onClick={() => loadRecommended(recContext)}>Suggest</button>
              </div>
            </div>
            {recList.length === 0 ? (
              <p className="text-[11px]" style={{ color: 'var(--lh-text-faint)' }}>
                Nothing to recommend — every catalog skill is installed.
              </p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {recList.map((rec) => (
                  <div key={rec.name} className="lh-row" style={{ background: 'var(--lh-surface-2)' }}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <span className="lh-mono text-[12px] font-semibold" style={{ color: 'var(--lh-text)' }}>{rec.name}</span>
                        <p className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>{rec.description}</p>
                        <MetaBadges metadata={{ domain: rec.domain, tags: rec.tags }} />
                      </div>
                      <button type="button" className="lh-btn lh-btn-primary shrink-0" disabled={busy} onClick={() => addRecommended(rec)}>
                        + Add
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
