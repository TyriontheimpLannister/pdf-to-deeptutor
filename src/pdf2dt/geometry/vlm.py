"""Optional VLM enrichment for deterministic geometry extraction.

Rules always run first.  A VLM may add review-only visual inferences, but it
never replaces rule-derived relations or turns a visual claim into a given.

Safe-fallback contract
----------------------

``MiniMaxM3Provider`` and ``SenseNovaProvider`` return :class:`VlmResponse`
for every outcome, including malformed model output.  Callers must treat
``VlmResponse.error`` as the only signal of failure and must never let it
propagate as an exception.  ``HybridGeometryAnalyzer.analyze`` relies on this
to keep Stage 5 deterministic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..bookview.builder import BookItem
from .analyzer import GeometryAnalyzer
from .evidence import NON_PROMOTABLE_EVIDENCE, Evidence, ReviewState
from .models import GeometryFigure, GeometryRelation, RelationType, relation_key
from .resource_gate import _DEFAULT_MAX_IMAGE_BYTES, check_vlm_asset

_TRIGGER_TEXT_BACKED_CONFIDENCE = 0.5


# ---------------------------------------------------------------------- #
# Provider protocol and response model
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class VlmRelationCandidate:
    """A relation proposed by a model before evidence-safe merging."""

    relation_type: RelationType
    entities: list[str]
    confidence: float
    observation: str = ""


@dataclass
class VlmCallRecord:
    """Audit row for one VLM call (or skipped call)."""

    asset_id: str = ""
    asset_sha256: str = ""
    provider: str = ""
    model: str = ""
    endpoint: str = ""
    status: str = "skipped"
    skip_reason: str = ""
    error: str = ""
    request_bytes: int = 0
    context_chars: int = 0
    elapsed_ms: int = 0
    response_sha256: str = ""
    raw_response_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_sha256": self.asset_sha256,
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "status": self.status,
            "skip_reason": self.skip_reason,
            "error": self.error,
            "request_bytes": self.request_bytes,
            "context_chars": self.context_chars,
            "elapsed_ms": self.elapsed_ms,
            "response_sha256": self.response_sha256,
            "raw_response_path": self.raw_response_path,
        }


@dataclass
class VlmResponse:
    """Provider outcome. Errors are data so callers can safely fall back."""

    relations: list[VlmRelationCandidate] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    raw_response: str = ""
    error: str = ""


class GeometryVlmProvider(Protocol):
    """One-image geometry interpretation provider."""

    name: str
    model: str

    def analyze_image(self, image_path: Path, context: str) -> VlmResponse:
        ...


_PROMPT = """You inspect a geometry diagram. Return JSON only, with this schema:
{{"relations":[{{"type":"parallel|perpendicular|equal_length|equal_angle|midpoint|collinear|point_on_segment","entities":["AB","CD"],
"confidence":0.0,"observation":"short visual reason"}}],"observations":["optional cautious note"]}}

Only report relationships visible from an explicit diagram mark or clearly drawn
label. Do not infer mathematical facts from appearance alone. Use uppercase point
labels and segment names when visible. This result is review-only, not a proof.

