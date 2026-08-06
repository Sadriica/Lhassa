"""Tests for the deterministic $0 chat-intent classifier (lhassa/notify/intent.py)."""

from lhassa.notify.intent import WORK_TYPES, classify, classify_work_type


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


# ── work-type classification (Plan 4, Stream 1 — task identification) ───────


def test_work_types_are_the_documented_six():
    assert set(WORK_TYPES) == {
        "question", "research", "build", "review", "maintenance", "decision",
    }


def test_work_type_build():
    assert classify_work_type("build a script to parse the logs") == "build"
    assert classify_work_type("implement a new endpoint for skills") == "build"
    assert classify_work_type("fix the bug in the parser") == "build"
    assert classify_work_type("please refactor this module") == "build"
    assert classify_work_type("add a test for the router") == "build"
    assert classify_work_type("escribe un script para limpiar logs") == "build"
    assert classify_work_type("arregla el bug del parser") == "build"


def test_work_type_review():
    assert classify_work_type("review my pull request") == "review"
    assert classify_work_type("check this code before I merge") == "review"
    assert classify_work_type("what do you think of my approach") == "review"
    assert classify_work_type("revisa este código por favor") == "review"


def test_work_type_maintenance():
    assert classify_work_type("clean up the old log files") == "maintenance"
    assert classify_work_type("sync the calendar with the tasks") == "maintenance"
    assert classify_work_type("archive the stale skills") == "maintenance"
    assert classify_work_type("sincroniza el repositorio") == "maintenance"


def test_work_type_decision():
    assert classify_work_type("should I use SQLite or Postgres") == "decision"
    assert classify_work_type("which one is better for this job") == "decision"
    assert classify_work_type("help me decide between these two options") == "decision"
    assert classify_work_type("debería usar A o B") == "decision"


def test_work_type_research():
    assert classify_work_type("research the state of the art in embeddings") == "research"
    assert classify_work_type("investigate what changed in the API") == "research"
    assert classify_work_type("compare React and Vue for this project") == "research"
    assert classify_work_type("investiga las novedades en modelos locales") == "research"


def test_work_type_question_default():
    assert classify_work_type("what is the capital of France") == "question"
    assert classify_work_type("hola, cómo estás") == "question"
    assert classify_work_type("thanks, that helped") == "question"


def test_work_type_priority_build_over_question():
    # "fix the bug" also reads as an ask, but the specific build signal wins.
    assert classify_work_type("can you fix the bug in the login flow?") == "build"


def test_work_type_never_raises_on_bad_input():
    assert classify_work_type("") == "question"
    assert classify_work_type(None) == "question"  # type: ignore[arg-type]
    assert classify_work_type("   ") == "question"
