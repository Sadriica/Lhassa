"""Tests for the (unwired, build-and-shelve) TOON encoder — lhassa/llm/toon.py."""

import json

from lhassa.llm.toon import from_toon, to_toon


def test_round_trip_mixed_fixture():
    rows = [
        {
            "name": "hello, world",
            "quote": 'she said "hi"',
            "multiline": "line1\nline2\r\nline3",
            "count": 42,
            "ratio": 3.14,
            "active": True,
            "inactive": False,
            "missing": None,
            "meta": {"a": 1, "b": [1, 2, 3]},
        }
    ]
    encoded = to_toon(rows, name="fixture")
    decoded = from_toon(encoded)

    assert len(decoded) == 1
    got = decoded[0]
    assert got["name"] == "hello, world"
    assert got["quote"] == 'she said "hi"'
    assert got["multiline"] == "line1\nline2\r\nline3"
    assert got["count"] == 42
    assert got["ratio"] == 3.14
    assert got["active"] is True
    assert got["inactive"] is False
    assert got["missing"] is None
    assert got["meta"] == {"a": 1, "b": [1, 2, 3]}


def test_header_grammar():
    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    encoded = to_toon(rows, name="pairs")
    lines = encoded.split("\n")
    assert lines[0] == "pairs[2]{a,b}:"
    assert lines[1] == "  1,2"
    assert lines[2] == "  3,4"


def test_non_uniform_rows_union_of_keys_and_empty_for_missing():
    rows = [{"a": 1, "b": 2}, {"a": 3, "c": 5}]
    encoded = to_toon(rows, name="mixed")
    lines = encoded.split("\n")
    assert lines[0] == "mixed[2]{a,b,c}:"
    assert lines[1] == "  1,2,"
    assert lines[2] == "  3,,5"

    decoded = from_toon(encoded)
    assert decoded[0] == {"a": 1, "b": 2, "c": None}
    assert decoded[1] == {"a": 3, "b": None, "c": 5}


def test_empty_list():
    encoded = to_toon([], name="empty")
    assert encoded == "empty[0]{}:"
    assert from_toon(encoded) == []


def test_leading_trailing_space_is_quoted_and_preserved():
    rows = [{"padded": "  spaced out  "}]
    encoded = to_toon(rows, name="pad")
    assert '"  spaced out  "' in encoded
    decoded = from_toon(encoded)
    assert decoded[0]["padded"] == "  spaced out  "


def test_from_toon_never_raises_on_garbage():
    assert from_toon("") == []
    assert from_toon("not toon at all") == []
    assert from_toon("name[abc]notabrace") == []
    assert from_toon("rows[1]{a}:\n  1,2,3,4,5") == [{"a": 1}]


def test_token_savings_over_json_on_bulk_tabular_fixture():
    # 50 rows x 6 fields, uniform, string-and-number mix — a stand-in for a
    # bulk graph-expansion result table, TOON's intended first consumer.
    rows = [
        {
            "id": i,
            "text": f"entity-{i} co-occurs with entity-{i + 1}",
            "source": f"fact:{1000 + i}",
            "via": "semantic",
            "weight": round(0.5 + (i % 10) * 0.05, 2),
            "active": i % 2 == 0,
        }
        for i in range(50)
    ]

    toon_text = to_toon(rows, name="expansions")
    json_text = json.dumps(rows)

    ratio = len(toon_text) / len(json_text)
    assert ratio < 0.60, f"TOON not compact enough: {ratio:.2%} of JSON length"

    # And the round trip still holds on the bulk fixture (bool/int/float/str).
    decoded = from_toon(toon_text)
    assert len(decoded) == 50
    for original, got in zip(rows, decoded):
        assert got["id"] == original["id"]
        assert got["text"] == original["text"]
        assert got["source"] == original["source"]
        assert got["via"] == original["via"]
        assert got["weight"] == original["weight"]
        assert got["active"] == original["active"]
