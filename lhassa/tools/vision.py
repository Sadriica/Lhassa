"""
Vision tools — screenshot capture and image analysis via a vision LLM.

Screenshot capture tries (in order): scrot → maim → ImageMagick import →
gnome-screenshot → spectacle (KDE).

Image analysis uses the provider selected by LHASSA_VISION_PROVIDER:
  auto        — use the active LHASSA_LLM_PROVIDER if it supports vision;
                otherwise fall back to ANTHROPIC_API_KEY → OPENAI_API_KEY
  anthropic   — Claude Haiku (fast, cheap, excellent vision)
  openai      — GPT-4o-mini
  openrouter  — any OpenRouter vision-capable model

Default models (override with LHASSA_VISION_MODEL):
  anthropic   → claude-haiku-4-5-20251001
  openai      → gpt-4o-mini
  openrouter  → anthropic/claude-haiku-4-5-20251001
"""

import asyncio
import base64
import platform
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

# Cached async clients — reuses HTTP connection pool across calls
_clients: dict[tuple, Any] = {}

# ── Screenshot ─────────────────────────────────────────────────────────────────

_SYSTEM = platform.system()

# Platform-specific screenshot commands tried in order.
# Each entry is a callable: (output_path) -> list[str]
_SCREENSHOT_CMDS_LINUX = [
    lambda p: ["scrot", p],
    lambda p: ["maim", p],
    lambda p: ["import", "-window", "root", p],
    lambda p: ["gnome-screenshot", "-f", p],
    lambda p: ["spectacle", "-b", "-o", p],
]

_SCREENSHOT_CMDS_MACOS = [
    lambda p: ["screencapture", "-x", p],
]

# Windows fallback: try PowerShell (no external deps)
def _win_powershell_screenshot(path: str):
    import subprocess
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$s=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap($s.Width,$s.Height);"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($s.Location,[System.Drawing.Point]::Empty,$s.Size);"
        f"$bmp.Save('{path}');"
        "$g.Dispose();$bmp.Dispose()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        timeout=15, check=True, capture_output=True,
    )


async def take_screenshot() -> bytes:
    """Capture the current screen. Returns PNG bytes."""
    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name

    try:
        if _SYSTEM == "Darwin":
            cmds = _SCREENSHOT_CMDS_MACOS
        elif _SYSTEM == "Windows":
            cmds = []
        else:
            cmds = _SCREENSHOT_CMDS_LINUX

        for cmd_fn in cmds:
            cmd = cmd_fn(path)
            try:
                await asyncio.to_thread(
                    lambda c=cmd: subprocess.run(c, timeout=8, check=True, capture_output=True)
                )
                return Path(path).read_bytes()
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                continue

        if _SYSTEM == "Windows":
            try:
                await asyncio.to_thread(_win_powershell_screenshot, path)
                return Path(path).read_bytes()
            except Exception:
                pass

        # PIL fallback (works on all platforms if Pillow is installed)
        try:
            def _pil_capture():
                from PIL import ImageGrab  # type: ignore
                img = ImageGrab.grab()
                img.save(path)
            await asyncio.to_thread(_pil_capture)
            return Path(path).read_bytes()
        except Exception:
            pass

        _hints = {
            "Darwin":  "On macOS: screencapture is built-in — check PATH.",
            "Windows": "On Windows: run  pip install Pillow  to enable screenshots.",
            "Linux":   "On Linux: install scrot (apt/pacman/dnf), maim, or ImageMagick.",
        }
        hint = _hints.get(_SYSTEM, "Install scrot, maim, or ImageMagick.")
        raise RuntimeError(f"No screenshot tool found. {hint}")
    finally:
        Path(path).unlink(missing_ok=True)


# ── Provider resolution ────────────────────────────────────────────────────────

_VISION_CAPABLE = {"anthropic", "openai", "openrouter"}

