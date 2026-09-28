# Integrity Watcher; executable Witara/Lhassa contracts

The ordinary monitor watches operational transitions: failures, health, runs
and approvals. Integrity watches a different question:

> Is the working system still obeying the architecture, evidence, sovereignty
> and approval rules it claims to embody?

It is deterministic, local and LLM-free. The watcher runs it on its own slower
cadence (`LHASSA_INTEGRITY_INTERVAL`, one hour by default). It records evidence
even when everything passes and only wakes notification/delegation paths on a
finding.

## Versioned manifest

Manifest version 1 contains:

| Contract | Severity | Invariant |
|---|---|---|
| `db.schema.current` | critical | The grande is on the supported forward migration. |
| `research.event_chain` | critical | Assessment state is reconstructible from a contiguous append-only stream; deep research earned its disposition. |
| `research.artifacts.present` | warn | Research/adoption artifact paths still exist. |
| `research.flows.canonical` | critical | Theory screening and corpus flows match the versioned method. |
| `product.adoption.chain` | critical | External workspace, enforceable isolation and both approvals remain provable. |
| `security.quarantine.available` | critical | A real namespace sandbox is available; best-effort is not conformity. |
| `security.governance.config` | critical | Money/deletion remain always-approved and autonomy cannot be disabled by an invalid threshold. |
| `runtime.capabilities.wired` | critical | Required research, org, temple actions and objective verifiers are registered. |
| `architecture.import_boundaries` | critical | Core, interfaces and adapters do not reach into world-facing shells. |
| `operations.approvals.fresh` | warn | Pending approvals have not silently aged past 72 hours. |

The registry is intentionally small and executable. A new philosophical claim
becomes a contract only when code can observe evidence for it.

## Persistence

Migration 10 adds:

- `conformance_runs`: one complete manifest execution and its attestation.
- `conformance_checks`: every contract verdict and evidence, append-only.
- `conformance_findings`: every failed verdict, append-only.
- `conformance_state`: current projection per contract for fast surfaces.

A warning is not painted green. Only an entirely passing run mints an
attestation. The default TTL is two hours
(`LHASSA_INTEGRITY_ATTESTATION_TTL`).

Product integration is fail-closed by default
(`LHASSA_INTEGRITY_ENFORCE_PRODUCT_CHANGES=1`): `apply-change` requires a fresh
attestation from the current manifest. Read-only/routine operation remains
available when integrity is degraded.

## Surfaces

```bash
lhassa integrity contracts
lhassa integrity check
lhassa integrity status
lhassa integrity findings
```

Read/run API:

```text
GET  /api/integrity
POST /api/integrity/check
GET  /api/integrity/findings
```

The main `lhassa status` overview includes the current attestation.

## Reaction policy

- pass: append evidence and issue/refresh the attestation;
- warn: append a finding, withhold attestation, notify on transition;
- critical: append a finding, alert, and block only governed dangerous
  transitions such as product integration;
- remediation: report or mint normal work; never silently rewrite the system.

The watcher itself is observed by the attestation expiry. If it stops producing
green runs, its previous permission naturally expires.
