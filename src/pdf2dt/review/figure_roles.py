"""Figure role classification — Stage 5 sidecar.

Each figure-bound :class:`~pdf2dt.bookview.builder.BookItem` is
classified as ``content``, ``decor``, or ``ambiguous`` by an
LLM-mediated annotator. The result lives in
``review/figure_roles.json`` and is consulted by the export renderer
to skip embedding figures that are purely decorative.

The module mirrors the safety guarantees of
:mod:`pdf2dt.geometry.vlm`: every model outcome — including errors
and malformed JSON — is recorded as data, never raised.  On failure
the role defaults to ``ambiguous``, which the renderer treats as
``content`` (safe default: never silently drop a figure that may
have been needed).

The classifier prompt is a single-shot ternary choice.  Few-shot
examples are inlined in the prompt so the model does not need to be
re-tuned per book.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import monotonic, sleep
from typing import Any, TextIO
from urllib.parse import unquote, urlsplit

from ..figure_roles.pre_filter import Evidence
from ..geometry.resource_gate import (
    _DEFAULT_MAX_IMAGE_BYTES,
    check_vlm_asset,
)
from ..geometry.vlm import (
    GeometryVlmProvider,
    VlmCallRecord,
)
from ..project import ProjectWorkspace
from .store import FigureRoleOverrideStore
from .template_decor import find_template_decor_assets


class FigureRole(str, Enum):
    CONTENT = "content"
    DECOR = "decor"
    AMBIGUOUS = "ambiguous"


def _normalized_image_path(value: str) -> str:
    """Normalize a local or Markdown image path for marker matching."""
    raw = unquote(str(value).strip())
    parsed = urlsplit(raw) if "://" in raw else None
    path = parsed.path if parsed is not None else raw
    path = path.split("?", 1)[0].split("#", 1)[0]
    return path.replace("\\", "/").strip()


def _local_image_context(
    text: str,
    local_path: str,
    asset_id: str,
    *,
    window: int = 200,
) -> str:
    """Return bounded OCR text around the candidate's Markdown image marker.

    MinerU may store a workspace-prefixed Windows path in ``asset_refs`` while
    the item's Markdown uses the shorter ``assets/<asset>.jpg`` spelling.
    Exact normalized paths are preferred; matching the filename stem is the
    conservative fallback. The whole item text is never returned.
    """
    if not text:
        return ""
    normalized_text = text.replace("\\", "/")
    expected_path = _normalized_image_path(local_path)
    expected_name = expected_path.rsplit("/", 1)[-1].lower()
    expected_stem = expected_name.rsplit(".", 1)[0]
    asset_token = unquote(str(asset_id)).strip().lower()
    markers = list(re.finditer(r"!\[image\]\(([^)]+)\)", normalized_text))
    for marker in markers:
        marker_path = _normalized_image_path(marker.group(1))
        marker_name = marker_path.rsplit("/", 1)[-1].lower()
        marker_stem = marker_name.rsplit(".", 1)[0]
        exact = bool(expected_path) and marker_path == expected_path
        same_name = bool(expected_name) and marker_name == expected_name
        same_asset = bool(asset_token) and marker_stem == asset_token
        if exact or same_name or same_asset or marker_stem == expected_stem:
            start = max(0, marker.start() - window)
            end = min(len(normalized_text), marker.end() + window)
            return normalized_text[start:end].strip()
    return ""


_ROLE_VALUES = {r.value for r in FigureRole}


class FigureRoleError(ValueError):
    """Raised when an override targets an unknown figure_id."""


@dataclass
class FigureRoleRecord:
    """One figure's role classification record."""

    figure_id: str
    asset_id: str
    asset_sha256: str
    role: FigureRole
    item_id: str = ""
    confidence: float = 0.0
    reason: str = ""
    model_id: str = ""
    request_id: str = ""
    classified_at: str = ""
    prefilter_skipped: bool = False
    prefilter_rule_id: str = ""
    prefilter_evidence: tuple[Evidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "asset_id": self.asset_id,
            "asset_sha256": self.asset_sha256,
            "item_id": self.item_id,
            "role": self.role.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "model_id": self.model_id,
            "request_id": self.request_id,
            "classified_at": self.classified_at,
            "prefilter_skipped": self.prefilter_skipped,
            "prefilter_rule_id": self.prefilter_rule_id,
            "prefilter_evidence": [
                evidence.to_dict() for evidence in self.prefilter_evidence
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FigureRoleRecord:
        role_raw = str(data.get("role") or FigureRole.AMBIGUOUS.value)
        if role_raw not in _ROLE_VALUES:
            role_raw = FigureRole.AMBIGUOUS.value
        try:
            confidence = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            figure_id=str(data.get("figure_id") or ""),
            asset_id=str(data.get("asset_id") or ""),
            asset_sha256=str(data.get("asset_sha256") or ""),
            item_id=str(data.get("item_id") or ""),
            role=FigureRole(role_raw),
            confidence=min(1.0, max(0.0, confidence)),
            reason=str(data.get("reason") or ""),
            model_id=str(data.get("model_id") or ""),
            request_id=str(data.get("request_id") or ""),
            classified_at=str(data.get("classified_at") or ""),
            prefilter_skipped=bool(data.get("prefilter_skipped", False)),
            prefilter_rule_id=str(data.get("prefilter_rule_id") or ""),
            prefilter_evidence=tuple(
                Evidence.from_dict(e)
                for e in data.get("prefilter_evidence") or []
                if isinstance(e, dict)
            ),
        )


_PROMPT = """You classify one figure from a Chinese math textbook into one of three roles.

ROLE DEFINITIONS:
- "content": the image conveys information needed to solve the problem
  (a geometry diagram, a number line, a bar chart, a quantity shown as
   circles/blocks, a labeled measurement, etc.). Removing the image
  makes the problem unsolvable or its solution ambiguous.
- "decor": the image is purely decorative — a cartoon character, an
  animal mascot, a banner, a border, a scene illustration, a 卡通人物
  next to a problem bubble. The problem can be solved without it.
- "ambiguous": you cannot tell without more context.

EXAMPLES:
Example 1: A cartoon panda with a speech bubble reading "你能算出来吗?"
next to a math problem → "decor", confidence 0.95, reason:
"cartoon mascot around a problem bubble, no math content in image"

Example 2: A right triangle with vertices labeled A, B, C and the right
angle marked at B → "content", confidence 0.99, reason:
"labeled geometry diagram with explicit angle mark"

Example 3: A row of five identical circles representing the number 5 →
"content", confidence 0.85, reason: "manipulable representation of a
quantity used in the problem"

Example 4: A blank decorative border with no math content → "decor",
confidence 0.95, reason: "decorative border only"

Example 5: A small unrecognizable thumbnail → "ambiguous", confidence
0.4, reason: "image too small to determine content"

CONTEXT (problem text near the figure, may be partial OCR):
{context}

Return ONLY a JSON object with this exact shape, no other text:
{{"role": "content|decor|ambiguous", "confidence": 0.0, "reason": "short reason"}}
"""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    return payload


def _coerce_role(value: Any) -> FigureRole:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _ROLE_VALUES:
            return FigureRole(v)
    return FigureRole.AMBIGUOUS


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        score = float(value)
        if score != score or score in (float("inf"), float("-inf")):
            return 0.0
        return min(1.0, max(0.0, score))
    if isinstance(value, str):
        try:
            return min(1.0, max(0.0, float(value.strip())))
        except ValueError:
            return 0.0
    return 0.0


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------- #
# File-backed role store
# ---------------------------------------------------------------------- #


_ROLE_FILENAME = "figure_roles.json"
_SCHEMA_VERSION = "figure_roles/v1"

# Common Chinese-language publisher / watermark phrases that show up
# in OCR text alongside purely decorative figures (page-header
# banners, marketing QR codes, publisher logos). Real math content
# never sits next to these phrases in isolation, so a mock provider
# can use them as a high-confidence decor signal. Real VLM
# providers should rely on the image itself, not this list.
DECOR_CONTEXT_PATTERNS: tuple[str, ...] = (
    "微信公众号 教辅资料站",
    "微信公众号",
    "教辅资料站",
    "学而思",
    "新东方",
    "高思教育",
)


class FigureRoleStore:
    """Persist figure role classifications to disk."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        *,
        path: Path | str | None = None,
    ) -> None:
        self._workspace = workspace
        self._path = (
            Path(path)
            if path
            else (workspace.review_dir / _ROLE_FILENAME)
        )

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[FigureRole]:
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [FigureRoleRecord.from_dict(r) for r in data.get("figures") or []]

    def index(self) -> dict[str, FigureRoleRecord]:
        return {r.figure_id: r for r in self.load()}

    def index_by_use(self) -> dict[tuple[str, str], FigureRoleRecord]:
        """Index context-aware records by ``(figure_id, item_id)``.

        Older role files do not carry ``item_id`` and are intentionally
        omitted; callers can fall back to :meth:`index` for those records.
        """
        return {
            (r.figure_id, r.item_id): r
            for r in self.load()
            if r.figure_id and r.item_id
        }

    def save(self, roles: Iterable[FigureRoleRecord]) -> list[FigureRoleRecord]:
        ordered = sorted(roles, key=lambda r: r.figure_id)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "project_id": self._workspace.root.name,
            "generated_at": _now(),
            "figures": [r.to_dict() for r in ordered],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ordered

    def merge_by_use(
        self, roles: Iterable[FigureRoleRecord]
    ) -> list[FigureRoleRecord]:
        """Replace only the supplied contextual records, retaining other audits."""
        replacements = list(roles)
        replacement_keys = {
            (role.figure_id, role.item_id)
            for role in replacements
            if role.figure_id and role.item_id
        }
        retained = [
            role
            for role in self.load()
            if (role.figure_id, role.item_id) not in replacement_keys
        ]
        return self.save([*retained, *replacements])


# ---------------------------------------------------------------------- #
# Annotator
# ---------------------------------------------------------------------- #


@dataclass
class FigureRoleAnnotator:
    """Run an LLM provider across figure-bound book items.

    The annotator never raises on provider failure — a failure maps to
    role=ambiguous with reason=provider_error so the renderer treats
    the figure as content (safe default). Results are cached on disk
    keyed by ``(asset_sha256, model_id, prompt_hash, item_context_hash)``
    so the same asset can be reviewed separately under different items.
    """

    provider: GeometryVlmProvider
    workspace: ProjectWorkspace
    cache_dir: Path | None = None
    cache_enabled: bool = True
    max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES
    max_provider_retries: int = 2
    retry_backoff_seconds: float = 1.0
    call_records: list[VlmCallRecord] = field(default_factory=list)

    _PROMPT_HASH = hashlib.sha256(_PROMPT.encode("utf-8")).hexdigest()

    def _cache_key(self, asset_sha256: str, context: str = "") -> str:
        context_hash = hashlib.sha256((context or "").encode("utf-8")).hexdigest()
        return hashlib.sha256(
            "{}|{}|{}".format(
                asset_sha256,
                getattr(self.provider, "model", ""),
                f"{self._PROMPT_HASH}|{context_hash}",
            ).encode("utf-8")
        ).hexdigest()

    def _cache_path(self, asset_sha256: str, context: str = "") -> Path | None:
        if not self.cache_enabled:
            return None
        base = (
            self.cache_dir
            if self.cache_dir is not None
            else (self.workspace.root / "providers" / "vlm" / "figure_role_cache")
        )
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{self._cache_key(asset_sha256, context)}.json"

    def _read_cache(self, asset_sha256: str, context: str = "") -> dict[str, Any] | None:
        p = self._cache_path(asset_sha256, context)
        if p is None or not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(
        self, asset_sha256: str, payload: dict[str, Any], context: str = ""
    ) -> None:
        p = self._cache_path(asset_sha256, context)
        if p is None:
            return
        p.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def classify_one(
        self,
        *,
        figure_id: str,
        asset_id: str,
        asset_path: Path,
        item_id: str = "",
        context: str = "",
    ) -> FigureRole:
        """Classify a single figure. Always returns a FigureRole."""
        if self.max_provider_retries < 0:
            raise ValueError("max_provider_retries must be non-negative")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
        record = VlmCallRecord(asset_id=asset_id)
        record.provider = getattr(self.provider, "name", "")
        record.model = getattr(self.provider, "model", "")
        record.endpoint = getattr(self.provider, "endpoint", "")
        record.context_chars = len(context or "")

        if not asset_path.is_file():
            record.skip_reason = "asset_unavailable"
            record.status = "skipped"
            self.call_records.append(record)
            return FigureRoleRecord(                figure_id=figure_id,
                asset_id=asset_id,
                asset_sha256="",
                item_id=item_id,
                role=FigureRole.AMBIGUOUS,
                confidence=0.0,
                reason="asset_unavailable",
                model_id=record.model,
                classified_at=_now(),
            )

        try:
            asset_sha256 = _sha256_file(asset_path)
        except OSError:
            asset_sha256 = ""
        record.asset_sha256 = asset_sha256

        cached = self._read_cache(asset_sha256, context) if asset_sha256 else None
        if cached is not None:
            record.status = "cached"
            record.response_sha256 = hashlib.sha256(
                json.dumps(cached, sort_keys=True).encode("utf-8")
            ).hexdigest()
            self.call_records.append(record)
            return FigureRoleRecord(                figure_id=figure_id,
                asset_id=asset_id,
                asset_sha256=asset_sha256,
                item_id=item_id,
                role=_coerce_role(cached.get("role")),
                confidence=_coerce_confidence(cached.get("confidence")),
                reason=str(cached.get("reason") or ""),
                model_id=str(cached.get("model_id") or record.model),
                request_id="cache",
                classified_at=_now(),
            )

        gate = check_vlm_asset(asset_path, max_image_bytes=self.max_image_bytes)
        if not gate.ok:
            record.skip_reason = f"asset_rejected: {gate.error}"
            record.status = "rejected"
            self.call_records.append(record)
            return FigureRoleRecord(                figure_id=figure_id,
                asset_id=asset_id,
                asset_sha256=asset_sha256,
                item_id=item_id,
                role=FigureRole.AMBIGUOUS,
                confidence=0.0,
                reason=f"asset_rejected: {gate.error}",
                model_id=record.model,
                classified_at=_now(),
            )

        record.request_bytes = asset_path.stat().st_size
        response = None
        for attempt in range(self.max_provider_retries + 1):
            try:
                response = self.provider.analyze_image(
                    asset_path, _PROMPT.format(context=context[:8000])
                )
            except Exception as exc:  # pragma: no cover — defensive
                record.status = "failed"
                record.error = f"provider_exception: {exc}"
                if attempt < self.max_provider_retries:
                    sleep(self.retry_backoff_seconds * (2**attempt))
                    continue
                self.call_records.append(record)
                return FigureRoleRecord(
                    figure_id=figure_id,
                    asset_id=asset_id,
                    asset_sha256=asset_sha256,
                    item_id=item_id,
                    role=FigureRole.AMBIGUOUS,
                    confidence=0.0,
                    reason="provider_error",
                    model_id=record.model,
                    classified_at=_now(),
                )

            if response.error:
                record.status = "failed"
                record.error = response.error
                if attempt < self.max_provider_retries:
                    sleep(self.retry_backoff_seconds * (2**attempt))
                    continue
                self.call_records.append(record)
                return FigureRoleRecord(
                    figure_id=figure_id,
                    asset_id=asset_id,
                    asset_sha256=asset_sha256,
                    item_id=item_id,
                    role=FigureRole.AMBIGUOUS,
                    confidence=0.0,
                    reason="provider_error",
                    model_id=record.model,
                    classified_at=_now(),
                )
            break

        record.status = "ok"
        record.raw_response_path = ""
        record.response_sha256 = hashlib.sha256(
            (response.raw_response or "").encode("utf-8")
        ).hexdigest()

        payload = _extract_json_object(response.raw_response or "")
        if payload is None or "role" not in payload:
            record.error = "schema_error: missing role in response"
            self.call_records.append(record)
            return FigureRoleRecord(
                figure_id=figure_id,
                asset_id=asset_id,
                asset_sha256=asset_sha256,
                item_id=item_id,
                role=FigureRole.AMBIGUOUS,
                confidence=0.0,
                reason="schema_error",
                model_id=record.model,
                classified_at=_now(),
            )

        role = _coerce_role(payload.get("role"))
        confidence = _coerce_confidence(payload.get("confidence"))
        reason = str(payload.get("reason") or "")

        cache_payload = {
            "role": role.value,
            "confidence": confidence,
            "reason": reason,
            "model_id": record.model,
        }
        self._write_cache(asset_sha256, cache_payload, context)
        self.call_records.append(record)
        return FigureRoleRecord(            figure_id=figure_id,
            asset_id=asset_id,
            asset_sha256=asset_sha256,
            item_id=item_id,
            role=role,
            confidence=confidence,
            reason=reason,
            model_id=record.model,
            request_id=record.response_sha256[:12],
            classified_at=_now(),
        )


# ---------------------------------------------------------------------- #
# BookView-driven classification
# ---------------------------------------------------------------------- #


@dataclass
class _FigureCandidate:
    figure_id: str
    asset_id: str
    item_id: str
    context: str
    local_context: str = ""


def _iter_figure_candidates(
    book_view: dict[str, Any],
    image_to_preceding_heading: dict[str, str] | None = None,
    image_local_contexts: dict[str, str] | None = None,
) -> Iterable[_FigureCandidate]:
    """Yield one candidate per figure-bearing item.

    Book view may nest items in either a flat ``items`` list (older
    shape) or a ``chapters[].sections[].items[]`` hierarchy (current
    shape). We walk both to stay compatible with whatever the
    workspace happens to have.

    ``image_to_preceding_heading`` is an optional lookup from image
    local path to the most recent markdown ``##`` / ``###`` heading
    text that appears before the image in ``normalized/full.md``. The
    mock figure-role provider uses this to recognise publisher
    section-banner images (e.g. images placed under ``## 练习`` or
    ``## 本讲知识点汇总``) that the book view's coarse
    chapter/section structure does not surface on each asset_ref.
    """
    seen_items: set[str] = set()

    def _walk_items(items: list[dict[str, Any]]) -> Iterable[_FigureCandidate]:
        for item in items:
            asset_refs = item.get("asset_refs") or []
            if not asset_refs:
                continue
            item_id = str(item.get("item_id") or "")
            if item_id and item_id in seen_items:
                continue
            if item_id:
                seen_items.add(item_id)
            title = (item.get("title") or "").strip()
            text = (item.get("text") or "").strip()
            item_type = (item.get("item_type") or "").strip()
            chapter_path = item.get("chapter_path") or []
            chapter_title = chapter_path[0] if chapter_path else ""
            section_title = (
                chapter_path[1] if len(chapter_path) > 1 else ""
            )
            # Structured metadata block the mock figure-role provider
            # can pattern-match. Real VLM providers treat it as
            # ordinary text and ignore it; it does not change the
            # rendered PDF.
            meta_lines = [
                f"[item_type:{item_type}]" if item_type else "",
                f"[chapter:{chapter_title}]" if chapter_title else "",
                f"[section:{section_title}]" if section_title else "",
                f"[title:{title}]" if title else "",
            ]
            for asset in asset_refs:
                aid = asset.get("asset_id")
                if not aid:
                    continue
                # Crop the context to the slice of text that
                # immediately surrounds the image's markdown marker,
                # so a banner image next to the section title is
                # *not* drowned out by later example text. We use
                # the asset's local_path (or asset_id fallback) as
                # the marker fragment.
                local_path = asset.get("local_path") or ""
                surrounding = _local_image_context(text, str(local_path), str(aid))
                if not surrounding and image_local_contexts:
                    surrounding = image_local_contexts.get(str(aid), "")
                # Resolve the *actual* heading under which the image
                # sits in the source markdown, when the caller
                # provided the lookup. The book view's coarse
                # chapter/section structure often groups chapter
                # heading AND several example problems under a single
                # chapter item, hiding the image's true section from
                # the mock provider. Walking the original markdown
                # gives the mock a precise "this image lives under
                # 练习 / 本讲知识点汇总" signal.
                actual_section = ""
                if image_to_preceding_heading is not None:
                    actual_section = image_to_preceding_heading.get(
                        local_path, ""
                    )
                context_parts = list(meta_lines)
                if actual_section and actual_section != section_title:
                    context_parts.append(f"[actual_section:{actual_section}]")
                if title:
                    context_parts.append(title)
                if surrounding:
                    context_parts.append(surrounding)
                context = "\n".join(context_parts)
                fid = str(asset.get("figure_id") or aid)
                yield _FigureCandidate(
                    figure_id=fid,
                    asset_id=str(aid),
                    item_id=item_id,
                    context=context,
                    local_context=surrounding,
                )

    flat = book_view.get("items")
    if isinstance(flat, list) and flat:
        yield from _walk_items(flat)
    chapters = book_view.get("chapters") or []
    for chapter in chapters:
        sections = chapter.get("sections") or []
        for section in sections:
            inner_items = section.get("items") or []
            yield from _walk_items(inner_items)


def load_book_view(workspace: ProjectWorkspace) -> dict[str, Any]:
    p = workspace.book_view_dir / "book_view.json"
    if not p.is_file():
        raise FileNotFoundError(f"book view not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def build_image_to_preceding_heading(
    workspace: ProjectWorkspace,
) -> dict[str, str]:
    """Walk ``normalized/full.md`` and build a local_path → heading map.

    For every ``![image](<local_path>)`` marker, the map records the
    most recent ``##`` / ``###`` (or bare ``#``) heading text that
    appears before the marker in the file. Returns an empty dict if
    the markdown is missing.
    """
    md_path = workspace.normalized_dir / "full.md"
    if not md_path.is_file():
        return {}
    heading = ""
    out: dict[str, str] = {}
    for line in md_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # Strip leading # and whitespace.
            heading = stripped.lstrip("#").strip()
            continue
        if "![image](" not in stripped:
            continue
        # Extract the local path inside the image marker.
        marker_start = stripped.index("![image](") + len("![image](")
        marker_end = stripped.find(")", marker_start)
        if marker_end < 0:
            continue
        local_path = stripped[marker_start:marker_end].strip()
        if local_path and local_path not in out:
            out[local_path] = heading
    return out


def build_image_to_local_contexts(workspace: ProjectWorkspace) -> dict[str, str]:
    """Build bounded OCR context keyed by asset id from normalized Markdown.

    BookView item text may omit image markers that are present in
    ``normalized/full.md``. This index supplies a provider context fallback
    without expanding the context to the entire item or document.
    """
    md_path = workspace.normalized_dir / "full.md"
    if not md_path.is_file():
        return {}
    text = md_path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for marker in re.finditer(r"!\[image\]\(([^)]+)\)", text):
        marker_path = _normalized_image_path(marker.group(1))
        marker_name = marker_path.rsplit("/", 1)[-1]
        asset_id = marker_name.rsplit(".", 1)[0]
        if asset_id and asset_id not in out:
            out[asset_id] = _local_image_context(text, marker.group(1), asset_id)
    return out


def resolve_asset_path(
    workspace: ProjectWorkspace,
    assets_registry: dict[str, dict[str, Any]],
    asset_id: str,
) -> Path | None:
    asset = assets_registry.get(asset_id)
    if not asset:
        return None
    local = asset.get("local_path")
    if not local:
        return None
    p = workspace.root / local
    if p.is_file():
        return p
    fallback = Path(local)
    if fallback.is_file():
        return fallback
    return None


def load_assets_registry(workspace: ProjectWorkspace) -> dict[str, dict[str, Any]]:
    p = workspace.normalized_dir / "assets_registry.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        str(a.get("asset_id")): a
        for a in (data.get("assets") or [])
        if a.get("asset_id")
    }


def _load_export_scoped_use_keys(workspace: ProjectWorkspace) -> set[tuple[str, str]]:
    """Return ``(figure_id, item_id)`` pairs used by the current export plan."""
    plans_path = workspace.export_plans_dir / "plans.json"
    if not plans_path.is_file():
        raise FileNotFoundError(
            f"export plans not found: {plans_path}; run Stage 4c first"
        )
    try:
        data = json.loads(plans_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"invalid export plans: {plans_path}") from exc

    use_keys: set[tuple[str, str]] = set()
    for plan in data.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        for item in plan.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_id = item.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                continue
            for asset in item.get("asset_refs") or []:
                if not isinstance(asset, dict):
                    continue
                figure_id = asset.get("figure_id") or asset.get("asset_id")
                if isinstance(figure_id, str) and figure_id:
                    use_keys.add((figure_id, item_id))
    return use_keys


def classify_figure_roles(
    workspace: ProjectWorkspace,
    *,
    provider: GeometryVlmProvider,
    cache_dir: Path | None = None,
    cache_enabled: bool = True,
    max_images: int | None = None,
    enable_template_decor_skip: bool = False,
    export_scoped: bool = False,
    progress_stream: TextIO | None = None,
    max_concurrency: int = 1,
) -> list[FigureRole]:
    """Run the annotator over every figure-bound BookItem in the workspace.

    Returns the list of persisted :class:`FigureRole` records, including
    ambiguous fallbacks for figures whose asset is missing or whose
    provider call failed. The store is rewritten atomically at the end
    so partial failures are not persisted.

    ``cache_enabled=False`` disables the on-disk cache for this run so
    the provider is called fresh for every figure (useful when iterating
    on the provider prompt).

    ``enable_template_decor_skip`` is an explicit opt-in for the reviewed
    repeated-template audit. No other pre-filter rule can skip a provider
    call through this entry point.

    When ``progress_stream`` is provided, emit one in-place progress line
    after each candidate. The callback is deliberately a stream rather than
    a logger so interrupted CLI runs retain an accurate last completed count
    without changing classification or persistence semantics.

    ``max_concurrency`` bounds provider calls. The default remains one for
    conservative compatibility; callers may raise it for providers that
    tolerate parallel requests. Results are restored to candidate order
    before persistence.

    ``export_scoped=True`` restricts classification to figure/item uses in
    the current Stage 4c plans. Existing human overrides and approved
    repeated-template decor are excluded from provider calls, and new records
    merge into the prior audit instead of replacing unrelated contexts.
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    book_view = load_book_view(workspace)
    registry = load_assets_registry(workspace)
    image_to_heading = build_image_to_preceding_heading(workspace)
    image_local_contexts = build_image_to_local_contexts(workspace)
    template_decor_ids: frozenset[str] = frozenset()
    if enable_template_decor_skip and registry:
        asset_paths = [
            (asset_id, asset_path)
            for asset_id in sorted(registry)
            if (asset_path := resolve_asset_path(workspace, registry, asset_id))
            is not None
        ]
        template_decor_ids = frozenset(find_template_decor_assets(asset_paths))
    annotator = FigureRoleAnnotator(
        provider=provider,
        workspace=workspace,
        cache_dir=cache_dir,
        cache_enabled=cache_enabled,
    )
    roles: list[FigureRole] = []
    all_candidates = list(
        _iter_figure_candidates(book_view, image_to_heading, image_local_contexts)
    )
    scoped_report: dict[str, Any] | None = None
    candidates = all_candidates
    if export_scoped:
        active_use_keys = _load_export_scoped_use_keys(workspace)
        override_ids = set(FigureRoleOverrideStore(workspace).index())
        active_candidates = [
            candidate
            for candidate in all_candidates
            if (candidate.figure_id, candidate.item_id) in active_use_keys
        ]
        excluded_overrides = [
            candidate
            for candidate in active_candidates
            if candidate.figure_id in override_ids
        ]
        excluded_templates = [
            candidate
            for candidate in active_candidates
            if candidate.asset_id in template_decor_ids
            and candidate.figure_id not in override_ids
        ]
        candidates = [
            candidate
            for candidate in active_candidates
            if candidate.figure_id not in override_ids
            and candidate.asset_id not in template_decor_ids
        ]
        scoped_report = {
            "generated_at": _now(),
            "export_plan_path": str(
                (workspace.export_plans_dir / "plans.json").relative_to(workspace.root)
            ),
            "bookview_candidates": len(all_candidates),
            "active_export_uses": len(active_candidates),
            "excluded_human_overrides": len(excluded_overrides),
            "excluded_template_decor": len(excluded_templates),
            "provider_candidates": len(candidates),
        }
    if max_images is not None:
        candidates = candidates[:max_images]
    if scoped_report is not None:
        scoped_report["selected_candidates"] = len(candidates)
    total = len(candidates)
    started = monotonic()
    counts = {"decor": 0, "content": 0, "ambiguous": 0, "errors": 0, "skipped": 0}

    def report_progress(processed: int) -> None:
        if progress_stream is None:
            return
        elapsed = monotonic() - started
        rate = processed / elapsed if elapsed > 0 else 0.0
        remaining = total - processed
        eta = remaining / rate if rate > 0 else 0.0
        progress_stream.write(
            "\r[figure-role] "
            f"processed={processed}/{total} remaining={remaining} "
            f"decor={counts['decor']} content={counts['content']} "
            f"ambiguous={counts['ambiguous']} skipped={counts['skipped']} "
            f"errors={counts['errors']} eta={eta:.0f}s"
        )
        progress_stream.flush()

    def classify_candidate(
        index: int, cand: Any
    ) -> tuple[int, FigureRole, list[VlmCallRecord]]:
        asset_path = resolve_asset_path(workspace, registry, cand.asset_id)
        if enable_template_decor_skip and cand.asset_id in template_decor_ids:
            record = VlmCallRecord(asset_id=cand.asset_id)
            record.status = "skipped"
            record.skip_reason = "pre_filter:template_decor"
            role = FigureRoleRecord(
                figure_id=cand.figure_id,
                asset_id=cand.asset_id,
                asset_sha256=str(
                    (registry.get(cand.asset_id) or {}).get("sha256") or ""
                ),
                item_id=cand.item_id,
                role=FigureRole.DECOR,
                confidence=0.0,
                reason=(
                    "pre_filter: repeated template decoration; "
                    "provider call skipped"
                ),
                model_id="pre_filter",
                prefilter_skipped=True,
                prefilter_rule_id="template_decor",
                prefilter_evidence=(
                    Evidence("detector", "find_template_decor_assets"),
                    Evidence("asset_id", cand.asset_id),
                ),
            )
            return index, role, [record]
        else:
            local_annotator = FigureRoleAnnotator(
                provider=provider,
                workspace=workspace,
                cache_dir=cache_dir,
                cache_enabled=cache_enabled,
            )
            role = local_annotator.classify_one(
                figure_id=cand.figure_id,
                asset_id=cand.asset_id,
                asset_path=asset_path or Path("(missing)"),
                item_id=cand.item_id,
                context=cand.context,
            )
            return index, role, local_annotator.call_records

    completed: list[FigureRole | None] = [None] * total
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures = [
            executor.submit(classify_candidate, index, cand)
            for index, cand in enumerate(candidates)
        ]
        for processed, future in enumerate(as_completed(futures), start=1):
            index, role, call_records = future.result()
            completed[index] = role
            annotator.call_records.extend(call_records)
            counts[role.role.value] += 1
            counts["skipped"] += int(role.prefilter_skipped)
            counts["errors"] += int(
                role.reason
                in {
                    "provider_error",
                    "schema_error",
                    "asset_unavailable",
                    "asset_rejected",
                }
            )
            report_progress(processed)
    roles = [role for role in completed if role is not None]
    if progress_stream is not None:
        progress_stream.write("\n")
        progress_stream.flush()
    store = FigureRoleStore(workspace)
    if export_scoped:
        store.merge_by_use(roles)
        assert scoped_report is not None
        scoped_report["classified"] = len(roles)
        scoped_report["distribution"] = {
            role: counts[role]
            for role in ("decor", "content", "ambiguous")
        }
        reports_dir = workspace.reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "export_scoped_figure_roles.json").write_text(
            json.dumps(scoped_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        store.save(roles)
    return roles


# ---------------------------------------------------------------------- #
# Effective role lookup (with override hook)
# ---------------------------------------------------------------------- #


def effective_role(
    figure_id: str,
    roles_by_id: dict[str, FigureRole],
    overrides: dict[str, FigureRole] | None = None,
) -> FigureRole:
    """Return the role that the renderer should honour for ``figure_id``.

    Order of precedence:

    1. Explicit override from the user's review decisions.
    2. Persisted classification for this figure_id.
    3. Ambiguous fallback (renderer treats this as content).
    """
    if overrides and figure_id in overrides:
        return overrides[figure_id]
    if figure_id in roles_by_id:
        return roles_by_id[figure_id]
    return FigureRoleRecord(        figure_id=figure_id,
        asset_id="",
        asset_sha256="",
        role=FigureRole.AMBIGUOUS,
        reason="no_record",
    )


def effective_role_for_use(
    figure_id: str,
    item_id: str,
    roles_by_use: dict[tuple[str, str], FigureRoleRecord],
    roles_by_id: dict[str, FigureRoleRecord],
    overrides: dict[str, FigureRoleRecord] | None = None,
) -> FigureRoleRecord:
    """Resolve a role for one asset occurrence in one book item.

    Explicit global overrides win first. Context-aware VLM records are
    preferred for the current item, then legacy asset-wide records remain a
    backward-compatible fallback.
    """
    if overrides and figure_id in overrides:
        return overrides[figure_id]
    record = roles_by_use.get((figure_id, item_id))
    if record is not None:
        return record
    return effective_role(figure_id, roles_by_id, overrides)


__all__ = [
    "FigureRole",
    "FigureRoleRecord",
    "FigureRoleError",
    "FigureRoleAnnotator",
    "FigureRoleStore",
    "classify_figure_roles",
    "effective_role",
    "effective_role_for_use",
    "load_assets_registry",
    "load_book_view",
    "resolve_asset_path",
]
