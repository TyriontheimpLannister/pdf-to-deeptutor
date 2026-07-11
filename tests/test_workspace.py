"""Tests for :class:`ProjectWorkspace` low-level helpers."""
from __future__ import annotations

import hashlib
from pathlib import Path

from pdf2dt.project.workspace import _dir_hash


def test_dir_hash_is_deterministic(tmp_path: Path) -> None:
    """Same directory contents must hash identically across runs.

    The hash is used by ``copy_mineru_raw`` to detect
    idempotent re-runs.  Determinism is the contract.
    """
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "c.txt").write_text("beta", encoding="utf-8")

    h1 = _dir_hash(tmp_path)
    h2 = _dir_hash(tmp_path)
    assert h1 == h2
    # Sanity: must be a real SHA-256 hex digest.
    assert len(h1) == 64
    int(h1, 16)  # raises if not hex


def test_dir_hash_changes_on_content_edit(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("alpha", encoding="utf-8")
    h_before = _dir_hash(tmp_path)
    a.write_text("alpha alpha alpha", encoding="utf-8")
    h_after = _dir_hash(tmp_path)
    assert h_before != h_after


def test_dir_hash_changes_on_file_size_change(tmp_path: Path) -> None:
    """Regression: the call must encode file size in big-endian.

    On Python 3.10 ``int.to_bytes(8)`` raised a ``TypeError``
    because the ``byteorder`` argument was mandatory.  The fix
    passed ``"big"`` explicitly.  This test pins the size-only
    behaviour so a future revert is caught immediately.
    """
    a = tmp_path / "a.txt"
    a.write_text("a", encoding="utf-8")  # 1 byte
    h1 = _dir_hash(tmp_path)
    a.write_text("aa", encoding="utf-8")  # 2 bytes
    h2 = _dir_hash(tmp_path)
    assert h1 != h2


def test_dir_hash_empty_directory(tmp_path: Path) -> None:
    """Empty directory still produces a stable hash (no crash)."""
    h = _dir_hash(tmp_path)
    assert h == hashlib.sha256(b"").hexdigest()


def test_dir_hash_ignores_subdirectories_in_filelist(tmp_path: Path) -> None:
    """Only file contents contribute; bare directories do not."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    h_with_sub = _dir_hash(tmp_path)

    # Same file, no subdirectory.
    other = tmp_path.parent / "other"
    if other.exists():
        for child in other.iterdir():
            child.unlink()
    other.mkdir()
    (other / "a.txt").write_text("alpha", encoding="utf-8")
    h_without_sub = _dir_hash(other)

    assert h_with_sub == h_without_sub
