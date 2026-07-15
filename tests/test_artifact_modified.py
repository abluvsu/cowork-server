"""Tests for the published-artifact `modified` badge + update flow (2026-06-23).

Kept after the managed-cloud strip: artifact-only tests (content_mtime, card_for_folder,
artifact_status). Publish-dependent tests were removed with the publish module.
"""

import json
import os
from pathlib import Path

from cowork.services.artifacts import _content_mtime, artifact_status, card_for_folder


def _touch(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------------------
# Task 1: _content_mtime
# ---------------------------------------------------------------------------


def test_content_mtime_is_max_over_user_files(tmp_path: Path):
    (tmp_path / "metadata.json").write_text(
        json.dumps({"id": "a", "type": "html-app", "primary": "index.html"}), encoding="utf-8"
    )
    a = tmp_path / "index.html"
    a.write_text("<h1>hi</h1>", encoding="utf-8")
    b = tmp_path / "data.csv"
    b.write_text("x,y\n1,2\n", encoding="utf-8")
    _touch(a, 1000.0)
    _touch(b, 2000.0)
    pub = tmp_path / ".published.json"
    pub.write_text("{}", encoding="utf-8")
    _touch(pub, 9999.0)
    assert _content_mtime(tmp_path) == 2000


def test_content_mtime_empty_folder_is_zero(tmp_path: Path):
    (tmp_path / "metadata.json").write_text(
        json.dumps({"id": "a", "type": "mixed"}), encoding="utf-8"
    )
    assert _content_mtime(tmp_path) == 0


# ---------------------------------------------------------------------------
# Task 4: card_for_folder `modified` flag
# ---------------------------------------------------------------------------


def _patch_scan(container: Path):
    from unittest.mock import patch
    return patch("cowork.services.artifacts._scan_artifact_dirs", lambda: [container])


def _make_static_html(tmp_path: Path, body: str = "<h1>hi</h1>") -> Path:
    root = tmp_path / "static-art"
    root.mkdir()
    (root / "metadata.json").write_text(
        json.dumps({"id": "static-art", "type": "html-app", "primary": "index.html"}),
        encoding="utf-8",
    )
    (root / "index.html").write_text(body, encoding="utf-8")
    return root


def test_modified_false_for_unpublished(tmp_path: Path):
    root = _make_static_html(tmp_path)
    with _patch_scan(tmp_path):
        card = card_for_folder(root)
    assert card["modified"] is False


# ---------------------------------------------------------------------------
# Task 6: artifact_status — the preview viewer's live in-place refresh
# ---------------------------------------------------------------------------


def test_artifact_status_unpublished(tmp_path: Path):
    root = _make_static_html(tmp_path)
    with _patch_scan(tmp_path):
        s = artifact_status(str(root))
    assert s["publishedUrl"] == ""
    assert s["modified"] is False
    assert s["accessMode"] == "public"
    assert s["accessProtected"] is False


def test_artifact_status_unknown_path_is_blank(tmp_path: Path):
    with _patch_scan(tmp_path):
        s = artifact_status(str(tmp_path / "does-not-exist"))
    assert s["publishedUrl"] == ""
    assert s["modified"] is False
    assert s["accessMode"] == "public"


def test_artifact_status_loose_file_never_leaks_password(tmp_path: Path):
    f = tmp_path / "page.html"
    f.write_text("<h1>hi</h1>", encoding="utf-8")
    (tmp_path / ".published.json").write_text(
        json.dumps({
            "page.html": {
                "report_id": "rid", "url": "https://4nton.ai/a/rid", "published": True,
                "mode": "password", "requires_password": True,
                "access_password": "s3cret", "pwd_version": 1,
            }
        }),
        encoding="utf-8",
    )
    with _patch_scan(tmp_path):
        s = artifact_status(str(f))
    assert s["accessMode"] == "password"
    assert s["accessProtected"] is True
    assert s["publishedUrl"] == "https://4nton.ai/a/rid"
    assert "accessPassword" not in s
