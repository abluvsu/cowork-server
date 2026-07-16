"""skill_links.py's own docstring: "Symlinks only — if one can't be created
we raise rather than fall back to a copy, so a misconfigured filesystem
fails loudly." A regression silently replaced that raise with
shutil.rmtree()/unlink() on any real (non-symlink) file or directory sitting
at the link path — meaning a user's own content (hand-authored skill files,
or a leftover from a previous misconfigured-filesystem failure) would be
silently deleted on the next reconcile pass (boot / skill toggle / project
creation). No test previously covered this module at all.
"""
from __future__ import annotations

import pytest

from cowork.services.skill_links import _ensure_symlink, _remove_link


def _is_link(path):
    """True for a symlink OR a Windows directory junction (the code's own
    fallback when symlink creation needs privileges the CI/dev box lacks)."""
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def test_ensure_symlink_creates_new_link(tmp_path):
    target = tmp_path / "canon"
    target.mkdir()
    link = tmp_path / "project" / "skills" / "myskill"

    _ensure_symlink(link, target)

    assert _is_link(link)
    assert link.resolve() == target.resolve()


def test_ensure_symlink_is_idempotent_when_already_correct(tmp_path):
    target = tmp_path / "canon"
    target.mkdir()
    link = tmp_path / "project" / "skills" / "myskill"
    _ensure_symlink(link, target)

    _ensure_symlink(link, target)  # must not raise or need to relink

    assert _is_link(link)


def test_ensure_symlink_refuses_to_delete_a_real_directory(tmp_path):
    target = tmp_path / "canon"
    target.mkdir()
    link = tmp_path / "project" / "skills" / "myskill"
    link.parent.mkdir(parents=True)
    link.mkdir()
    (link / "hand_authored.md").write_text("real user content")

    with pytest.raises(RuntimeError):
        _ensure_symlink(link, target)

    # The real content must survive the failed attempt.
    assert (link / "hand_authored.md").read_text() == "real user content"


def test_ensure_symlink_refuses_to_delete_a_real_file(tmp_path):
    target = tmp_path / "canon"
    target.mkdir()
    link = tmp_path / "project" / "skills" / "myskill"
    link.parent.mkdir(parents=True)
    link.write_text("not a symlink")

    with pytest.raises(RuntimeError):
        _ensure_symlink(link, target)

    assert link.read_text() == "not a symlink"


def test_remove_link_deletes_a_symlink(tmp_path):
    target = tmp_path / "canon"
    target.mkdir()
    link = tmp_path / "project" / "skills" / "myskill"
    _ensure_symlink(link, target)

    _remove_link(link)

    assert not link.exists()
    assert not link.is_symlink()


def test_remove_link_refuses_to_delete_a_real_directory(tmp_path):
    link = tmp_path / "project" / "skills" / "myskill"
    link.mkdir(parents=True)
    (link / "hand_authored.md").write_text("real user content")

    with pytest.raises(RuntimeError):
        _remove_link(link)

    assert (link / "hand_authored.md").read_text() == "real user content"


def test_remove_link_is_a_noop_when_nothing_there(tmp_path):
    link = tmp_path / "project" / "skills" / "myskill"
    _remove_link(link)  # must not raise
