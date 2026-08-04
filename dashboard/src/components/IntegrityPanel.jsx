import { useCallback, useEffect, useState } from 'react'
import { getIntegrity, getIntegrityFindings, runIntegrityCheck } from '../lib/api'
import { shortWhen } from '../lib/format'
import { EmptyState, ErrorState, MetricCard, SectionHeader } from './ui/common'

// The "Integrity" section — the conformance manifest and what it found.
// GET /api/integrity (manifest version + attestation + state + the declared
// contracts), GET /api/integrity/findings, POST /api/integrity/check.
//
// The check is the one mutation here and it is explicitly the owner's act: a
// button, never a poll. Findings are shown as they are recorded — this panel
// never softens a severity or hides a contract that failed.

const SEVERITY_ORDER = { critical: 0, high: 1, error: 1, medium: 2, warn: 3, warning: 3, low: 4, info: 5 }

function severityRank(value) {
  const key = String(value || '').toLowerCase()
  return key in SEVERITY_ORDER ? SEVERITY_ORDER[key] : 9
}

function ContractRow({ contract }) {
  return (
    <li className="lh-row">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-[12px]" style={{ color: 'var(--lh-text)' }}>{contract.id}</span>
        <div className="flex items-center gap-1.5">
          <span className="lh-badge">{contract.severity}</span>
          <span className="lh-mono text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>v{contract.version}</span>
        </div>
      </div>
      {contract.description && (
        <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>{contract.description}</div>
      )}
    </li>
  )
}

function FindingRow({ finding }) {
  return (
    <li className="lh-row">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-[12px]" style={{ color: 'var(--lh-text)' }}>
          {finding.contract_id || finding.contrato || finding.id}
        </span>
        <div className="flex items-center gap-1.5">
          {finding.severity && <span className="lh-badge">{finding.severity}</span>}
          <span className="text-[10px]" style={{ color: 'var(--lh-text-faint)' }}>
            {shortWhen(finding.creado_en || finding.created_at)}
          </span>
        </div>
      </div>
      {(finding.detail || finding.detalle || finding.message) && (
        <div className="text-[11px] mt-0.5" style={{ color: 'var(--lh-text-muted)' }}>
          {finding.detail || finding.detalle || finding.message}
        </div>
      )}
    </li>
  )
}

export default function IntegrityPanel() {
  const [manifest, setManifest] = useState(null)
  const [findings, setFindings] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)
  const [checkResult, setCheckResult] = useState('')

  const load = useCallback(async () => {
    try {
      const [m, f] = await Promise.all([getIntegrity(), getIntegrityFindings(100)])
      setManifest(m)
      setFindings(Array.isArray(f) ? f : [])
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the integrity manifest.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const check = useCallback(async () => {
    setChecking(true)
    setCheckResult('')
    try {
      const result = await runIntegrityCheck()
      const failed = result && (result.failed ?? result.findings_count ?? 0)
      setCheckResult(failed ? `${failed} finding(s) recorded.` : 'Checked — nothing new.')
      await load()
    } catch (e) {
      setCheckResult(e instanceof Error ? e.message : 'The check could not run.')
    } finally {
      setChecking(false)
    }
  }, [load])

  if (loading) {
    return (
      <div className="lh-card">
        <SectionHeader eyebrow="Conformance" title="Integrity" />
        <p className="text-xs" style={{ color: 'var(--lh-text-faint)' }}>Loading…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="lh-card">
        <SectionHeader eyebrow="Conformance" title="Integrity" />
        <ErrorState>{error}</ErrorState>
      </div>
    )
  }

  const contracts = [...(manifest?.contracts || [])].sort(
    (a, b) => severityRank(a.severity) - severityRank(b.severity)
  )
  const state = manifest?.state || {}
  const attestation = manifest?.attestation || {}

  return (
    <div className="space-y-4">
      <div className="lh-card">
        <SectionHeader
          eyebrow="Conformance"
          title="Integrity"
          actions={
            <button type="button" className="lh-btn" onClick={check} disabled={checking}>
              {checking ? 'checking…' : 'run check'}
            </button>
          }
        />
        {checkResult && (
          <p className="text-[11px] mb-2" style={{ color: 'var(--lh-text-muted)' }}>{checkResult}</p>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <MetricCard label="manifest" value={String(manifest?.manifest_version ?? '—')} />
          <MetricCard label="contracts" value={String(contracts.length)} />
          <MetricCard label="findings" value={String(findings.length)} />
          <MetricCard
            label="last checked"
            value={shortWhen(state.last_checked_at || state.checked_at) || '—'}
          />
        </div>
        {Object.keys(attestation).length > 0 && (
          <ul className="mt-3 space-y-1">
            {Object.entries(attestation).map(([key, value]) => (
              <li key={key} className="text-[11px] flex gap-2">
                <span className="lh-label w-32 shrink-0">{key}</span>
                <span className="lh-mono truncate" style={{ color: 'var(--lh-text-muted)' }}>
                  {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="lh-card">
          <SectionHeader eyebrow="Declared" title={`${contracts.length} contracts`} />
          {contracts.length === 0 ? (
            <EmptyState>No contracts declared in the manifest.</EmptyState>
          ) : (
            <ul className="space-y-1 max-h-[60vh] overflow-y-auto">
              {contracts.map((c) => <ContractRow key={c.id} contract={c} />)}
            </ul>
          )}
        </div>

        <div className="lh-card">
          <SectionHeader eyebrow="Observed" title={`${findings.length} findings`} />
          {findings.length === 0 ? (
            <EmptyState>Nothing has broken a contract yet.</EmptyState>
          ) : (
            <ul className="space-y-1 max-h-[60vh] overflow-y-auto">
              {findings.map((f, i) => <FindingRow key={f.id ?? i} finding={f} />)}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
