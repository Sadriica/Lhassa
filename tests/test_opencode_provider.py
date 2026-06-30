"""Tests for the opencode-CLI heartbeat provider and its router wiring (S1).

Fully offline: the subprocess is mocked via the injected ``runner`` seam, so no
real ``opencode`` process is ever spawned. The router is exercised with a minimal
config double and a monkeypatched ``shutil.which`` for binary availability.
"""

import asyncio

import pytest

from lhassa.llm import router
from lhassa.llm.provider import (
    Completion,
    OpenCodeProvider,
    PseudonymizingProvider,
    ProviderNotConfigured,
    _parse_opencode_json,
    get_model_provider,
)


# ── Config double ─────────────────────────────────────────────────────────────


class FakeConfig:
    def __init__(
        self,
        *,
        llm_provider="groq",
        model="cheap-model",
        model_cheap="cheap-model",
        model_smart="smart-model",
        router_mode="rules",
        local_model_url="",
        local_model="local-llama",
        heartbeat_provider="auto",
        pseudonymize_remote=False,
        opencode_bin="opencode",
        opencode_model="opencode/deepseek-v4-flash-free",
    ):
        self.llm_provider = llm_provider
        self.model = model
        self.model_cheap = model_cheap
        self.model_smart = model_smart
        self.router_mode = router_mode
        self.local_model_url = local_model_url
        self.local_model = local_model
        self.heartbeat_provider = heartbeat_provider
        self.pseudonymize_remote = pseudonymize_remote
        self.opencode_bin = opencode_bin
        self.opencode_model = opencode_model


def _runner(returncode=0, stdout=b"", stderr=b"", *, capture=None, exc=None):
    """Build an async subprocess-seam stand-in returning canned bytes."""

    async def run(argv):
        if capture is not None:
            capture["argv"] = argv
        if exc is not None:
            raise exc
        return returncode, stdout, stderr

    return run


# A plausible opencode ``--format json`` event stream (schema is [SUPUESTO]):
# a streamed text part, the final assistant message, then a finish event with
# token usage + cost.
SAMPLE_STREAM = (
    b'{"type":"message.part","part":{"text":"Hel"}}\n'
    b'{"type":"message.part","part":{"text":"lo"}}\n'
    b'{"type":"message","role":"assistant","parts":[{"type":"text","text":"Hello there, all set."}]}\n'
    b'{"type":"step.finish","tokens":{"input":18,"output":6},"cost":0.00042}\n'
)


# ── OpenCodeProvider.complete ─────────────────────────────────────────────────


async def test_opencode_success_parses_text_and_cost():
    cap = {}
    cfg = FakeConfig()
    prov = OpenCodeProvider(cfg, runner=_runner(0, SAMPLE_STREAM, capture=cap))

    assert prov.name == "opencode"
    assert prov.zero_retention is False
    assert prov.is_local is False

    result = await prov.complete(
        [{"role": "user", "content": "hi"}], system="be brief"
    )
    assert isinstance(result, Completion)
    assert result.text == "Hello there, all set."  # final message wins over deltas
    assert result.model == "opencode/deepseek-v4-flash-free"
    assert result.input_tokens == 18
    assert result.output_tokens == 6
    assert result.cost_usd == pytest.approx(0.00042)

    # argv shape + model selection + system prepend into the prompt.
    argv = cap["argv"]
    assert argv[:6] == ["opencode", "run", "--pure", "--format", "json", "-m"]
    assert argv[6] == "opencode/deepseek-v4-flash-free"
    prompt = argv[7]
    assert "be brief" in prompt
    assert "hi" in prompt


async def test_opencode_explicit_model_overrides_config():
    cap = {}
    prov = OpenCodeProvider(
        FakeConfig(), runner=_runner(0, SAMPLE_STREAM, capture=cap)
    )
    result = await prov.complete(
        [{"role": "user", "content": "hi"}], model="opencode/other"
    )
    assert result.model == "opencode/other"
    assert cap["argv"][6] == "opencode/other"


async def test_opencode_real_ndjson_schema_verified_live():
    # The exact stdout opencode emits (captured from a live `--pure` run): text is
    # nested under part.text on a "text" event; the step_finish event nests the
    # totals under part.tokens / part.cost.
    stream = (
        b'{"type":"step_start","part":{"type":"step-start"}}\n'
        b'{"type":"text","part":{"type":"text","text":"pong"}}\n'
        b'{"type":"step_finish","part":{"type":"step-finish",'
        b'"tokens":{"total":11168,"input":11153,"output":3},"cost":0}}\n'
    )
    prov = OpenCodeProvider(FakeConfig(), runner=_runner(0, stream))
    result = await prov.complete([{"role": "user", "content": "ping"}])
    assert result.text == "pong"
    assert result.input_tokens == 11153
    assert result.output_tokens == 3
    assert result.cost_usd == 0.0


async def test_opencode_delta_only_stream_concatenates():
    stream = (
        b'{"part":{"text":"foo "}}\n'
        b'{"part":{"text":"bar"}}\n'
    )
    prov = OpenCodeProvider(FakeConfig(), runner=_runner(0, stream))
    result = await prov.complete([{"role": "user", "content": "hi"}])
    assert result.text == "foo bar"
    assert result.cost_usd == 0.0


async def test_opencode_non_json_falls_back_to_cleaned_stdout():
    prov = OpenCodeProvider(
        FakeConfig(), runner=_runner(0, b"\x1b[2mplain answer\x1b[0m\n")
    )
    result = await prov.complete([{"role": "user", "content": "hi"}])
    assert result.text == "plain answer"


