"""Tests for the modular notification channel registry.

Covers the :class:`~lhassa.notify.channels.Channel` registry, ``active_channels``
resolution (order, unknown-skip, unconfigured-skip, log always on), the desktop
channel (subprocess fully mocked; no real ``notify-send``), and the dispatcher
fanning out over several channels. No network, no subprocess, no real notifier.
"""

import asyncio
from types import SimpleNamespace

import pytest

from lhassa.notify import channels as ch
from lhassa.notify import desktop, dispatch


class _FakeChannel:
    """A recording channel for tests."""

    def __init__(self, name, *, configured=True, delivered=True):
        self.name = name
        self._configured = configured
        self._delivered = delivered
        self.sent = []

    def is_configured(self, config):
        return self._configured

    async def send(self, text, *, level="info", config=None):
        self.sent.append((text, level))
        return self._delivered


@pytest.fixture
def add_channel():
    """Register channels and unregister them after the test (keeps CHANNELS clean)."""
    added = []

    def _add(channel, *, override=False):
        ch.register_channel(channel.name, channel, override=override)
        added.append(channel.name)
        return channel

    yield _add

    for name in added:
        if name in ch.CHANNELS:
            ch.CHANNELS.unregister(name)


def _cfg(channels):
    return SimpleNamespace(notify_channels=channels)


# ── registry basics ──────────────────────────────────────────────────────────────


def test_builtin_channels_registered():
    # The two built-ins are present the moment the package is imported; the
    # telegram channel comes from the telegram pack, registered with it.
    from lhassa import actionpack
    from lhassa.extensions import PACKS

    actionpack.register_all()
    for name in ("desktop", "log"):
        assert name in ch.CHANNELS
    assert ("telegram" in ch.CHANNELS) == ("telegram" in PACKS)
    assert ch.CHANNELS.get("log").name == "log"


def test_register_and_get(add_channel):
    c = add_channel(_FakeChannel("fake_a"))
    assert ch.CHANNELS.get("fake_a") is c


def test_register_decorator_instantiates_class():
    # @register_channel on a class registers an INSTANCE and returns the class.
    @ch.register_channel("fake_deco")
    class FakeDeco:
        name = "fake_deco"

        def is_configured(self, config):
            return True

        async def send(self, text, *, level="info", config=None):
            return True

    try:
        assert FakeDeco.__name__ == "FakeDeco"  # class returned unchanged
        assert isinstance(ch.CHANNELS.get("fake_deco"), FakeDeco)  # instance stored
    finally:
        ch.CHANNELS.unregister("fake_deco")


def test_duplicate_registration_raises(add_channel):
    add_channel(_FakeChannel("dup"))
    with pytest.raises(ValueError):
        ch.register_channel("dup", _FakeChannel("dup"))
    # override=True replaces.
    replacement = _FakeChannel("dup")
    ch.register_channel("dup", replacement, override=True)
    assert ch.CHANNELS.get("dup") is replacement


# ── active_channels resolution ────────────────────────────────────────────────────


def test_active_channels_honors_order_and_skips(add_channel, caplog):
    a = add_channel(_FakeChannel("ord_a"))
    b = add_channel(_FakeChannel("ord_b"))
    unconf = add_channel(_FakeChannel("ord_unconf", configured=False))

    # Order reversed vs registration; an unknown name + an unconfigured one present.
    cfg = _cfg("ord_b, nope, ord_unconf, ord_a")
    active = ch.active_channels(cfg)

    # Unknown skipped, unconfigured skipped silently, the rest in config order.
    assert active == [b, a]
    assert unconf not in active


def test_active_channels_unknown_warns_but_continues(add_channel):
    a = add_channel(_FakeChannel("known_one"))
    cfg = _cfg("known_one, totally_unknown")
    active = ch.active_channels(cfg)
    assert active == [a]


def test_active_channels_empty_when_blank():
    assert ch.active_channels(_cfg("")) == []
    assert ch.active_channels(_cfg("   ,  ,")) == []


