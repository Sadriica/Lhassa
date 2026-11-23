#!/usr/bin/env python3
"""
Lhassa pre-flight check; validates your environment before running `make run`.

Usage:
  uv run python scripts/check.py           # quick checks (no API calls)
  uv run python scripts/check.py --full    # also validates API keys with live requests
"""

import os
import platform
import shutil
import sys
from pathlib import Path

# ── Colour helpers ─────────────────────────────────────────────────────────────

def _ok(msg: str):   print(f"  \033[32m✓\033[0m  {msg}")
def _warn(msg: str): print(f"  \033[33m⚠\033[0m  {msg}")
def _fail(msg: str): print(f"  \033[31m✗\033[0m  {msg}")
def _info(msg: str): print(f"  \033[2m→\033[0m  {msg}")
def _head(msg: str): print(f"\n\033[1m{msg}\033[0m")

errors = 0
warnings = 0

def ok(msg):
    _ok(msg)

def warn(msg):
    global warnings
    warnings += 1
    _warn(msg)

def fail(msg):
    global errors
    errors += 1
    _fail(msg)


# ── Load .env ──────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
env_file = ROOT / ".env"
if env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(env_file)
    ok(f".env loaded from {env_file}")
else:
    warn(".env not found; copy .env.example to .env and fill in your keys")


# ── 1. Python version ──────────────────────────────────────────────────────────

_head("Python")
major, minor = sys.version_info[:2]
if (major, minor) >= (3, 13):
    ok(f"Python {major}.{minor} ✓")
else:
    fail(f"Python {major}.{minor}; Lhassa requires 3.13 or newer")


# ── 2. Node.js ─────────────────────────────────────────────────────────────────

_head("Node.js")
node = shutil.which("node")
if node:
    import subprocess
    v = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    major_node = int(v.lstrip("v").split(".")[0])
    if major_node >= 18:
        ok(f"Node.js {v} ✓")
    else:
        fail(f"Node.js {v}; Lhassa requires 18 or newer")
else:
    fail("node not found in PATH; install Node.js 18+")


# ── 3. Dashboard ───────────────────────────────────────────────────────────────

_head("Dashboard")
dist = ROOT / "dashboard" / "dist"
if dist.exists() and any(dist.iterdir()):
    ok("Dashboard built (dashboard/dist/ exists)")
else:
    warn("Dashboard not built; run  make dashboard  to build it")
    _info("Lhassa still works without the dashboard; it's optional")


# ── 4. Required env vars ───────────────────────────────────────────────────────

_head("Required API keys")

deepgram = os.getenv("DEEPGRAM_API_KEY", "")
cartesia  = os.getenv("CARTESIA_API_KEY", "")

if deepgram:
    ok("DEEPGRAM_API_KEY is set")
else:
    fail("DEEPGRAM_API_KEY is missing; sign up at console.deepgram.com")

if cartesia:
    ok("CARTESIA_API_KEY is set")
else:
    fail("CARTESIA_API_KEY is missing; sign up at console.cartesia.ai")

_head("LLM provider")

llm_keys = {
    "ANTHROPIC_API_KEY":  "anthropic",
    "OPENAI_API_KEY":     "openai",
    "GROQ_API_KEY":       "groq",
    "OPENROUTER_API_KEY": "openrouter",
    "CEREBRAS_API_KEY":   "cerebras",
}
found_llm = [(env, prov) for env, prov in llm_keys.items() if os.getenv(env)]
ollama_explicit = os.getenv("LHASSA_LLM_PROVIDER", "").lower() == "ollama"

if found_llm:
    for env, prov in found_llm:
        ok(f"{env} is set  →  provider: {prov}")
    if len(found_llm) > 1:
        first_env, first_prov = found_llm[0]
        _info(f"Multiple LLM keys found; {first_prov} wins (auto-detection priority order)")
