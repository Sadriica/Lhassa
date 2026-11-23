"""Tests for lhassa.llm.prompt; system prompt builder."""

from lhassa.llm.prompt import BASE_PROMPT, build_system_prompt


# ── Base prompt ────────────────────────────────────────────────────────────────

def test_base_prompt_not_empty():
    assert len(BASE_PROMPT) > 100


def test_base_prompt_no_markdown_bullet():
    """BASE_PROMPT should be voice-friendly (already advises against markdown)."""
    assert "No markdown" in BASE_PROMPT


# ── build_system_prompt; default (English) ────────────────────────────────────

def test_default_contains_base():
    result = build_system_prompt()
    assert BASE_PROMPT in result


def test_default_no_language_injection():
    result = build_system_prompt()
    assert "Language instruction" not in result


def test_default_no_memory_section():
    result = build_system_prompt()
    assert "---" not in result


# ── Language injection ─────────────────────────────────────────────────────────

def test_spanish_injection():
    result = build_system_prompt(stt_language="es")
    assert "Language instruction" in result
    assert "Spanish" in result


def test_french_injection():
    result = build_system_prompt(stt_language="fr")
    assert "French" in result


def test_german_injection():
    result = build_system_prompt(stt_language="de")
    assert "German" in result


def test_portuguese_injection():
    result = build_system_prompt(stt_language="pt")
    assert "Portuguese" in result


def test_italian_injection():
    result = build_system_prompt(stt_language="it")
    assert "Italian" in result


def test_dutch_injection():
    result = build_system_prompt(stt_language="nl")
    assert "Dutch" in result


def test_japanese_injection():
    result = build_system_prompt(stt_language="ja")
    assert "Japanese" in result


def test_chinese_injection():
    result = build_system_prompt(stt_language="zh")
    assert "Chinese" in result


# ── BCP-47 subtag normalisation ────────────────────────────────────────────────

def test_bcp47_es_419():
    """es-419 (Latin American Spanish) should resolve to the same Spanish instruction."""
    result = build_system_prompt(stt_language="es-419")
    assert "Spanish" in result


def test_bcp47_pt_br():
    result = build_system_prompt(stt_language="pt-BR")
    assert "Portuguese" in result


def test_bcp47_en_gb_no_injection():
    """en-GB is still English; no language instruction expected."""
    result = build_system_prompt(stt_language="en-GB")
    assert "Language instruction" not in result


# ── Unknown language code ──────────────────────────────────────────────────────

def test_unknown_language_generic_fallback():
    result = build_system_prompt(stt_language="sw")
    assert "Language instruction" in result
    assert "sw" in result


# ── Memory context ─────────────────────────────────────────────────────────────

def test_memory_context_included():
    ctx = "## What you know about the user\n- name: Alice"
    result = build_system_prompt(memory_context=ctx)
    assert ctx in result
    assert "---" in result


def test_memory_context_none_no_separator():
    result = build_system_prompt(memory_context=None)
    assert "---" not in result


def test_memory_context_empty_string_no_separator():
    result = build_system_prompt(memory_context="")
    assert "---" not in result


# ── Combined: language + memory ────────────────────────────────────────────────

def test_spanish_with_memory():
    ctx = "## What you know about the user\n- name: Alice"
    result = build_system_prompt(memory_context=ctx, stt_language="es")
    assert "Spanish" in result
    assert "Alice" in result
    # Language instruction comes before memory section
    lang_pos = result.index("Language instruction")
    mem_pos = result.index("---")
    assert lang_pos < mem_pos