def test_active_channels_default_is_telegram_when_attr_absent():
    # A config object without notify_channels falls back to "telegram" only.
    cfg = SimpleNamespace(telegram_bot_token="", telegram_chat_id="")
    names = [c.name for c in ch.active_channels(cfg)]
    # Telegram is unconfigured here → skipped → empty, but the default resolved to
    # the telegram channel (not desktop/log), i.e. no desktop toast leaks in.
    assert names == []


def test_log_channel_always_configured():
    log = ch.CHANNELS.get("log")
    assert log.is_configured(_cfg("log")) is True
    active = ch.active_channels(_cfg("log"))
    assert active == [log]
    assert asyncio.run(log.send("hi", level="warning")) is True


def test_active_channels_skips_channel_whose_predicate_raises(add_channel):
    class _Boom(_FakeChannel):
        def is_configured(self, config):
            raise RuntimeError("predicate boom")

    good = add_channel(_FakeChannel("good_one"))
    add_channel(_Boom("boom_one"))
    active = ch.active_channels(_cfg("boom_one, good_one"))
    assert active == [good]  # the raising predicate is skipped, not fatal


# ── desktop channel (subprocess mocked) ───────────────────────────────────────────


def test_desktop_is_configured_follows_which(monkeypatch):
    d = ch.CHANNELS.get("desktop")
    monkeypatch.setattr(desktop.shutil, "which", lambda _b: "/usr/bin/notify-send")
    assert d.is_configured() is True
    monkeypatch.setattr(desktop.shutil, "which", lambda _b: None)
    assert d.is_configured() is False


def test_desktop_send_invokes_notify_send(monkeypatch):
    d = ch.CHANNELS.get("desktop")
    calls = []

    # The desktop channel dispatches per platform (notify-send on Linux,
    # osascript elsewhere), so the platform is PINNED by injection rather than
    # left to whatever host the suite runs on; macOS CI caught this.
    monkeypatch.setenv("LHASSA_PLATFORM", "linux")
    monkeypatch.setattr(desktop.shutil, "which", lambda _b: "/usr/bin/notify-send")
    monkeypatch.setattr(
        desktop.subprocess, "run", lambda *a, **k: calls.append((a, k))
    )

    ok = asyncio.run(d.send("desktop hi", level="error"))
    assert ok is True
    assert len(calls) == 1
    argv = calls[0][0][0]
    assert argv[0] == "notify-send"
    assert "desktop hi" in argv
    assert "--urgency=critical" in argv  # error → critical


def test_desktop_send_noop_when_binary_missing(monkeypatch):
    d = ch.CHANNELS.get("desktop")

    def _boom(*a, **k):
        raise AssertionError("must not run notify-send when the binary is missing")

    monkeypatch.setattr(desktop.shutil, "which", lambda _b: None)
    monkeypatch.setattr(desktop.subprocess, "run", _boom)
    assert asyncio.run(d.send("x")) is False


def test_desktop_send_never_raises(monkeypatch):
    d = ch.CHANNELS.get("desktop")
    monkeypatch.setattr(desktop.shutil, "which", lambda _b: "/usr/bin/notify-send")

    def _raise(*a, **k):
        raise RuntimeError("subprocess kaboom")

    monkeypatch.setattr(desktop.subprocess, "run", _raise)
    assert asyncio.run(d.send("x")) is False


# ── dispatcher fan-out ────────────────────────────────────────────────────────────


def test_dispatch_fans_out_to_multiple_channels(add_channel):
    a = add_channel(_FakeChannel("fan_a"))
    b = add_channel(_FakeChannel("fan_b"))
    cfg = _cfg("fan_a, fan_b")

    assert asyncio.run(dispatch.notify("broadcast", level="warning", config=cfg)) is True
    assert a.sent == [("broadcast", "warning")]
    assert b.sent == [("broadcast", "warning")]


def test_dispatch_true_if_any_delivers(add_channel):
    add_channel(_FakeChannel("nd_a", delivered=False))
    b = add_channel(_FakeChannel("nd_b", delivered=True))
    cfg = _cfg("nd_a, nd_b")
    assert asyncio.run(dispatch.notify("x", config=cfg)) is True
    assert b.sent == [("x", "info")]


