"""Ed25519 signing for remote execution (§2.6) — "firmar > cifrar".

Authenticity, not secrecy: the channel is a TLS/WireGuard tunnel; the signature
proves a ``Task`` really came from the brain and was not tampered with. The
brain holds the PRIVATE key; a machine holds the PUBLIC key. Local-first today
means both live on this box, but the split is real in the API.

Key management: a private key is loaded from ``Config.signing_key_path`` (default
``~/.lhassa/keys/ed25519``), or generated on first use with its public key
written alongside as ``<path>.pub``. ``KEY_PATH`` is module-level so tests can
point it at a tmp dir (monkeypatch), exactly like ``core.db.DB_PATH``.

We do not roll our own crypto (principle #8): this is the ``cryptography``
library's Ed25519, already a project dependency.
"""

import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519

from lhassa.config import Config

# Module-level so tests can redirect key storage to a tmp path via monkeypatch.
KEY_PATH = Path(Config.signing_key_path)

_PREFIX = "ed25519:"


# ── key files ─────────────────────────────────────────────────────────────────

def _public_path(priv: Path) -> Path:
    """Public-key path that sits alongside the private key (``<name>.pub``)."""
    return priv.with_name(priv.name + ".pub")


def _load_or_generate_private_key() -> ed25519.Ed25519PrivateKey:
    """Load the private key from ``KEY_PATH``, generating a keypair on first use.

    Stored as raw 32-byte seeds. The private key file is chmod 0600 (best effort).
    """
    priv_path = KEY_PATH
    if priv_path.exists():
        return ed25519.Ed25519PrivateKey.from_private_bytes(priv_path.read_bytes())

    key = ed25519.Ed25519PrivateKey.generate()
    priv_path.parent.mkdir(parents=True, exist_ok=True)
    priv_path.write_bytes(key.private_bytes_raw())
    try:
        os.chmod(priv_path, 0o600)
    except OSError:
        pass  # non-POSIX filesystem; the tunnel + allowlist remain the guard
    _public_path(priv_path).write_bytes(key.public_key().public_bytes_raw())
    return key


def load_public_key() -> ed25519.Ed25519PublicKey:
    """Return the public key (the machine's half), generating the keypair if needed."""
    pub_path = _public_path(KEY_PATH)
    if pub_path.exists():
        return ed25519.Ed25519PublicKey.from_public_bytes(pub_path.read_bytes())
    return _load_or_generate_private_key().public_key()


def public_key_hex() -> str:
    """The public key as hex — shareable with a machine to pin the brain's identity."""
    return load_public_key().public_bytes_raw().hex()


def _coerce_public_key(public_key) -> ed25519.Ed25519PublicKey:
    """Accept an Ed25519PublicKey or a (possibly prefixed) hex string."""
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        return public_key
    if isinstance(public_key, str):
        raw = bytes.fromhex(public_key.removeprefix(_PREFIX))
        return ed25519.Ed25519PublicKey.from_public_bytes(raw)
    raise TypeError(f"unsupported public_key type: {type(public_key)!r}")


# ── sign / verify ───────────────────────────────────────────────────────────────

def sign(data: bytes) -> str:
    """Sign raw bytes with the brain's private key. Returns ``"ed25519:<hex>"``."""
    key = _load_or_generate_private_key()
    return f"{_PREFIX}{key.sign(data).hex()}"


def verify(data: bytes, signature: str, public_key) -> bool:
    """Verify ``signature`` over ``data`` with ``public_key``.

    Tolerates the ``ed25519:`` prefix (and accepts a hex-string public key).
    Returns ``False`` on any error — bad signature, garbage input, wrong type —
    and NEVER raises (the allowlist gate must be able to call it on hostile data).
    """
    try:
        sig_hex = signature.removeprefix(_PREFIX) if signature.startswith(_PREFIX) else signature
        sig = bytes.fromhex(sig_hex)
        _coerce_public_key(public_key).verify(sig, data)
        return True
    except Exception:
        return False


# ── task helpers ────────────────────────────────────────────────────────────────

def sign_task(task) -> str:
    """Sign a ``Task`` over its ``canonical_bytes()``. Returns the signature string."""
    return sign(task.canonical_bytes())


def verify_task(task, public_key) -> bool:
    """Verify a ``Task``'s ``firma`` against its ``canonical_bytes()``. Never raises."""
    return verify(task.canonical_bytes(), task.firma, public_key)
