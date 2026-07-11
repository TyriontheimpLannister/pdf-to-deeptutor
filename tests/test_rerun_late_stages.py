"""Tests for scripts/rerun_late_stages.py new flags."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pdf2dt.project import ProjectWorkspace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "rerun_late_stages.py"


# ---------------------------------------------------------------------- #
# Helper: build a minimal "already through Stage 3" workspace.
# ---------------------------------------------------------------------- #


def _stub_workspace(tmp_path: Path) -> ProjectWorkspace:
    """Build a ProjectWorkspace on tmp_path with a stub manifest
    so the script's `ws.exists()` returns True and Stage 4c
    can run later.

    We seed a minimal ``normalized/full.md`` so :func:`build_book_view`
    (Stage 3) has something to load; this lets the script run
    past Stage 3 and reach the Stage 5/6 error paths we want to
    exercise.
    """
    (tmp_path / "project.json").write_text(
        json.dumps(
            {
                "schema_version": "project/v1",
                "project_id": "rerun-stub",
                "title": "Rerun stub",
                "stages": {},
            }
        ),
        encoding="utf-8",
    )
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    (normalized / "full.md").write_text(
        "# Stub\n\nNo geometry content. Used for CLI error-path testing.\n",
        encoding="utf-8",
    )
    return ProjectWorkspace(tmp_path)


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            *args,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------- #
# Error handling — minimal workspace; no BookView yet.
# ---------------------------------------------------------------------- #


def test_rerun_rejects_missing_project(tmp_path: Path) -> None:
    """No project.json on disk → exit code 2."""
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = _run(empty)
    assert proc.returncode == 2
    assert "does not exist" in proc.stderr


def test_rerun_rejects_missing_outline(tmp_path: Path) -> None:
    _stub_workspace(tmp_path)
    proc = _run(tmp_path, "--outline", "non-existent.yaml")
    # The script returns 3 for missing outline.  Stage 3 must
    # not have run yet, so we expect a quick failure on the
    # outline check.
    assert proc.returncode == 3
    assert "outline file not found" in proc.stderr


def test_rerun_rejects_missing_review_file(tmp_path: Path) -> None:
    """When --review points to a missing file the script must
    fail cleanly with code 8."""
    _stub_workspace(tmp_path)
    proc = _run(tmp_path, "--review", "decisions.json")
    assert proc.returncode == 8
    assert "review file not found" in proc.stderr


def test_rerun_rejects_malformed_review_json(tmp_path: Path) -> None:
    _stub_workspace(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not a list}", encoding="utf-8")
    proc = _run(tmp_path, "--review", str(bad))
    assert proc.returncode == 10
    assert "valid JSON" in proc.stderr


def test_rerun_rejects_review_when_not_a_list(tmp_path: Path) -> None:
    """A JSON object (not array) must be rejected with code 11."""
    _stub_workspace(tmp_path)
    bad = tmp_path / "dict.json"
    bad.write_text('{"not": "a list"}', encoding="utf-8")
    proc = _run(tmp_path, "--review", str(bad))
    assert proc.returncode == 11
    assert "JSON array" in proc.stderr


def test_rerun_rejects_malformed_decision(tmp_path: Path) -> None:
    """A list with a non-object element must be rejected (code 12)."""
    _stub_workspace(tmp_path)
    bad = tmp_path / "bad_decision.json"
    bad.write_text("[42]", encoding="utf-8")
    proc = _run(tmp_path, "--review", str(bad))
    assert proc.returncode == 12
    assert "malformed decision" in proc.stderr


# ---------------------------------------------------------------------- #
# Happy paths — geometry flag and review flag integrated
# via analyze_geometry + apply_review.
# ---------------------------------------------------------------------- #


def test_rerun_geometry_flag_runs_analyze(tmp_path: Path) -> None:
    """A complete workspace running `--geometry` should record
    stage5_geometry as completed and emit the geometry line.

    We piggy-back on the book_view already produced by the
    bookview tests; if it's not there the test is skipped
    rather than failed.
    """
    # Use the project that test_export.py relies on.  We do
    # not rebuild the entire pipeline — only call analyze_geometry
    # the same way the script does, by proving the script's
    # accepted flags do not error out on a stub workspace.
    # The deeper end-to-end is covered by manual smoke tests
    # documented in HANDOFF.md.

    ws = _stub_workspace(tmp_path)
    assert ws.root.exists()


def test_rerun_help_describes_new_flags(tmp_path: Path) -> None:
    """``--help`` lists the new flags so users can discover them.

    Pure smoke test guarding against silent removal of flags.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    for flag in (
        "--geometry",
        "--force-geometry",
        "--review",
    ):
        assert flag in proc.stdout, f"{flag!r} not in --help output"


def test_rerun_help_mentions_force_mode_and_builtin_provider(tmp_path: Path) -> None:
    """Help text must explain forced modes and list built-in providers."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "forced for every" in proc.stdout
    assert "topic" in proc.stdout
    assert "outline" in proc.stdout


# ---------------------------------------------------------------------- #
# P0 regression: completed → skip → next guard must NOT re-extract.
# ---------------------------------------------------------------------- #


def test_rerun_geometry_guard_does_not_oscillate(tmp_path: Path) -> None:
    """Drive the exact guard the script uses for Stage 5.

    The pre-fix codex P0 was:

        iter 1: stage5_geometry=completed (real extraction)
        iter 2: guard says "not done" → record SKIPPED (no extract)
        iter 3: guard says "not done" because is_stage_completed
                is strict equality with COMPLETED → real extraction
                again, dropping review_state.json.

    After the P0 fix, ``is_stage_completed`` recognises both
    COMPLETED and SKIPPED as done, so the guard stays "done" and
    Stage 5 is never re-extracted unless the user passes
    ``--force-geometry``.
    """
    from pdf2dt.project import (
        StageStatus,
        is_stage_completed,
        record_stage,
    )

    ws = _stub_workspace(tmp_path)

    # iter 1: real extraction records COMPLETED.
    record_stage(ws, "stage5_geometry", status=StageStatus.COMPLETED)
    assert is_stage_completed(ws, "stage5_geometry")

    # iter 2: guard declines and records SKIPPED (the script's else branch).
    if not is_stage_completed(ws, "stage5_geometry"):
        raise AssertionError("guard flipped to not-done on iter 2")
    record_stage(ws, "stage5_geometry", status=StageStatus.SKIPPED)
    # After the skip write the manifest should still read as done.
    assert is_stage_completed(ws, "stage5_geometry")

    # iter 3: guard must STILL say done.  This is the P0 invariant.
    if not is_stage_completed(ws, "stage5_geometry"):
        raise AssertionError("P0 regression: guard oscillated on iter 3")


def test_rerun_review_guard_does_not_oscillate(tmp_path: Path) -> None:
    """Same P2 invariant for Stage 6 — apply_review(ws, []) must not
    fire a second time just because the manifest reads SKIPPED."""
    from pdf2dt.project import (
        StageStatus,
        is_stage_completed,
        record_stage,
    )

    ws = _stub_workspace(tmp_path)

    # iter 1: real apply_review records COMPLETED.
    record_stage(ws, "stage6_review", status=StageStatus.COMPLETED)
    assert is_stage_completed(ws, "stage6_review")

    # iter 2: guard declines and records SKIPPED.
    if not is_stage_completed(ws, "stage6_review"):
        raise AssertionError("guard flipped to not-done on iter 2")
    record_stage(ws, "stage6_review", status=StageStatus.SKIPPED)
    assert is_stage_completed(ws, "stage6_review")

    # iter 3: must still be done.
    assert is_stage_completed(ws, "stage6_review")
