"""The self-card; live config + execution-modality taxonomy for the chat think.

All offline: the card is built from Config/registries alone ($0, no model, no
network); these tests pin what it must contain, what it must NEVER contain
(secret material), and that it degrades instead of raising.
"""

from types import SimpleNamespace

from lhassa.notify import selfcard


def _cfg(**over):
    base = dict(
        instance_id="inst_personal",
        llm_provider="deepseek",
        model="deepseek-v4-flash",
        telegram_provider="default",
        heartbeat_provider="claude",
        fallback_providers="claude,codex,default",
        deepseek_api_key="sk-test-secret-material",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_card_names_the_live_seats():
    card = selfcard.build(_cfg())
    assert "Lhassa v" in card
    assert "inst_personal" in card
    assert "deepseek" in card
    assert "deepseek-v4-flash" in card
    assert "claude" in card  # heartbeat seat + fallback chain
    assert "claude,codex,default" in card


def test_card_teaches_the_modality_taxonomy():
    card = selfcard.build(_cfg())
    for word in ("ACTIONS", "PTC", "API think", "Subscription CLIs", "Markers", "Org delegation"):
        assert word in card, word


def test_card_advertises_registered_allowlisted_actions():
    card = selfcard.build(_cfg())
    # sync_repo is registered AND allowlisted without approval from Phase 0;
    # run_script is the canonical approval-gated one.
    assert "sync_repo" in card
    assert "run_script" in card
    assert "[[action:" in card


def test_card_never_leaks_secret_material():
    cfg = _cfg()
    card = selfcard.build(cfg)
    assert cfg.deepseek_api_key not in card
    assert "sk-" not in card


def test_card_degrades_but_never_raises_on_a_hostile_config():
    card = selfcard.build(object())  # no attrs at all
    assert isinstance(card, str)
    assert "How you execute" in card  # the static half always survives