elif ollama_explicit:
    ok("LHASSA_LLM_PROVIDER=ollama; no API key required")
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    _info(f"Ollama URL: {ollama_url}")
else:
    fail(
        "No LLM API key found. Set one of: GROQ_API_KEY, ANTHROPIC_API_KEY, "
        "OPENAI_API_KEY, OPENROUTER_API_KEY, CEREBRAS_API_KEY"
    )
    _info("Groq has a free tier; sign up at console.groq.com")


# ── 5. Optional features ───────────────────────────────────────────────────────

_head("Optional features")

brave = os.getenv("BRAVE_API_KEY", "")
if brave:
    ok("BRAVE_API_KEY set; web search uses Brave")
else:
    _info("BRAVE_API_KEY not set; web search falls back to DuckDuckGo (fine)")

vision_capable = {"anthropic", "openai", "openrouter"}
active_prov = (found_llm[0][1] if found_llm else None)
has_vision_key = (
    active_prov in vision_capable
    or bool(os.getenv("ANTHROPIC_API_KEY"))
    or bool(os.getenv("OPENAI_API_KEY"))
)
if has_vision_key:
    ok("Vision tools (analyze_screen, analyze_image); enabled")
else:
    warn("Vision tools disabled; set ANTHROPIC_API_KEY or OPENAI_API_KEY to enable")

github_token = os.getenv("GITHUB_TOKEN", "")
if github_token:
    ok("GITHUB_TOKEN set. GitHub tools enabled")
else:
    _info("GITHUB_TOKEN not set. GitHub tools disabled")

ms_tenant = os.getenv("MS_TENANT_ID", "")
ms_client = os.getenv("MS_CLIENT_ID", "")
if ms_tenant and ms_client:
    ok("MS_TENANT_ID + MS_CLIENT_ID set. Outlook tools enabled")
elif ms_tenant or ms_client:
    warn("Outlook: only one of MS_TENANT_ID / MS_CLIENT_ID is set; both are required")
else:
    _info("MS_TENANT_ID / MS_CLIENT_ID not set. Outlook tools disabled")


# ── 6. Audio devices ───────────────────────────────────────────────────────────

_head("Audio")
try:
    import pyaudio
    p = pyaudio.PyAudio()
    devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            devices.append((i, info["name"]))
    p.terminate()

    if devices:
        ok(f"{len(devices)} input device(s) found:")
        for idx, name in devices:
            _info(f"[{idx}] {name}")
    else:
        fail("No audio input devices found; check your microphone")

    mic_name = os.getenv("LHASSA_MIC_NAME", "")
    if mic_name:
        matched = [(i, n) for i, n in devices if mic_name.lower() in n.lower()]
        if matched:
            ok(f"LHASSA_MIC_NAME={mic_name!r} matches: [{matched[0][0]}] {matched[0][1]}")
        else:
            warn(
                f"LHASSA_MIC_NAME={mic_name!r} did not match any device. "
                "Lhassa will fall back to the system default."
            )
    else:
        _info("LHASSA_MIC_NAME not set; using system default input device")

except ImportError:
    warn("pyaudio not installed; run  make install  first")


# ── 7. Claude CLI (agent backend) ─────────────────────────────────────────────

_head("Agent backend")
claude_bin = shutil.which("claude")
agent_backend_env = os.getenv("LHASSA_AGENT_BACKEND", "").lower()

if agent_backend_env == "builtin":
    ok("LHASSA_AGENT_BACKEND=builtin; built-in LLM tool loop")
elif agent_backend_env == "claude_code":
    if claude_bin:
        ok(f"LHASSA_AGENT_BACKEND=claude_code; claude found at {claude_bin}")
    else:
        fail(
            "LHASSA_AGENT_BACKEND=claude_code but 'claude' not found in PATH. "
            "Install Claude Code or set LHASSA_AGENT_BACKEND=builtin"
        )