Associated text (may be incomplete OCR):
{context}
"""


def _coerce_confidence(value: Any) -> tuple[float, bool]:
    """Return ``(score_in_[0,1], ok)`` for an arbitrary model field.

    Many string-to-float coercions raise ``ValueError`` on labels like
    ``"high"``.  We return ``ok=False`` so callers can drop the candidate
    rather than abort the loop.  ``None`` and missing values are treated as
    the lowest score and considered valid so simple models do not invalidate
    an otherwise good candidate.
    """
    if value is None:
        return 0.0, True
    if isinstance(value, bool):
        return 0.0, True
    if isinstance(value, (int, float)):
        score = float(value)
        if score != score or score in (float("inf"), float("-inf")):
            return 0.0, False
        return min(1.0, max(0.0, score)), True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0, True
        try:
            score = float(text)
        except ValueError:
            return 0.0, False
        return min(1.0, max(0.0, score)), True
    return 0.0, False


def _parse_response(text: str) -> VlmResponse:
    """Parse a provider response without trusting its relation vocabulary.

    Every parsing failure maps to ``VlmResponse(error=...)`` so a malformed
    model output never aborts the rules-first pipeline.  Individual bad
    relation candidates are silently dropped (one bad record must not kill
    the rest of the response).
    """
    cleaned = text.strip() if isinstance(text, str) else ""
    if not cleaned:
        return VlmResponse(raw_response="", error="empty response")
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    if not cleaned:
        return VlmResponse(raw_response=text, error="empty response after code fence strip")
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return VlmResponse(raw_response=text, error=f"invalid JSON response: {exc.msg}")
    if not isinstance(payload, dict):
        return VlmResponse(raw_response=text, error="response JSON must be an object")

    raw_relations = payload.get("relations")
    candidates: list[VlmRelationCandidate] = []
    invalid = 0
    if isinstance(raw_relations, list):
        for raw in raw_relations:
            if not isinstance(raw, dict):
                invalid += 1
                continue
            raw_type = raw.get("type")
            if not isinstance(raw_type, str) or not raw_type:
                invalid += 1
                continue
            try:
                relation_type = RelationType(raw_type)
            except ValueError:
                invalid += 1
                continue
            raw_entities = raw.get("entities")
            if not isinstance(raw_entities, list):
                invalid += 1
                continue
            entities = [
                str(entity).strip().upper()
                for entity in raw_entities
                if entity is not None
            ]
            entities = [entity for entity in entities if entity]
            if not entities:
                invalid += 1
                continue
            confidence, ok = _coerce_confidence(raw.get("confidence"))
            if not ok:
                invalid += 1
                continue
            observation_raw = raw.get("observation")
            observation = (
                str(observation_raw).strip()
                if isinstance(observation_raw, str)
                else ""
            )
            candidates.append(
                VlmRelationCandidate(
                    relation_type=relation_type,
                    entities=entities,
                    confidence=confidence,
                    observation=observation,
                )
            )
    else:
        invalid += 0  # missing relations is fine; downstream copes with [].
    raw_observations = payload.get("observations")
    observations: list[str] = []
    if isinstance(raw_observations, list):
        for value in raw_observations:
            if isinstance(value, str):
                observations.append(value)
    error = f"discarded {invalid} invalid relation(s)" if invalid else ""
    return VlmResponse(
        relations=candidates,
        observations=observations,
        raw_response=text,
        error=error,
    )


def _extract_message_text(content: Any) -> str:
    """Safely join text blocks from an Anthropic-style ``content`` field.

    Returns ``""`` when the field is missing, not iterable, or composed
    entirely of non-text blocks.  Never raises.
    """
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def _extract_sensenova_text(data: Any) -> str:
    """Safely extract the assistant message from a SenseNova response.

    Returns ``""`` when the response is missing required containers; never
    raises.
    """
    if not isinstance(data, dict):
        return ""
    data_field = data.get("data")
    if not isinstance(data_field, dict):
        return ""
    choices = data_field.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


# ---------------------------------------------------------------------- #
# Providers
# ---------------------------------------------------------------------- #


class MiniMaxM3Provider:
    """MiniMax-M3 over its Anthropic-compatible Messages endpoint."""

    name = "minimax-m3"
    endpoint = "https://api.minimaxi.com/anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 45.0,
        client: httpx.Client | None = None,
        max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("MINIMAX_API_KEY")
        self._base_url = (
            base_url.rstrip("/") if base_url else self.endpoint
        )
        self._model = model or os.environ.get("MINIMAX_VLM_MODEL", "MiniMax-M3")
        self._timeout = timeout_seconds
        self._client = client
        self._max_image_bytes = max_image_bytes

    @property
    def model(self) -> str:
        return self._model

    def analyze_image(self, image_path: Path, context: str) -> VlmResponse:
        if not self._api_key:
            return VlmResponse(error="MINIMAX_API_KEY is not set")
        gate = check_vlm_asset(
            image_path, max_image_bytes=self._max_image_bytes
        )
        if not gate.ok:
            return VlmResponse(error=f"asset_rejected: {gate.error}")
        try:
            image_bytes = image_path.read_bytes()
            encoded = base64.b64encode(image_bytes).decode("ascii")
        except OSError as exc:
            return VlmResponse(error=f"cannot read image: {exc}")
        media_type = gate.media_type or "application/octet-stream"
        payload = {
            "model": self._model,
            "max_tokens": 1200,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": _PROMPT.format(context=context[:8000])},
                    ],
                }
            ],
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            if self._client is None:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(
                        f"{self._base_url}/v1/messages", json=payload, headers=headers
                    )
            else:
                response = self._client.post(
                    f"{self._base_url}/v1/messages", json=payload, headers=headers
                )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                return VlmResponse(
                    raw_response=response.text,
                    error=f"MiniMax returned non-JSON body: {exc}",
                )
        except httpx.HTTPError as exc:
            return VlmResponse(error=f"MiniMax request failed: {exc}")
        text = _extract_message_text(data.get("content") if isinstance(data, dict) else None)
        if not text:
            return VlmResponse(
                raw_response=json.dumps(data) if isinstance(data, dict) else "",
                error="MiniMax returned no text block",
            )
        return _parse_response(text)


class SenseNovaProvider:
    """SenseNova multimodal chat-completions provider."""

    name = "sensenova"
    endpoint = "https://api.sensenova.cn/v1/llm/chat-completions"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 45.0,
        client: httpx.Client | None = None,
        max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get("SENSENOVA_API_KEY")
        )
        self._model = (
            model
            if model is not None
            else os.environ.get("SENSENOVA_VLM_MODEL", "SenseNova-6.7-Flash-Lite")
        )
        self._timeout = timeout_seconds
        self._client = client
        self._max_image_bytes = max_image_bytes

    @property
    def model(self) -> str:
        return self._model

    def analyze_image(self, image_path: Path, context: str) -> VlmResponse:
        if not self._api_key:
            return VlmResponse(error="SENSENOVA_API_KEY is not set")
        gate = check_vlm_asset(
            image_path, max_image_bytes=self._max_image_bytes
        )
        if not gate.ok:
            return VlmResponse(error=f"asset_rejected: {gate.error}")
        try:
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError as exc:
            return VlmResponse(error=f"cannot read image: {exc}")
        payload = {
            "model": self._model,
            "max_new_tokens": 1200,
            "temperature": 0,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_base64", "image_base64": encoded},
                        {"type": "text", "text": _PROMPT.format(context=context[:8000])},
                    ],
                }
            ],
        }
        try:
            if self._client is None:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(
                        self.endpoint,
                        json=payload,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
            else:
                response = self._client.post(
                    self.endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                return VlmResponse(
                    raw_response=response.text,
                    error=f"SenseNova returned non-JSON body: {exc}",
                )
        except httpx.HTTPError as exc:
            return VlmResponse(error=f"SenseNova request failed: {exc}")
        text = _extract_sensenova_text(data)
        if not text:
            return VlmResponse(raw_response=json.dumps(data), error="SenseNova returned no text")
        return _parse_response(text)


# ---------------------------------------------------------------------- #
# Hybrid analyzer: rules-first with VLM selection strategy
# ---------------------------------------------------------------------- #


def should_call_vlm(figure: GeometryFigure) -> tuple[bool, str]:
    """Decide whether a hybrid analyzer should pay for one VLM call.

    Rules already produced a confident, mostly text-backed answer: skip the
    paid call.  Rules came back empty, came back low-confidence, or left
    unresolved visual observations: visual may add evidence, call it.
    """
    if not figure.relations:
        if not figure.visual_observations:
            return True, "rules_blank"
        return True, "rules_blank_no_relations"
    promotable = [
        relation
        for relation in figure.relations
        if relation.evidence not in NON_PROMOTABLE_EVIDENCE
    ]
    if not promotable:
        return True, "rules_only_non_promotable"
    if any(
        relation.confidence < _TRIGGER_TEXT_BACKED_CONFIDENCE
        for relation in promotable
    ):
        return True, "rules_low_confidence"
    if figure.visual_observations:
        return True, "rules_have_visual_observations"
    return False, "rules_sufficient"


@dataclass
class HybridGeometryAnalyzer(GeometryAnalyzer):
    """Rules-first analyzer that adds review-only VLM candidates.

    Selection contract
    ------------------
    A VLM call is only paid for when the deterministic analyzer expresses
    uncertainty: no relations, only non-promotable evidence, low
    confidence, or unresolved visual observations.  A call record is
    written for every figure (whether the VLM was called or skipped) so
    the audit log captures a justification for each.

    Conflict handling
    -----------------
    A VLM relation whose key collides with a rules relation of the same
    type is treated as confirmation (extra observation only).  A
    collision with a different type becomes a conflict candidate: the
    candidate is recorded as an ``unknown``-evidence relation so the
    review queue surfaces it.  Because ``unknown`` is non-promotable,
    :class:`apply_review` will refuse to auto-confirm the conflict —
    the reviewer must issue an explicit ``corrected`` or ``rejected``
    decision before the relation can be embedded in an export PDF.
    """

    provider: GeometryVlmProvider | None = None
    call_records: list[VlmCallRecord] = field(default_factory=list)
    raw_responses_dir: Path | None = None

    def analyze(
        self,
        *,
        item: BookItem,
        asset_id: str,
        caption: str = "",
        layout_labels: list[str] | None = None,
        asset_path: Path | None = None,
    ) -> GeometryFigure | None:
        figure = super().analyze(
            item=item,
            asset_id=asset_id,
            caption=caption,
            layout_labels=layout_labels,
            asset_path=asset_path,
        )
        if figure is None:
            return None

        call = VlmCallRecord(asset_id=asset_id)
        if self.provider is None:
            call.skip_reason = "no_provider"
            self.call_records.append(call)
            return figure
        if asset_path is None or not asset_path.is_file():
            call.skip_reason = "asset_unavailable"
            figure.visual_observations.append("vlm: asset file unavailable; rules used")
            self.call_records.append(call)
            return figure

        should_call, reason = should_call_vlm(figure)
        call.provider = self.provider.name
        call.model = getattr(self.provider, "model", "")
        call.endpoint = getattr(self.provider, "endpoint", "")
        if not should_call:
            call.status = "skipped"
            call.skip_reason = reason
            self.call_records.append(call)
            return figure

        call.asset_sha256 = _file_sha256(asset_path)
        context = "\n".join(filter(None, [item.title, item.text, caption]))
        call.context_chars = len(context)
        call.request_bytes = asset_path.stat().st_size if asset_path.exists() else 0

        start = time.perf_counter()
        response = self.provider.analyze_image(asset_path, context)
        call.elapsed_ms = int((time.perf_counter() - start) * 1000)
        call.error = response.error
        call.response_sha256 = _sha256(response.raw_response)
        if response.error:
            call.status = (
                "rejected" if response.error.startswith("asset_rejected:") else "failed"
            )
        else:
            call.status = "ok"

        if response.raw_response and self.raw_responses_dir is not None:
            try:
                self.raw_responses_dir.mkdir(parents=True, exist_ok=True)
                raw_path = self.raw_responses_dir / f"{figure.figure_id}.json"
                raw_path.write_text(
                    json.dumps(
                        {
                            "figure_id": figure.figure_id,
                            "asset_id": asset_id,
                            "asset_sha256": call.asset_sha256,
                            "provider": call.provider,
                            "model": call.model,
                            "endpoint": call.endpoint,
                            "request_bytes": call.request_bytes,
                            "context_chars": call.context_chars,
                            "elapsed_ms": call.elapsed_ms,
                            "status": call.status,
                            "error": call.error,
                            "response_sha256": call.response_sha256,
                            "raw_response": response.raw_response,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                call.raw_response_path = str(raw_path)
            except OSError:
                # Persistence must never abort the rules-first pipeline.
                pass

        self.call_records.append(call)

        if response.error:
            figure.visual_observations.append(
                f"vlm[{self.provider.name}]: {response.error}"
            )
            return figure

        existing_by_key: dict[str, GeometryRelation] = {
            relation.key: relation for relation in figure.relations
        }
        # Per (type, entities) the figure must hold *one* relation.
        # Decisions and the renderer both key on ``relation.key``, so
        # a second record with the same key would silently shadow the
        # first — codex P1 #2 follow-up.  We track which keys are
        # already on the figure and skip any candidate that would
        # duplicate.
        for candidate in response.relations:
            key = relation_key(candidate.relation_type, candidate.entities)
            if key in existing_by_key:
                # Same key + same type ⇒ already covered, note model
                # agreement in the audit log and move on.
                figure.visual_observations.append(
                    f"vlm[{self.provider.name}]: agrees on {key}"
                )
                continue

            # Decide between the three outcomes BEFORE appending:
            #   1. rule-vs-VLM conflict → ``unknown`` (non-promotable)
            #   2. otherwise            → ``visual_inference``
            # A conflict is *same entity set, different rule type*
            # on a relation that originated from a rules analyzer
            # (source_reference does not start with "vlm:").
            candidate_entities = set(candidate.entities)
            conflict = next(
                (
                    rel
                    for rel in figure.relations
                    if not rel.source_reference.startswith("vlm:")
                    and set(rel.entities) == candidate_entities
                    and rel.key != key
                ),
                None,
            )
            if conflict is not None:
                figure.relations.append(
                    GeometryRelation(
                        type=candidate.relation_type,
                        entities=list(candidate.entities),
                        evidence=Evidence.UNKNOWN,
                        source_reference=(
                            f"vlm:{self.provider.name}:conflict"
                        ),
                        confidence=candidate.confidence,
                        review_state=ReviewState.UNREVIEWED,
                        review_note=(
                            f"VLM conflict: rules said {conflict.key} "
                            f"but VLM says {key}"
                        ),
                    )
                )
                figure.visual_observations.append(
                    f"vlm[{self.provider.name}]: conflict on "
                    f"{sorted(candidate_entities)} "
                    f"({conflict.key} vs {key}) — review needed"
                )
            else:
                figure.relations.append(
                    GeometryRelation(
                        type=candidate.relation_type,
                        entities=list(candidate.entities),
                        evidence=Evidence.VISUAL_INFERENCE,
                        source_reference=f"vlm:{self.provider.name}",
                        confidence=candidate.confidence,
                        review_state=ReviewState.UNREVIEWED,
                        review_note=candidate.observation,
                    )
                )
            existing_by_key[key] = figure.relations[-1]

        figure.visual_observations.extend(
            f"vlm[{self.provider.name}]: {observation}"
            for observation in response.observations
        )
        return figure


def build_geometry_analyzer(name: str) -> GeometryAnalyzer:
    """Build a rules-only or hybrid analyzer from a CLI-safe provider name."""
    if name == "rules":
        return GeometryAnalyzer()
    if name == "hybrid-minimax-m3":
        return HybridGeometryAnalyzer(provider=MiniMaxM3Provider())
    if name == "hybrid-sensenova":
        return HybridGeometryAnalyzer(provider=SenseNovaProvider())
    raise ValueError(f"unknown geometry provider: {name}")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


__all__ = [
    "HybridGeometryAnalyzer",
    "GeometryVlmProvider",
    "MiniMaxM3Provider",
    "SenseNovaProvider",
    "VlmCallRecord",
    "VlmRelationCandidate",
    "VlmResponse",
    "build_geometry_analyzer",
    "should_call_vlm",
]
