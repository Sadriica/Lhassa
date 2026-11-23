# Security

Lhassa's security model; deny-by-default allowlist, Ed25519-signed tasks,
quarantine for self-written code, human-in-the-loop approvals; is documented
in full, including its honest limits, in [`doc/security.md`](doc/security.md).
What is public code and what stays on the owner's disk is drawn in
[`doc/boundaries.md`](doc/boundaries.md).

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/Sadriica/Lhassa/security/advisories/new)
rather than a public issue. Include the affected module, a reproduction, and
what an attacker gains. You should hear back within a week.

## Scope notes for researchers

- The brain assumes a **single trusted owner** on a machine they control; the
  Telegram surface answers exactly one chat id, and the API binds to loopback
  unless a token is configured (`LHASSA_API_TOKEN`).
- The interesting boundaries are the ones the model can touch: the action
  allowlist, the approval frontier, the PTC/quarantine sandbox, and the
  pseudonymization edge. Bypasses of any of those are in scope and very welcome.