def test_dispatch_false_when_none_deliver(add_channel):
    add_channel(_FakeChannel("z_a", delivered=False))
    add_channel(_FakeChannel("z_b", delivered=False))
    assert asyncio.run(dispatch.notify("x", config=_cfg("z_a, z_b"))) is False


def test_dispatch_never_raises_on_channel_error(add_channel):
    class _Raiser(_FakeChannel):
        async def send(self, text, *, level="info", config=None):
            raise RuntimeError("send boom")

    add_channel(_Raiser("raiser"))
    good = add_channel(_FakeChannel("after"))
    # The raising channel is swallowed; the next channel still delivers → True.
    assert asyncio.run(dispatch.notify("x", config=_cfg("raiser, after"))) is True
    assert good.sent == [("x", "info")]


def test_desktop_send_uses_osascript_off_linux(monkeypatch):
    """The macOS counterpart; same seam, other platform, pinned by injection."""
    d = ch.CHANNELS.get("desktop")
    calls = []

    monkeypatch.setenv("LHASSA_PLATFORM", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda _b: "/usr/bin/" + _b)
    monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: calls.append((a, k)))

    ok = asyncio.run(d.send("hola mac", level="error"))
    assert ok is True
    argv = calls[0][0][0]
    assert argv[0] == "osascript"
    # Both halves ride the environment, never interpolated into the script.
    # This matters more now that a message with no separator becomes the TITLE
    # (it used to always be the body): title and message must both be passed
    # out of band, or the summary would be the new injection seam.
    assert "hola mac" not in " ".join(argv)
    env = calls[0][1]["env"]
    assert env["_LHASSA_TITLE"] == "hola mac" and env["_LHASSA_MSG"] == ""


# ── the toast's headline ─────────────────────────────────────────────────────────


def test_split_headline_lifts_the_messages_own_opening_clause():
    """The toast already shows "Lhassa" as the app name; the summary must not repeat it.

    The regression: every alert rendered as "Lhassa / Lhassa / <the actual
    message>", spending the one line the owner reads at a glance on the app
    name they already knew.
    """
    from lhassa.notify.desktop import split_headline

    title, body = split_headline("Something needs a look: 2 new failures")
    assert title == "Something needs a look:"
    assert body == "2 new failures"


def test_split_headline_keeps_the_separator_on_the_summary():
    """Dropping it broke the sentence the owner reads.

    The daemon stacks summary over body, so a summary ending in "failed" above
    a body starting with "1 critical" rendered as "…failed 1 critical finding":
    the colon gone and nothing in its place.
    """
    from lhassa.notify.desktop import split_headline

    title, body = split_headline(
        "The integrity check failed: 1 critical finding and 1 warning"
    )
    assert title.endswith(":")
    assert f"{title} {body}" == (
        "The integrity check failed: 1 critical finding and 1 warning"
    )


def test_split_headline_never_repeats_the_app_name():
    """The toast already shows "Lhassa" as the application.

    Falling back to it as the SUMMARY printed the name twice and pushed the
    message down a line. With no separator the message itself is the summary;
    an empty body is fine, a duplicated summary is not.
    """
    from lhassa.notify.desktop import _APP_NAME, split_headline

    assert split_headline("disco casi lleno") == ("disco casi lleno", "")
    assert split_headline("") == (_APP_NAME, "")  # nothing to say, nothing to lift


def test_split_headline_falls_back_to_the_first_sentence():
    """A whole first sentence is a legitimate headline when there is no clause
    separator; without this the entire message landed in the summary, where the
    daemon truncates it."""
    from lhassa.notify.desktop import split_headline

    title, body = split_headline(
        "Encontré un problema serio y uno menor en las comprobaciones internas. "
        "Corré `lhassa integrity findings`, ahí está lo que falló."
    )
    assert title.endswith("internas.")
    assert body.startswith("Corré")


def test_split_headline_refuses_an_over_long_first_clause():
    """A clause too long to be a headline is detail, not a title; the whole
    message becomes the summary rather than a title made of detail."""
    from lhassa.notify.desktop import split_headline

    long_head = "x" * 80
    text = f"{long_head}: tail"
    assert split_headline(text) == (text, "")
