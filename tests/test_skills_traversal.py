"""A skill name can never address anything outside the store.

`SkillStore.folder_for` used to fall back to the RAW name when slugify returned
"" — and slugify("..") is "". So `delete("..")` resolved to the store's parent
and rmtree'd it: on a real install that is ~/.lhassa, brain.db included. These
tests pin the boundary at the one function every path goes through.
"""

from types import SimpleNamespace

import pytest

from lhassa.skills.store import SkillStore
from lhassa.skills.skillmd import Skill


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    (tmp_path / "brain.db").write_text("PRECIOUS")
    return SkillStore(SimpleNamespace(skills_dir=str(root))), tmp_path, root


@pytest.mark.parametrize("name", ["..", "../..", ".", "///", "../../etc", "  ..  "])
def test_hostile_names_never_escape_the_store(store, name):
    st, base, root = store
    folder = st.folder_for(name)
    assert root.resolve() in folder.resolve().parents, f"{name!r} escaped to {folder}"


@pytest.mark.parametrize("name", ["..", "../..", ".", "///"])
def test_deleting_a_hostile_name_destroys_nothing(store, name):
    st, base, root = store
    assert st.delete(name) is False
    assert (base / "brain.db").exists(), "the brain DB was destroyed"
    assert root.exists(), "the skills directory was destroyed"


def test_unsluggable_names_get_stable_distinct_folders(store):
    st, _base, _root = store
    a, b = st.folder_for(".."), st.folder_for("...")
    assert a != b
    assert a == st.folder_for("..")  # stable across calls


def test_ordinary_skills_still_work(store):
    st, _base, root = store
    (root / "real-skill").mkdir()
    (root / "real-skill" / "SKILL.md").write_text("---\nname: real\n---\nbody")
    assert st.folder_for("My Skill").name == "my-skill"
    assert st.delete("real-skill") is True
    assert not (root / "real-skill").exists()


def test_get_does_not_read_outside_the_store(store):
    st, base, _root = store
    (base / "outside").mkdir()
    (base / "outside" / "SKILL.md").write_text("---\nname: leaked\n---\nsecret")
    assert st.get("../outside") is None


def test_symlink_cannot_redirect_save_or_read_outside_store(store):
    st, base, root = store
    outside = base / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: leaked\n---\nsecret")
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        st.save(Skill(name="escape", description="d", instructions="body"))
    assert st.get("escape") is None
    assert st.list_skills() == []
    assert (outside / "SKILL.md").read_text().endswith("secret")


def test_in_store_folder_alias_cannot_overwrite_or_delete_target(store):
    st, _base, root = store
    target = root / "real"
    target.mkdir()
    (target / "SKILL.md").write_text("---\nname: real\n---\nprecious")
    (root / "alias").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError):
        st.save(Skill(name="alias", description="d", instructions="overwrite"))
    assert st.delete("alias") is False
    assert target.exists()
    assert (target / "SKILL.md").read_text().endswith("precious")


def test_definition_symlink_cannot_read_or_write_outside_store(store):
    st, base, root = store
    outside = base / "outside-definition.md"
    outside.write_text("secret")
    folder = root / "linked-definition"
    folder.mkdir()
    (folder / "SKILL.md").symlink_to(outside)

    got = st.get("linked-definition")
    assert got is not None and got.error
    assert "secret" not in got.instructions
    with pytest.raises(ValueError):
        st.save(
            Skill(name="linked-definition", description="d", instructions="overwrite")
        )
    assert outside.read_text() == "secret"
