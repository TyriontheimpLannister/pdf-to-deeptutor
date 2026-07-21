"""Daily-quota tracking for the MinerU API.

MinerU's cloud API enforces a daily quota of 1000 pages that is
**shared with the web UI**. We can't see web UI usage from the API,
so the local state is best-effort:

* Pre-submit check — refuse when ``pages_used + estimated > quota``.
* Post-submit reconciliation — update ``pages_used`` from the actual
  page count reported by the API once a task finishes.
* Date roll-over — when the local ``date_utc`` no longer matches
  today's UTC date, the counter resets to 0 (the API itself does the
  same roll-over server-side).

State is persisted as JSON so the counter survives CLI invocations:

```
providers/mineru/quota_state.json
{
  "schema_version": "quota/v1",
  "date_utc": "2026-07-14",
  "pages_used": 480,
  "pages_quota": 1000,
  "updated_at": "2026-07-14T08:31:02Z"
}
```

The file is workspace-scoped. There is no global counter because the
user may run several unrelated projects in parallel and each only
knows its own submissions.
"""
from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "quota/v1"
DEFAULT_QUOTA = 1000
_DEFAULT_FILENAME = "quota_state.json"
_DEFAULT_SUBDIR = "providers" / Path("mineru")


class QuotaDecision(str, enum.Enum):
    """Result of a pre-submit check."""

    ALLOW = "allow"
    REFUSE_PRE = "refuse_pre"
    WARN_LIVE = "warn_live"


@dataclass
class QuotaState:
    """Mutable quota counter persisted to disk."""

    date_utc: str
    pages_used: int = 0
    pages_quota: int = DEFAULT_QUOTA
    updated_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def remaining(self) -> int:
        return max(0, self.pages_quota - self.pages_used)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "date_utc": self.date_utc,
            "pages_used": int(self.pages_used),
            "pages_quota": int(self.pages_quota),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuotaState:
        return cls(
            date_utc=str(data.get("date_utc") or _today_utc()),
            pages_used=int(data.get("pages_used") or 0),
            pages_quota=int(data.get("pages_quota") or DEFAULT_QUOTA),
            updated_at=str(data.get("updated_at") or ""),
            raw=data,
        )


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------- #
# Persistence
# ---------------------------------------------------------------------- #


def quota_path(workspace_root: Path | str) -> Path:
    """Default location: ``<workspace_root>/providers/mineru/quota_state.json``."""
    root = Path(workspace_root)
    return root / _DEFAULT_SUBDIR / _DEFAULT_FILENAME


def load_quota_state(workspace_root: Path | str) -> QuotaState:
    """Load the persisted quota state. If missing, returns today's empty state.

    If the persisted ``date_utc`` is older than today, the counter is
    reset to 0 (UTC roll-over).
    """
    path = quota_path(workspace_root)
    if not path.is_file():
        return QuotaState(date_utc=_today_utc(), updated_at=_now_iso())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("quota state at %s unreadable (%s); resetting", path, exc)
        return QuotaState(date_utc=_today_utc(), updated_at=_now_iso())
    state = QuotaState.from_dict(data)
    if state.date_utc != _today_utc():
        # UTC date rolled over; the server-side counter also reset.
        logger.info(
            "quota date roll-over detected: persisted=%s, today=%s — resetting",
            state.date_utc,
            _today_utc(),
        )
        state = QuotaState(
            date_utc=_today_utc(),
            pages_used=0,
            pages_quota=state.pages_quota,
            updated_at=_now_iso(),
        )
    return state


def save_quota_state(workspace_root: Path | str, state: QuotaState) -> Path:
    """Persist ``state`` to disk and return the path written."""
    path = quota_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = _now_iso()
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------- #
# Decision logic
# ---------------------------------------------------------------------- #


def check_quota(state: QuotaState, estimated_pages: int | None) -> tuple[QuotaDecision, str]:
    """Pre-submit decision against the local quota state.

    Returns ``(decision, reason)``. The reason is human-readable and
    safe to print to the user. Callers must persist a new state via
    :func:`record_pages_used` after a successful submit.
    """
    today = _today_utc()
    if state.date_utc != today:
        # State was loaded for a previous UTC day; treat as a fresh day.
        state = QuotaState(
            date_utc=today,
            pages_used=0,
            pages_quota=state.pages_quota,
            updated_at=state.updated_at,
        )
    if estimated_pages is None:
        # Can't estimate (scanned PDF without a parseable page tree).
        # Let the API decide and warn the user.
        if state.pages_used >= state.pages_quota:
            return (
                QuotaDecision.REFUSE_PRE,
                f"Local quota already exhausted ({state.pages_used}/"
                f"{state.pages_quota} pages today); cannot estimate pages "
                "for an unstructured PDF.",
            )
        return (
            QuotaDecision.WARN_LIVE,
            f"Could not estimate page count; submitting anyway. "
            f"Today's known usage: {state.pages_used}/{state.pages_quota}.",
        )
    if estimated_pages <= 0:
        return (QuotaDecision.ALLOW, "estimated 0 pages; nothing to do.")
    if state.pages_used + estimated_pages > state.pages_quota:
        return (
            QuotaDecision.REFUSE_PRE,
            f"Refusing: estimated {estimated_pages} pages + "
            f"{state.pages_used} already used today > "
            f"{state.pages_quota} quota. Submit on a later UTC date or "
            "upgrade your MinerU plan.",
        )
    return (
        QuotaDecision.ALLOW,
        f"Estimated {estimated_pages} pages + {state.pages_used} used "
        f"= {state.pages_used + estimated_pages} / {state.pages_quota} "
        "today.",
    )


def record_pages_used(
    workspace_root: Path | str,
    state: QuotaState,
    pages: int,
) -> QuotaState:
    """Add ``pages`` to the local counter and persist. Returns the new state."""
    today = _today_utc()
    if state.date_utc != today:
        # Roll over before recording.
        state = QuotaState(
            date_utc=today,
            pages_used=0,
            pages_quota=state.pages_quota,
            updated_at=state.updated_at,
        )
    state.pages_used = max(0, state.pages_used + max(0, int(pages)))
    save_quota_state(workspace_root, state)
    return state


# ---------------------------------------------------------------------- #
# PDF page estimation
# ---------------------------------------------------------------------- #


def estimate_pdf_pages(pdf_path: Path | str) -> int | None:
    """Best-effort page count using pypdf.

    Returns ``None`` if pypdf is unavailable or the PDF page tree
    cannot be parsed. The caller treats ``None`` as WARN_LIVE.
    """
    path = Path(pdf_path)
    if not path.is_file():
        return None
    try:
        import pypdf  # local import keeps the dep optional
    except ImportError:
        logger.warning("pypdf unavailable; cannot estimate PDF page count")
        return None
    try:
        reader = pypdf.PdfReader(str(path))
        return len(reader.pages)
    except Exception as exc:  # noqa: BLE001 — best-effort estimation
        logger.warning("pypdf could not read %s: %s", path, exc)
        return None


__all__ = [
    "DEFAULT_QUOTA",
    "QuotaDecision",
    "QuotaState",
    "SCHEMA_VERSION",
    "check_quota",
    "estimate_pdf_pages",
    "load_quota_state",
    "quota_path",
    "record_pages_used",
    "save_quota_state",
]