elif claude_bin:
    ok(f"claude CLI found at {claude_bin}; agents will use claude_code (auto-detected)")
    agent_dir = os.getenv("LHASSA_AGENT_DIR", "")
    if agent_dir:
        if Path(agent_dir).exists():
            ok(f"LHASSA_AGENT_DIR={agent_dir!r} ✓")
        else:
            warn(f"LHASSA_AGENT_DIR={agent_dir!r} does not exist")
    else:
        _info(f"LHASSA_AGENT_DIR not set; agents will run in {os.getcwd()}")
else:
    ok("claude CLI not found; agents will use builtin LLM tool loop (auto-detected)")


# ── 8. Screenshot tool (for analyze_screen) ───────────────────────────────────

_head("Vision / screenshots")
system = platform.system()

if system == "Darwin":
    if shutil.which("screencapture"):
        ok("screencapture found (macOS built-in); analyze_screen enabled")
    else:
        warn("screencapture not found; unexpected on macOS")
elif system == "Windows":
    _info("Windows: screenshots use PowerShell or Pillow")
    try:
        from PIL import ImageGrab  # noqa: F401
        ok("Pillow installed; screenshots will work")
    except ImportError:
        _info("Pillow not installed. PowerShell fallback will be used")
else:  # Linux
    scrot_tools = ["scrot", "maim", "import", "gnome-screenshot", "spectacle"]
    found_scrot = [t for t in scrot_tools if shutil.which(t)]
    if found_scrot:
        ok(f"Screenshot tool found: {found_scrot[0]}")
    else:
        warn(
            "No screenshot tool found; install scrot, maim, or ImageMagick "
            "to enable analyze_screen"
        )


# ── 9. Live API validation (--full only) ───────────────────────────────────────

if "--full" in sys.argv:
    _head("API key validation (--full)")

    import asyncio
    import httpx

    async def _check_keys():
        async with httpx.AsyncClient(timeout=8) as client:

            if deepgram:
                try:
                    r = await client.get(
                        "https://api.deepgram.com/v1/projects",
                        headers={"Authorization": f"Token {deepgram}"},
                    )
                    if r.status_code == 200:
                        ok("DEEPGRAM_API_KEY is valid")
                    else:
                        fail(f"DEEPGRAM_API_KEY rejected (HTTP {r.status_code})")
                except Exception as e:
                    warn(f"Could not validate DEEPGRAM_API_KEY: {e}")

            if cartesia:
                try:
                    r = await client.get(
                        "https://api.cartesia.ai/voices",
                        headers={"X-API-Key": cartesia, "Cartesia-Version": "2024-06-10"},
                    )
                    if r.status_code == 200:
                        ok("CARTESIA_API_KEY is valid")
                    else:
                        fail(f"CARTESIA_API_KEY rejected (HTTP {r.status_code})")
                except Exception as e:
                    warn(f"Could not validate CARTESIA_API_KEY: {e}")

            groq_key = os.getenv("GROQ_API_KEY", "")
            if groq_key:
                try:
                    r = await client.get(
                        "https://api.groq.com/openai/v1/models",
                        headers={"Authorization": f"Bearer {groq_key}"},
                    )
                    if r.status_code == 200:
                        ok("GROQ_API_KEY is valid")
                    else:
                        fail(f"GROQ_API_KEY rejected (HTTP {r.status_code})")
                except Exception as e:
                    warn(f"Could not validate GROQ_API_KEY: {e}")

    asyncio.run(_check_keys())


# ── Summary ────────────────────────────────────────────────────────────────────

print()
if errors == 0 and warnings == 0:
    print("\033[32m✓ All checks passed. Run  make run  to start Lhassa.\033[0m")
elif errors == 0:
    print(f"\033[33m⚠ {warnings} warning(s). Lhassa will start but some features may be limited.\033[0m")
    print("  Run  make run  to start.")
else:
    print(f"\033[31m✗ {errors} error(s), {warnings} warning(s). Fix the errors above before running Lhassa.\033[0m")
    sys.exit(1)
