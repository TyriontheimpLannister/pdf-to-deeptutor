"""Tests for the MinerU daily quota tracker.

The quota is a *best-effort* local counter because the 1000 pages/day
limit is shared with the MinerU web UI and the API does not expose
real-time usage. The pre-check refuses obvious over-submissions; the
post-call reconciliation updates the counter from the actual page
count reported by the API; UTC date roll-over resets the counter to 0.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pdf2dt.providers.mineru.quota import (
    DEFAULT_QUOTA,
    SCHEMA_VERSION,
    QuotaDecision,
    QuotaState,
    check_quota,
    estimate_pdf_pages,
    load_quota_state,
    quota_path,
    record_pages_used,
    save_quota_state,
)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()


# ---------------------------------------------------------------------- #
# Pre-check decisions
# ---------------------------------------------------------------------- #


def test_pre_check_refuses_when_estimated_exceeds_remaining() -> None:
    state = QuotaState(
        date_utc=_today(),
        pages_used=900,
        pages_quota=1000,
    )
    decision, reason = check_quota(state, estimated_pages=200)
    assert decision is QuotaDecision.REFUSE_PRE
    assert "Refusing" in reason
    assert "900" in reason and "1000" in reason and "200" in reason


def test_pre_check_allows_when_under_quota() -> None:
    state = QuotaState(
        date_utc=_today(),
        pages_used=100,
        pages_quota=1000,
    )
    decision, reason = check_quota(state, estimated_pages=200)
    assert decision is QuotaDecision.ALLOW
    assert "300 / 1000" in reason  # 100 used + 200 estimated


def test_pre_check_warns_when_pages_unknown() -> None:
    state = QuotaState(
        date_utc=_today(),
        pages_used=100,
        pages_quota=1000,
    )
    decision, reason = check_quota(state, estimated_pages=None)
    assert decision is QuotaDecision.WARN_LIVE
    assert "Could not estimate" in reason


def test_pre_check_refuses_when_quota_already_exhausted_and_unknown_estimate() -> None:
    """If we can't estimate, refuse when the daily counter is already full."""
    state = QuotaState(
        date_utc=_today(),
        pages_used=1000,
        pages_quota=1000,
    )
    decision, _reason = check_quota(state, estimated_pages=None)
    assert decision is QuotaDecision.REFUSE_PRE


# ---------------------------------------------------------------------- #
# Persistence + post-call reconciliation
# ---------------------------------------------------------------------- #


def test_save_load_round_trip(tmp_path: Path) -> None:
    state = QuotaState(
        date_utc=_today(),
        pages_used=480,
        pages_quota=1000,
    )
    path = save_quota_state(tmp_path, state)
    assert path == quota_path(tmp_path)
    assert path.is_file()
    loaded = load_quota_state(tmp_path)
    assert loaded.date_utc == state.date_utc
    assert loaded.pages_used == 480
    assert loaded.pages_quota == 1000
    assert loaded.updated_at  # was filled in by save
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == SCHEMA_VERSION


def test_post_call_updates_pages_used(tmp_path: Path) -> None:
    state = QuotaState(
        date_utc=_today(),
        pages_used=200,
        pages_quota=1000,
    )
    save_quota_state(tmp_path, state)
    new_state = record_pages_used(tmp_path, state, pages=150)
    assert new_state.pages_used == 350
    reloaded = load_quota_state(tmp_path)
    assert reloaded.pages_used == 350


def test_date_rollover_resets_counter(tmp_path: Path) -> None:
    """A persisted state from yesterday must load as today's empty state."""
    stale = QuotaState(
        date_utc=_yesterday(),
        pages_used=999,
        pages_quota=1000,
    )
    save_quota_state(tmp_path, stale)
    loaded = load_quota_state(tmp_path)
    assert loaded.date_utc == _today()
    assert loaded.pages_used == 0
    assert loaded.pages_quota == 1000  # quota preserved across roll-over


def test_check_quota_rolls_over_stale_state() -> None:
    """``check_quota`` should treat a stale-dated state as a fresh day."""
    stale = QuotaState(
        date_utc=_yesterday(),
        pages_used=999,
        pages_quota=1000,
    )
    decision, _reason = check_quota(stale, estimated_pages=500)
    # 0 + 500 < 1000, so allow.
    assert decision is QuotaDecision.ALLOW


def test_record_pages_used_rolls_over_stale_state(tmp_path: Path) -> None:
    stale = QuotaState(
        date_utc=_yesterday(),
        pages_used=999,
        pages_quota=1000,
    )
    save_quota_state(tmp_path, stale)
    new_state = record_pages_used(tmp_path, stale, pages=100)
    assert new_state.date_utc == _today()
    assert new_state.pages_used == 100  # not 999 + 100


# ---------------------------------------------------------------------- #
# estimate_pdf_pages
# ---------------------------------------------------------------------- #


def _make_real_pdf(path: Path, *, pages: int) -> None:
    try:
        import pypdf
    except ImportError:
        pytest.skip("pypdf not installed")
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as f:
        writer.write(f)


def test_estimate_pdf_pages_returns_count(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    _make_real_pdf(pdf, pages=7)
    assert estimate_pdf_pages(pdf) == 7


def test_estimate_pdf_pages_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert estimate_pdf_pages(tmp_path / "nope.pdf") is None


def test_estimate_pdf_pages_returns_none_for_garbage(tmp_path: Path) -> None:
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"not a real PDF")
    assert estimate_pdf_pages(pdf) is None


def test_load_quota_state_returns_fresh_when_missing(tmp_path: Path) -> None:
    state = load_quota_state(tmp_path)
    assert state.date_utc == _today()
    assert state.pages_used == 0
    assert state.pages_quota == DEFAULT_QUOTA


def test_load_quota_state_recovers_from_corrupted_file(tmp_path: Path) -> None:
    path = quota_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    state = load_quota_state(tmp_path)
    assert state.date_utc == _today()
    assert state.pages_used == 0  # reset on corruption
