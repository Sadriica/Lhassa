import { useCallback, useEffect, useMemo, useState } from 'react'

const POLL_MS = 15000

function asArray(value) {
  return Array.isArray(value) ? value : []
}

function StatusDot({ ok, warn = false }) {
  return <span className={`lh-dot ${ok ? 'lh-dot-ok' : warn ? 'lh-dot-warn' : 'lh-dot-alert'}`} />
}

function CapabilityRow({ name, status, detail, href, ok, warn }) {
  return (
    <li className="lh-row">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <StatusDot ok={ok} warn={warn} />
            <span className="text-[13px] font-medium" style={{ color: 'var(--lh-text)' }}>{name}</span>
            <span className="lh-tag">{status}</span>
          </div>
          {detail && (
            <div className="text-[11px] mt-1" style={{ color: 'var(--lh-text-muted)' }}>{detail}</div>
          )}
        </div>
        {href && (
          <button type="button" className="lh-btn shrink-0" onClick={() => { window.location.hash = href.replace(/^#/, '') }}>
            Open
          </button>
        )}
      </div>
    </li>
  )
}

function CountCard({ label, value, sub, tone }) {
  return (
    <div className="lh-card" style={{ padding: '0.85rem 0.95rem' }}>
      <div className="lh-eyebrow">{label}</div>
      <div className="lh-mono font-bold mt-1" style={{ fontSize: 24, color: tone || 'var(--lh-text)' }}>{value}</div>
      {sub && <div className="text-[11px] mt-1" style={{ color: 'var(--lh-text-faint)' }}>{sub}</div>}
    </div>
  )
}

export default function CapabilitiesPanel() {
  const [health, setHealth] = useState(null)
  const [clis, setClis] = useState(null)
  const [tools, setTools] = useState([])
  const [skills, setSkills] = useState([])
  const [overview, setOverview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState({})

  const load = useCallback(async () => {
    const nextErrors = {}

    const read = async (key, url, fallback) => {
      try {
        const r = await fetch(url)
        if (!r.ok) throw new Error(`${url} failed`)
        return await r.json()
      } catch {
        nextErrors[key] = 'unavailable'
        return fallback
      }
    }

    const [healthData, cliData, toolData, skillData, overviewData] = await Promise.all([
      read('runtime', '/api/health', null),
      read('clis', '/api/code/clis', null),
      read('tools', '/api/tools', []),
      read('skills', '/api/skills', []),
      read('overview', '/api/overview', null),
    ])

    setHealth(healthData)
    setClis(cliData)
    setTools(asArray(toolData))
    setSkills(asArray(skillData))
    setOverview(overviewData)
    setErrors(nextErrors)
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [load])

  const installedClis = asArray(clis?.clis)
  const toolStates = useMemo(() => {
    const out = {}
    for (const tool of tools) {
      const key = tool.estado || 'unknown'
      out[key] = (out[key] || 0) + 1
    }
    return out
  }, [tools])
  const graduated = toolStates.graduada || toolStates.graduated || 0
  const quarantined = toolStates.cuarentena || toolStates.candidate || 0
  const pendingApprovals = overview?.approvals?.pending || 0
  const running = overview?.now_running?.length || 0

  const rows = [
    {
      name: 'Delegate work to local CLIs',
      status: installedClis.length ? `${installedClis.length} installed` : 'none detected',
      detail: clis ? `default: ${clis.default || 'auto'}${installedClis.length ? ` · ${installedClis.join(', ')}` : ''}` : 'CLI endpoint unavailable',
      ok: installedClis.length > 0,
      warn: clis && installedClis.length === 0,
      href: '#run',
    },
    {
      name: 'Run deterministic actions',
      status: `${tools.length} registered`,
      detail: tools.length
        ? `${Object.entries(toolStates).map(([k, v]) => `${k}: ${v}`).join(' · ')} · examples: ${tools.slice(0, 4).map((t) => t.nombre || t.name).filter(Boolean).join(', ')}`
        : 'No approved actions yet',
      ok: tools.length > 0,
      warn: tools.length === 0,
      href: '#tools',
    },
    {
      name: 'Use installed skills',
      status: `${skills.length} installed`,
      detail: `${overview?.skills?.recommended || 0} recommendations available`,
      ok: skills.length > 0,
      warn: skills.length === 0,
      href: '#skills',
    },
    {
      name: 'Respect runtime guardrails',
      status: health ? (health.allow_destructive_shell ? 'permissive' : 'guarded') : 'unknown',
      detail: health ? `write root: ${health.file_write_root || 'unrestricted'} · API ${health.api_host}:${health.api_port}` : 'Runtime endpoint unavailable',
      ok: health && !health.allow_destructive_shell,
      warn: health && health.allow_destructive_shell,
      href: '#settings',
    },
    {
      name: 'Ask for human approval',
      status: `${pendingApprovals} pending`,
      detail: pendingApprovals ? 'Actions are waiting for a decision.' : 'Decision queue is clear.',
      ok: pendingApprovals === 0,
      warn: pendingApprovals > 0,
      href: '#inbox',
    },
    {
      name: 'Track active delegations',
      status: `${running} running`,
      detail: running ? 'Agentic work is currently in flight.' : 'No delegated work is running.',
      ok: running === 0,
      warn: running > 0,
      href: '#run',
    },
  ]

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <div className="lh-card-head">
          <div>
            <div className="lh-eyebrow">Capability map</div>
            <h2 className="lh-card-title mt-1">What Lhassa can safely do</h2>
          </div>
          <button type="button" className="lh-btn" onClick={load}>Refresh</button>
        </div>
        <p className="text-xs max-w-3xl" style={{ color: 'var(--lh-text-muted)' }}>
          Availability, limits and decision gates for actions, skills, local CLIs,
          guardrails and delegated work.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <CountCard label="Agent CLIs" value={installedClis.length} sub={clis?.default ? `default ${clis.default}` : 'delegation harness'} tone={installedClis.length ? 'var(--lh-accent-text)' : undefined} />
        <CountCard label="Actions" value={tools.length} sub={`${graduated} approved · ${quarantined} candidate`} />
        <CountCard label="Skills" value={skills.length} sub={`${overview?.skills?.recommended || 0} recommended`} />
        <CountCard label="Decisions" value={pendingApprovals} sub="waiting approval" tone={pendingApprovals ? 'var(--lh-accent-text)' : undefined} />
        <CountCard label="Running" value={running} sub="delegated work" tone={running ? 'var(--lh-accent-text)' : undefined} />
      </div>

      <div className="lh-card">
        <div className="lh-card-head">
          <span className="lh-eyebrow">Availability matrix</span>
          {loading && <span className="lh-tag">loading</span>}
        </div>
        <ul className="space-y-2">
          {rows.map((row) => (
            <CapabilityRow key={row.name} {...row} />
          ))}
        </ul>
      </div>

      {Object.keys(errors).length > 0 && (
        <div className="lh-card" style={{ borderColor: 'rgba(162,62,52,0.25)' }}>
          <div className="lh-eyebrow" style={{ color: 'var(--lh-red-soft)' }}>Partial data</div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {Object.entries(errors).map(([key, value]) => (
              <span key={key} className="lh-tag">{key}: {value}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
