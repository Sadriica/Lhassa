"""Security primitives — the three walls that guard remote execution (§2.6).

Phase-0 ships the deterministic order-execution wall:
  - ``trust``     — TrustLevel + Origin (the WHO), trust degrades by contagion.
  - ``signing``   — Ed25519 authenticity (brain signs, machine verifies).
  - ``allowlist`` — deny-by-default gate, per-action HITL threshold by risk.

The quarantine wall (self-written code, §2.8) and the probabilistic
threat-awareness wall live in later phases. These three "fail differently":
deterministic allowlist + cryptographic signature + (later) sandbox.

Note: ``lhassa.tools.privileged`` is a SEPARATE, command-level (sudo) allowlist
that gates which OS binaries lhassa may run as root. This package's allowlist is
TASK-level: it gates which signed ``Task`` actions a machine will execute. They
are independent layers and intentionally kept separate.
"""
