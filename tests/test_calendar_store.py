"""Tests for the multi-calendar JSON store (lhassa/calendar/store.py).

Offline, no network: every test points ``calendars_file`` at a tmp_path so the
real ``~/.lhassa/calendars.json`` is never touched.
"""

from types import SimpleNamespace

from lhassa.calendar import store


def _cfg(tmp_path, **over):
    base = dict(
        calendars_file=str(tmp_path / "calendars.json"),
        calendar_ics_url="",
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── load / add / remove roundtrip ─────────────────────────────────────────────


def test_load_calendars_missing_file_is_empty(tmp_path):
    cfg = _cfg(tmp_path)
    assert store.load_calendars(config=cfg) == []


def test_add_then_list_then_remove_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)

    assert store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg) is True
    items = store.list_calendars(config=cfg)
    assert items == [{"name": "Work", "url": "https://example.edu/feed.ics"}]

    assert store.add_calendar("Personal", "https://example.com/p.ics", config=cfg) is True
    items = store.list_calendars(config=cfg)
    assert {i["name"] for i in items} == {"Work", "Personal"}

    assert store.remove_calendar("Work", config=cfg) is True
    items = store.list_calendars(config=cfg)
    assert items == [{"name": "Personal", "url": "https://example.com/p.ics"}]

    # Removing something absent reports False and changes nothing.
    assert store.remove_calendar("Work", config=cfg) is False


def test_add_calendar_dedupes_by_name_replacing_url(tmp_path):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/old.ics", config=cfg)
    store.add_calendar("Work", "https://example.edu/new.ics", config=cfg)

    items = store.list_calendars(config=cfg)
    assert len(items) == 1
    assert items[0]["url"] == "https://example.edu/new.ics"


def test_add_calendar_rejects_bad_url(tmp_path):
    cfg = _cfg(tmp_path)
    assert store.add_calendar("Bad", "not-a-url", config=cfg) is False
    assert store.list_calendars(config=cfg) == []


def test_add_calendar_rejects_empty_name_or_url(tmp_path):
    cfg = _cfg(tmp_path)
    assert store.add_calendar("", "https://example.com/x.ics", config=cfg) is False
    assert store.add_calendar("Name", "", config=cfg) is False


def test_atomic_write_leaves_no_tmp_artifact(tmp_path):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    leftovers = list(tmp_path.glob(".calendars-*.tmp"))
    assert leftovers == []


def test_corrupt_file_degrades_to_empty(tmp_path):
    path = tmp_path / "calendars.json"
    path.write_text("{not valid json", encoding="utf-8")
    cfg = _cfg(tmp_path)
    assert store.load_calendars(config=cfg) == []


def test_non_list_json_degrades_to_empty(tmp_path):
    path = tmp_path / "calendars.json"
    path.write_text('{"oops": "not a list"}', encoding="utf-8")
    cfg = _cfg(tmp_path)
    assert store.load_calendars(config=cfg) == []


# ── all_feeds — merges the env-var shortcut ───────────────────────────────────


def test_all_feeds_empty_when_nothing_configured(tmp_path):
    cfg = _cfg(tmp_path)
    assert store.all_feeds(config=cfg) == []


def test_all_feeds_merges_env_shortcut_as_default(tmp_path):
    cfg = _cfg(tmp_path, calendar_ics_url="https://example.com/shortcut.ics")
    feeds = store.all_feeds(config=cfg)
    assert feeds == [{"name": "default", "url": "https://example.com/shortcut.ics"}]


def test_all_feeds_does_not_duplicate_shortcut_already_stored(tmp_path):
    cfg = _cfg(tmp_path, calendar_ics_url="https://example.com/shortcut.ics")
    store.add_calendar("Mine", "https://example.com/shortcut.ics", config=cfg)

    feeds = store.all_feeds(config=cfg)
    assert feeds == [{"name": "Mine", "url": "https://example.com/shortcut.ics"}]


def test_all_feeds_combines_stored_and_shortcut(tmp_path):
    cfg = _cfg(tmp_path, calendar_ics_url="https://example.com/shortcut.ics")
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)

    feeds = store.all_feeds(config=cfg)
    names = {f["name"] for f in feeds}
    assert names == {"Work", "default"}


def test_all_feeds_avoids_default_name_clash(tmp_path):
    cfg = _cfg(tmp_path, calendar_ics_url="https://example.com/shortcut.ics")
    store.add_calendar("default", "https://example.edu/other.ics", config=cfg)

    feeds = store.all_feeds(config=cfg)
    names = [f["name"] for f in feeds]
    assert names.count("default") == 1
    assert "default2" in names


# ── primary flag — exactly one master calendar ────────────────────────────────


def test_get_primary_none_when_unset(tmp_path):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    assert store.get_primary(config=cfg) is None


def test_set_primary_then_get_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.add_calendar("Personal", "https://example.com/p.ics", config=cfg)

    assert store.set_primary("Work", config=cfg) is True
    assert store.get_primary(config=cfg) == "Work"


def test_set_primary_clears_it_on_others(tmp_path):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    store.add_calendar("Personal", "https://example.com/p.ics", config=cfg)

    store.set_primary("Work", config=cfg)
    store.set_primary("Personal", config=cfg)

    assert store.get_primary(config=cfg) == "Personal"
    items = store.list_calendars(config=cfg)
    primaries = [i for i in items if i.get("primary")]
    assert len(primaries) == 1
    assert primaries[0]["name"] == "Personal"


def test_set_primary_unknown_name_fails(tmp_path):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/feed.ics", config=cfg)
    assert store.set_primary("Ghost", config=cfg) is False
    assert store.get_primary(config=cfg) is None


def test_set_primary_survives_a_later_url_replace(tmp_path):
    cfg = _cfg(tmp_path)
    store.add_calendar("Work", "https://example.edu/old.ics", config=cfg)
    store.set_primary("Work", config=cfg)

    # Re-adding Work (e.g. updating its url) must not silently un-primary it.
    store.add_calendar("Work", "https://example.edu/new.ics", config=cfg)
    assert store.get_primary(config=cfg) == "Work"
