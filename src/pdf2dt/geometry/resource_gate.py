"""Pre-flight resource gates for VLM submissions.

Both VLM providers base64-encode every asset before sending it to the
remote service. The audit (P1 #2) found no MIME whitelist, byte cap or
decoded-pixel cap, so a single oversized figure could exceed the provider
request limit (``MiniMax-M3``: 64 MB request, 10 MB image per file).

Gating contract
---------------

``check_vlm_asset`` validates an asset file before :func:`read_bytes` so a
bad asset becomes a :class:`VlmResponse(error=...)` instead of an
exception that aborts Stage 5.  When the gate fails, the rules-first
analyzer still finishes; the failure is recorded in the VLM call audit
log alongside the rest of the call metadata.

MIME detection is real: the suffix is treated as a hint, but the actual
``media_type`` returned to the provider is derived from Pillow's image
format detection (``PIL.Image.open().format``).  When the suffix
disagrees with the detected format, the asset is rejected — codex P1
#1 (a JPEG renamed to ``.png`` previously passed as ``image/png``).

Caps are kept on the providers themselves as module constants.  They are
exposed via ``__init__`` so a concrete provider requirement can override
them; the rest of the pipeline does not need to know the numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# MiniMax-M3 documents a 10 MB image cap and a 64 MB request cap.
# SenseNova does not document public caps in our reference material,
# but a sensible conservative default keeps the audit guidance
# (reject before Base64 encode) intact.
_DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_PIXELS = 25_000_000  # 25 MP — well above a textbook scan.
_ALLOWED_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp"}
)
# Pillow's ``Image.open().format`` returns one of these for valid
# inputs; the gate rejects anything outside this whitelist.
_ALLOWED_PILLOW_FORMATS: frozenset[str] = frozenset(
    {"PNG", "JPEG", "GIF", "WEBP"}
)
# The suffix is treated as a hint that must agree with the format
# Pillow actually detects.  Multiple suffixes can map to the same
# Pillow format (``.jpg`` / ``.jpeg`` → ``JPEG``).
_SUFFIX_TO_PILLOW_FORMAT: dict[str, str] = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".gif": "GIF",
    ".webp": "WEBP",
}
_PILLOW_FORMAT_TO_MEDIA_TYPE: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


@dataclass(frozen=True)
class VlmGateResult:
    """Outcome of one asset pre-flight check."""

    ok: bool
    error: str = ""
    media_type: str = ""
    pixel_count: int = 0
    detected_format: str = ""
    """Pillow's reported format string (``"PNG"``/``"JPEG"``/...).  Empty
    when the gate never reached the decode step."""


def _media_type_for_pillow_format(fmt: str) -> str:
    return _PILLOW_FORMAT_TO_MEDIA_TYPE.get(fmt, "")


def check_vlm_asset(
    image_path: Path,
    *,
    max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
    max_pixels: int = _DEFAULT_MAX_PIXELS,
) -> VlmGateResult:
    """Validate a local asset before it is sent to a VLM provider.

    The check never trusts the file suffix for MIME detection.  Pillow
    decodes the file header to learn the real image format; that
    format is then cross-checked against the suffix hint and against
    the whitelist.  A mismatched format is rejected so a JPEG renamed
    to ``.png`` cannot be shipped to the VLM as ``image/png``.
    """
    if not isinstance(image_path, Path):
        image_path = Path(image_path)
    suffix = image_path.suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        return VlmGateResult(
            ok=False,
            error=f"unsupported image extension: {suffix or '<none>'}",
        )
    if not image_path.is_file():
        return VlmGateResult(ok=False, error=f"image file missing: {image_path}")
    try:
        size_bytes = image_path.stat().st_size
    except OSError as exc:
        return VlmGateResult(ok=False, error=f"cannot stat image: {exc}")
    if size_bytes > max_image_bytes:
        return VlmGateResult(
            ok=False,
            error=(
                f"image too large: {size_bytes} bytes "
                f"(max {max_image_bytes})"
            ),
        )
    try:
        from PIL import Image, UnidentifiedImageError  # Pillow is in pyproject.toml.

        # Codex P1 follow-up: the gate must fully verify the image
        # bytes, not just the header.  A truncated PNG keeps a valid
        # header so ``Image.open(...).size`` succeeds, but Pillow's
        # lazy loader only decodes the file when ``load()`` or
        # ``verify()`` is called.  We do both, on two fresh handles,
        # so a corrupt body is rejected before the asset is sent
        # to the VLM.  ``verify()`` is the canonical Pillow check
        # for internal structure (CRCs, IDAT/IEND, etc.); the
        # follow-up ``load()`` confirms the pixel data actually
        # decodes (which a truncated body cannot).
        with Image.open(image_path) as verify_probe:
            verify_probe.verify()
        with Image.open(image_path) as decode_probe:
            decode_probe.load()
            width, height = decode_probe.size
            detected_format = (decode_probe.format or "").upper()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        return VlmGateResult(ok=False, error=f"cannot decode image: {exc}")
    if detected_format not in _ALLOWED_PILLOW_FORMATS:
        return VlmGateResult(
            ok=False,
            detected_format=detected_format,
            error=(
                f"unsupported image format: {detected_format or '<unknown>'}"
            ),
        )
    expected_format = _SUFFIX_TO_PILLOW_FORMAT.get(suffix)
    if expected_format is not None and detected_format != expected_format:
        return VlmGateResult(
            ok=False,
            detected_format=detected_format,
            error=(
                f"image format does not match extension: "
                f"suffix {suffix!r} implies {expected_format!r} "
                f"but Pillow detected {detected_format!r}"
            ),
        )
    pixels = int(width) * int(height)
    if pixels > max_pixels:
        return VlmGateResult(
            ok=False,
            detected_format=detected_format,
            error=(
                f"image too large: {width}x{height} = {pixels} pixels "
                f"(max {max_pixels})"
            ),
        )
    return VlmGateResult(
        ok=True,
        media_type=_PILLOW_FORMAT_TO_MEDIA_TYPE[detected_format],
        pixel_count=pixels,
        detected_format=detected_format,
    )


__all__ = [
    "VlmGateResult",
    "check_vlm_asset",
    "_DEFAULT_MAX_IMAGE_BYTES",
    "_DEFAULT_MAX_PIXELS",
]
