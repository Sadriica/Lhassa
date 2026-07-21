"""list_dir / read_file — deterministic file perception, roots-bounded.

All offline: a tmp_path plays the knowledge root. These pin the security
frontier (outside-root refused, secret-shaped names never read, traversal
resolved before the check) and the honest degradations.
"""

from types import SimpleNamespace

from lhassa.actionpack._files import list_dir, read_file


def _cfg(root=None):
    return SimpleNamespace(knowledge_roots=str(root) if root else "")


def test_no_roots_is_an_honest_refusal_naming_the_knob(tmp_path):
    out = read_file({"path": str(tmp_path / "x.txt"), "config": _cfg()})
    assert "LHASSA_KNOWLEDGE_ROOTS" in out


def test_outside_root_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    out = read_file({"path": str(outside), "config": _cfg(root)})
    assert "outside" in out
    assert "secret" not in out


def test_traversal_is_resolved_before_the_root_check(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "leak.txt").write_text("leak")
    out = read_file({"path": f"{root}/../leak.txt", "config": _cfg(root)})
    assert "leak" not in out.splitlines()[-1] or "outside" in out
    assert "outside" in out


def test_read_file_happy_path(tmp_path):
    (tmp_path / "nota.md").write_text("hola mundo")
    out = read_file({"path": str(tmp_path / "nota.md"), "config": _cfg(tmp_path)})
    assert "hola mundo" in out


def test_read_file_truncates_long_content(tmp_path):
    (tmp_path / "big.md").write_text("x" * 10_000)
    out = read_file({"path": str(tmp_path / "big.md"), "config": _cfg(tmp_path)})
    assert "truncated" in out
    assert len(out) < 6_000


def test_secret_shaped_file_is_never_read(tmp_path):
    (tmp_path / ".env").write_text("API_KEY=oops")
    out = read_file({"path": str(tmp_path / ".env"), "config": _cfg(tmp_path)})
    assert "oops" not in out
    assert "ignore-list" in out


def test_list_dir_hides_secret_shaped_entries(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "readme.md").write_text("hi")
    (tmp_path / ".env").write_text("KEY=x")
    out = list_dir({"path": str(tmp_path), "config": _cfg(tmp_path)})
    assert "docs/" in out
    assert "readme.md" in out
    assert ".env" not in out
    assert "hidden" in out


def test_list_dir_on_a_file_and_read_file_on_a_dir_degrade(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    assert "not a directory" in list_dir({"path": str(tmp_path / "f.txt"), "config": _cfg(tmp_path)})
    assert "not a file" in read_file({"path": str(tmp_path), "config": _cfg(tmp_path)})


def test_never_raises_on_garbage_params(tmp_path):
    assert isinstance(read_file(None), str)
    assert isinstance(list_dir({"path": 42, "config": _cfg(tmp_path)}), str)