_DEFAULT_MODELS = {
    "anthropic":  "claude-haiku-4-5-20251001",
    "openai":     "gpt-4o-mini",
    "openrouter": "anthropic/claude-haiku-4-5-20251001",
}


def _resolve_vision():
    """
    Return (provider, api_key, model) for vision analysis.

    Priority:
      1. Explicit LHASSA_VISION_PROVIDER (not "auto")
      2. Active LHASSA_LLM_PROVIDER if it supports vision and has a key
      3. ANTHROPIC_API_KEY
      4. OPENAI_API_KEY
    """
    from lhassa.config import Config
    config = Config()

    vp = config.vision_provider
    model_override = config.vision_model

    def _model(provider: str) -> str:
        return model_override or _DEFAULT_MODELS.get(provider, "")

    key_map = {
        "anthropic":  config.anthropic_api_key,
        "openai":     config.openai_api_key,
        "openrouter": config.openrouter_api_key,
    }

    if vp != "auto":
        key = key_map.get(vp, "")
        if not key:
            raise RuntimeError(
                f"LHASSA_VISION_PROVIDER={vp!r} but the matching API key is not set. "
                f"Add it to .env."
            )
        return vp, key, _model(vp)

    # Auto: prefer active LLM provider if vision-capable
    if config.llm_provider in _VISION_CAPABLE:
        key = key_map.get(config.llm_provider, "")
        if key:
            return config.llm_provider, key, _model(config.llm_provider)

    # Fall back to any available vision key
    if config.anthropic_api_key:
        return "anthropic", config.anthropic_api_key, _model("anthropic")
    if config.openai_api_key:
        return "openai", config.openai_api_key, _model("openai")

    raise RuntimeError(
        "Vision tools require a vision-capable API key. Options:\n"
        "  1. Set LHASSA_LLM_PROVIDER=anthropic, openai, or openrouter "
        "and add the matching API key.\n"
        "  2. Set ANTHROPIC_API_KEY or OPENAI_API_KEY independently.\n"
        "  3. Set LHASSA_VISION_PROVIDER=anthropic|openai|openrouter explicitly."
    )


# ── Image analysis ─────────────────────────────────────────────────────────────

async def analyze_image(image_bytes: bytes, question: str = "Describe what you see in detail.") -> str:
    """Send an image to the configured vision provider and return the description."""
    provider, api_key, model = _resolve_vision()
    logger.debug(f"[vision] provider={provider} model={model}")

    b64 = base64.standard_b64encode(image_bytes).decode()

    if provider == "anthropic":
        return await _analyze_anthropic(b64, question, api_key, model)
    elif provider == "openrouter":
        return await _analyze_openai_compat(b64, question, api_key, model, base_url="https://openrouter.ai/api/v1")
    else:  # openai
        return await _analyze_openai_compat(b64, question, api_key, model)


def _get_anthropic_client(api_key: str):
    key = ("anthropic", api_key)
    if key not in _clients:
        from anthropic import AsyncAnthropic
        _clients[key] = AsyncAnthropic(api_key=api_key)
    return _clients[key]


def _get_openai_client(api_key: str, base_url: str | None = None):
    key = ("openai", api_key, base_url)
    if key not in _clients:
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _clients[key] = AsyncOpenAI(**kwargs)
    return _clients[key]


async def _analyze_anthropic(b64: str, question: str, api_key: str, model: str) -> str:
    client = _get_anthropic_client(api_key)
    response = await asyncio.wait_for(
        client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": question},
                ],
            }],
        ),
        timeout=60.0,
    )
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""


async def _analyze_openai_compat(b64: str, question: str, api_key: str, model: str, base_url: str | None = None) -> str:
    client = _get_openai_client(api_key, base_url)
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": question},
                ],
            }],
        ),
        timeout=60.0,
    )
    return response.choices[0].message.content


async def analyze_image_file(path: str, question: str) -> str:
    """Analyse an image at a given file path."""
    data = Path(path).expanduser().read_bytes()
    return await analyze_image(data, question)