async def test_opencode_nonzero_exit_raises():
    prov = OpenCodeProvider(
        FakeConfig(), runner=_runner(1, b"", b"boom: model not found")
    )
    with pytest.raises(ProviderNotConfigured, match="exit 1"):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_opencode_missing_binary_raises():
    prov = OpenCodeProvider(
        FakeConfig(), runner=_runner(exc=FileNotFoundError("opencode"))
    )
    with pytest.raises(ProviderNotConfigured, match="not found"):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_opencode_timeout_raises():
    prov = OpenCodeProvider(
        FakeConfig(), runner=_runner(exc=asyncio.TimeoutError())
    )
    with pytest.raises(ProviderNotConfigured, match="timed out"):
        await prov.complete([{"role": "user", "content": "hi"}])


async def test_opencode_empty_output_raises():
    prov = OpenCodeProvider(FakeConfig(), runner=_runner(0, b"   \n"))
    with pytest.raises(ProviderNotConfigured, match="empty output"):
        await prov.complete([{"role": "user", "content": "hi"}])


def test_get_model_provider_opencode():
    prov = get_model_provider(FakeConfig(), name="opencode")
    assert isinstance(prov, OpenCodeProvider)
    assert prov.is_local is False


def test_parse_opencode_json_variants():
    # Single JSON object with flat content.
    text, cost, i, o = _parse_opencode_json('{"content":"answer"}')
    assert text == "answer"
    # Array of events with usage spelled prompt/completion + top-level cost.
    text, cost, i, o = _parse_opencode_json(
        '[{"text":"yo"},{"usage":{"prompt_tokens":3,"completion_tokens":2},"cost":0.5}]'
    )
    assert text == "yo"
    assert (i, o) == (3, 2)
    assert cost == 0.5
    # Garbage → empty (no JSON parsed → cleaned-stdout fallback handled in caller).
    text, _, _, _ = _parse_opencode_json("not json at all")
    assert text == "not json at all"


# ── Router wiring ─────────────────────────────────────────────────────────────


def test_router_heartbeat_opencode_uses_opencode_wrapped(monkeypatch):
    # heartbeat=opencode + pseudonymize → OpenCodeProvider wrapped (cloud rule).
    cfg = FakeConfig(heartbeat_provider="opencode", pseudonymize_remote=True)
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert isinstance(prov, PseudonymizingProvider)
    assert prov.name == "opencode"
    assert prov.is_local is False


def test_router_heartbeat_opencode_unwrapped_without_pseudonymize():
    cfg = FakeConfig(heartbeat_provider="opencode", pseudonymize_remote=False)
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert isinstance(prov, OpenCodeProvider)
    assert prov.name == "opencode"


def test_router_auto_prefers_opencode_when_present(monkeypatch):
    monkeypatch.setattr(router.shutil, "which", lambda b: "/usr/bin/opencode")
    cfg = FakeConfig(heartbeat_provider="auto", local_model_url="")
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert prov.name == "opencode"


def test_router_auto_no_opencode_falls_to_cloud(monkeypatch):
    monkeypatch.setattr(router.shutil, "which", lambda b: None)
    cfg = FakeConfig(heartbeat_provider="auto", local_model_url="")
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert prov.name == "default"


def test_router_auto_local_beats_opencode(monkeypatch):
    # local_model_url wins over opencode in the auto precedence.
    monkeypatch.setattr(router.shutil, "which", lambda b: "/usr/bin/opencode")
    cfg = FakeConfig(
        heartbeat_provider="auto", local_model_url="http://127.0.0.1:11434/v1"
    )
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert prov.is_local is True
    assert prov.name == "local"


def test_router_cloud_never_opencode(monkeypatch):
    monkeypatch.setattr(router.shutil, "which", lambda b: "/usr/bin/opencode")
    cfg = FakeConfig(heartbeat_provider="cloud", local_model_url="")
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert prov.name == "default"


def test_router_hard_path_never_opencode(monkeypatch):
    monkeypatch.setattr(router.shutil, "which", lambda b: "/usr/bin/opencode")
    cfg = FakeConfig(heartbeat_provider="opencode", local_model_url="")
    prov = router.pick_provider(sensitive=False, config=cfg, hard=True)
    assert getattr(prov, "is_local", False) is False
    assert prov.name == "default"


def test_pick_model_opencode_uses_configured_model(monkeypatch):
    cfg = FakeConfig(heartbeat_provider="opencode", pseudonymize_remote=False)
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert router.pick_model(prov, hard=False, config=cfg) == (
        "opencode/deepseek-v4-flash-free"
    )


def test_pick_model_opencode_through_wrapper(monkeypatch):
    # The name proxies through PseudonymizingProvider, so pick_model still resolves.
    cfg = FakeConfig(heartbeat_provider="opencode", pseudonymize_remote=True)
    prov = router.pick_provider(sensitive=False, config=cfg, hard=False)
    assert isinstance(prov, PseudonymizingProvider)
    assert router.pick_model(prov, hard=False, config=cfg) == (
        "opencode/deepseek-v4-flash-free"
    )


def test_decide_routine_routes_opencode(monkeypatch):
    monkeypatch.setattr(router.shutil, "which", lambda b: "/usr/bin/opencode")
    cfg = FakeConfig(heartbeat_provider="auto", local_model_url="")

    class T:
        kind = "task"
        params: dict = {}

    d = router.decide(T(), None, config=cfg)
    assert d.kind == "think"
    assert d.provider.name == "opencode"
    assert d.model == "opencode/deepseek-v4-flash-free"
