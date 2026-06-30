"""Tests for drop-in plugin discovery (``lhassa/plugins.py``)."""

import sys
from types import SimpleNamespace

import pytest

from lhassa import plugins


@pytest.fixture(autouse=True)
def _isolate_plugin_state():
    """Reset the loaded-plugin tracker around each test so they don't bleed."""
    saved = set(plugins._LOADED)
    plugins._LOADED.clear()
    try:
        yield
    finally:
        plugins._LOADED.clear()
        plugins._LOADED.update(saved)


def _config(tmp_path):
    """A minimal stand-in for ``Config`` exposing only ``plugins_dir``."""
    return SimpleNamespace(plugins_dir=str(tmp_path))


def test_directory_plugin_registers_an_action(tmp_path):
    from lhassa.core import actions

    (tmp_path / "echo_plugin.py").write_text(
        "from lhassa.core.actions import register_action\n"
        "\n"
        "@register_action('plugin_echo')\n"
        "def plugin_echo(params):\n"
        "    return 'echo:' + str(params.get('x'))\n"
    )
    try:
        loaded = plugins.load_plugins(_config(tmp_path))
        # The plugin was imported, so its @register_action ran.
        assert "lhassa_plugin_echo_plugin" in loaded
        assert "plugin_echo" in actions.ACTIONS
    finally:
        if "plugin_echo" in actions._REGISTRY:
            actions._REGISTRY.unregister("plugin_echo")
        sys.modules.pop("lhassa_plugin_echo_plugin", None)


@pytest.mark.asyncio
async def test_registered_action_is_dispatchable_via_run(tmp_path):
    from lhassa.core import actions

    (tmp_path / "shout.py").write_text(
        "from lhassa.core.actions import register_action\n"
        "\n"
        "@register_action('plugin_shout')\n"
        "def plugin_shout(params):\n"
        "    return 'SHOUT'\n"
    )
    try:
        plugins.load_plugins(_config(tmp_path))
        out = await actions.run("plugin_shout", {})
        assert out == "SHOUT"
    finally:
        if "plugin_shout" in actions._REGISTRY:
            actions._REGISTRY.unregister("plugin_shout")
        sys.modules.pop("lhassa_plugin_shout", None)


def test_idempotent_second_call_is_noop(tmp_path):
    (tmp_path / "marker.py").write_text("VALUE = 1\n")
    first = plugins.load_plugins(_config(tmp_path))
    assert "lhassa_plugin_marker" in first
    second = plugins.load_plugins(_config(tmp_path))
    # Already loaded → not re-reported.
    assert "lhassa_plugin_marker" not in second
    sys.modules.pop("lhassa_plugin_marker", None)


def test_broken_plugin_is_skipped_not_fatal(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom on import')\n")
    (tmp_path / "good.py").write_text("OK = True\n")
    # Must not raise even though one plugin explodes on import.
    loaded = plugins.load_plugins(_config(tmp_path))
    assert "lhassa_plugin_broken" not in loaded
    # A sibling plugin still loads.
    assert "lhassa_plugin_good" in loaded
    sys.modules.pop("lhassa_plugin_good", None)


def test_missing_directory_returns_empty(tmp_path):
    cfg = SimpleNamespace(plugins_dir=str(tmp_path / "does-not-exist"))
    assert plugins.load_plugins(cfg) == []


def test_package_plugin_is_imported(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("LOADED = True\n")
    loaded = plugins.load_plugins(_config(tmp_path))
    assert "lhassa_plugin_mypkg" in loaded
    sys.modules.pop("lhassa_plugin_mypkg", None)


def test_dunder_and_private_entries_skipped(tmp_path):
    (tmp_path / "_private.py").write_text("X = 1\n")
    (tmp_path / ".hidden.py").write_text("X = 1\n")
    loaded = plugins.load_plugins(_config(tmp_path))
    assert loaded == []


def test_entry_points_path_tolerated_when_none(tmp_path):
    # No entry points in the group present → just no extra names, no crash.
    cfg = SimpleNamespace(plugins_dir=str(tmp_path))
    assert plugins.load_plugins(cfg) == []
    # And the entry-point source on its own is defensive.
    assert isinstance(plugins._load_from_entry_points(), list)


def test_load_plugins_never_raises_on_bad_config():
    # A config whose plugins_dir attribute access blows up is tolerated.
    class Bad:
        @property
        def plugins_dir(self):
            raise RuntimeError("nope")

    assert plugins.load_plugins(Bad()) == []
