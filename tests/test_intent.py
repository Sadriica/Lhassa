"""Tests for the deterministic $0 chat-intent classifier (lhassa/notify/intent.py)."""

from lhassa.notify.intent import classify


def test_command_bucket():
    assert classify("/help") == "command"
    assert classify("/cv some job posting") == "command"


def test_live_data_bucket_news_today_prices():
    assert classify("what's the news today") == "live_data"
    assert classify("dame las noticias de hoy") == "live_data"
    assert classify("qué precio tiene el dólar hoy") == "live_data"
    assert classify("what's the weather right now") == "live_data"
    assert classify("cuál es el clima ahora") == "live_data"
    assert classify("what's the score of the game tonight") == "live_data"
    assert classify("bitcoin price") == "live_data"


def test_live_data_wins_over_cacheable_shape():
    # Matches a "what is" cacheable prefix AND a live-data keyword — live_data
    # must win (never cache a time-sensitive answer).
    assert classify("what is the weather today") == "live_data"


def test_cacheable_stable_facts_and_howtos():
    assert classify("what is the capital of France") == "cacheable"
    assert classify("cuál es la capital de Francia") == "cacheable"
    assert classify("how do I undo a git commit") == "cacheable"
    assert classify("cómo instalo docker") == "cacheable"
    assert classify("who is the author of Don Quixote") == "cacheable"
    assert classify("explain what TCP is") == "cacheable"
    assert classify("qué es una API REST") == "cacheable"
    assert classify("define recursion") == "cacheable"


def test_judgment_bucket_default():
    assert classify("I think we should refactor this module") == "judgment"
    assert classify("remind me to call mom") == "judgment"
    assert classify("hola, cómo estás") == "judgment"
    assert classify("do you think I should take this job") == "judgment"


def test_never_raises_on_bad_input():
    assert classify("") == "judgment"
    assert classify(None) == "judgment"  # type: ignore[arg-type]
    assert classify("   ") == "judgment"
