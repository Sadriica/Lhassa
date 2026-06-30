"""Tests for the pseudonymization hook (lhassa/privacy.py).

Fully offline, pure stdlib: round-trips, determinism, and passthrough on bad
input. No LLM, no network.
"""

from lhassa.privacy import (
    depseudonymize,
    pseudonymize,
    pseudonymize_messages,
)


def test_pseudonymize_scrubs_email():
    text = "Contact me at jane.doe@example.com please"
    scrubbed, mapping = pseudonymize(text)
    assert "jane.doe@example.com" not in scrubbed
    assert "<email_1>" in scrubbed
    assert mapping == {"<email_1>": "jane.doe@example.com"}


def test_pseudonymize_scrubs_phone_ip_secret():
    text = (
        "call +1 (415) 555-2671, server 192.168.1.42, "
        "key sk-abcdef0123456789ABCDEF"
    )
    scrubbed, mapping = pseudonymize(text)
    assert "192.168.1.42" not in scrubbed
    assert "sk-abcdef0123456789ABCDEF" not in scrubbed
    assert "415" not in scrubbed
    kinds = {ph.strip("<>").rsplit("_", 1)[0] for ph in mapping}
    assert {"phone", "ip", "secret"} <= kinds


def test_round_trip_restores_original():
    text = "email a@b.com and a@b.com again, ip 10.0.0.1"
    scrubbed, mapping = pseudonymize(text)
    assert depseudonymize(scrubbed, mapping) == text


def test_repeated_value_reuses_placeholder():
    text = "a@b.com / a@b.com / c@d.com"
    scrubbed, mapping = pseudonymize(text)
    # Two distinct emails → two placeholders; the repeat reuses the first.
    assert len(mapping) == 2
    assert scrubbed.count("<email_1>") == 2
    assert scrubbed.count("<email_2>") == 1


def test_deterministic():
    text = "x@y.com talks to z@w.com"
    a, ma = pseudonymize(text)
    b, mb = pseudonymize(text)
    assert a == b
    assert ma == mb


def test_no_pii_is_unchanged():
    text = "just a normal sentence with no secrets"
    scrubbed, mapping = pseudonymize(text)
    assert scrubbed == text
    assert mapping == {}


def test_pseudonymize_messages_shares_mapping():
    messages = [
        {"role": "system", "content": "user email is bob@corp.com"},
        {"role": "user", "content": "remind bob@corp.com about the IP 8.8.8.8"},
    ]
    scrubbed, mapping = pseudonymize_messages(messages)
    # Same email across two messages → one shared placeholder.
    email_phs = [ph for ph in mapping if ph.startswith("<email")]
    assert len(email_phs) == 1
    ph = email_phs[0]
    assert ph in scrubbed[0]["content"]
    assert ph in scrubbed[1]["content"]
    # Original messages are not mutated.
    assert messages[0]["content"] == "user email is bob@corp.com"


def test_pseudonymize_messages_passes_through_non_string():
    messages = [
        {"role": "user", "content": [{"type": "image"}]},
        "not-a-dict",
        {"role": "user", "content": "mail q@z.com"},
    ]
    scrubbed, mapping = pseudonymize_messages(messages)
    assert scrubbed[0]["content"] == [{"type": "image"}]
    assert scrubbed[1] == "not-a-dict"
    assert "<email_1>" in scrubbed[2]["content"]


def test_degrades_to_passthrough_on_bad_input():
    # Non-string input must never raise.
    scrubbed, mapping = pseudonymize(None)  # type: ignore[arg-type]
    assert scrubbed is None
    assert mapping == {}


def test_depseudonymize_passthrough_empty_mapping():
    assert depseudonymize("anything", {}) == "anything"
    assert depseudonymize(None, {"<email_1>": "a@b.com"}) is None  # type: ignore[arg-type]
