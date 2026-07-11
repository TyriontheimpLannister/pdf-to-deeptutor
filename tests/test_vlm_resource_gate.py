"""Tests for the VLM asset resource gate (P1 #2).

The gate is the deterministic pre-flight that runs before any base64
encoding or HTTP call.  It enforces a MIME whitelist, a byte cap and a
decoded-pixel cap so a single oversized asset cannot blow past the
provider request limit.  A failing gate returns
``VlmResponse(error="asset_rejected: ...")`` so the rules-first pipeline
finishes regardless of the result.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from PIL import Image

from pdf2dt.geometry import VlmGateResult, check_vlm_asset
from pdf2dt.geometry.vlm import (
    HybridGeometryAnalyzer,
    MiniMaxM3Provider,
    SenseNovaProvider,
)


def _write_png(path: Path, *, width: int, height: int) -> None:
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    image.save(path, format="PNG")


def test_check_vlm_asset_rejects_missing_file(tmp_path: Path) -> None:
    result = check_vlm_asset(tmp_path / "missing.png")
    assert isinstance(result, VlmGateResult)
    assert result.ok is False
    assert "missing" in result.error


def test_check_vlm_asset_rejects_unsupported_suffix(tmp_path: Path) -> None:
    bad = tmp_path / "figure.bmp"
    bad.write_bytes(b"BM-not-checked-for-content")
    result = check_vlm_asset(bad)
    assert result.ok is False
    assert "unsupported image extension" in result.error


def test_check_vlm_asset_rejects_oversize_file(tmp_path: Path) -> None:
    huge = tmp_path / "figure.png"
    huge.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    result = check_vlm_asset(huge, max_image_bytes=10 * 1024 * 1024)
    assert result.ok is False
    assert "image too large" in result.error
    assert "bytes" in result.error


def test_check_vlm_asset_rejects_oversize_pixels(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    _write_png(image, width=6000, height=6000)  # 36M pixels
    result = check_vlm_asset(image, max_pixels=25_000_000)
    assert result.ok is False
    assert "pixels" in result.error


def test_check_vlm_asset_accepts_within_caps(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    _write_png(image, width=1280, height=720)  # 921_600 pixels
    result = check_vlm_asset(image)
    assert result.ok is True
    assert result.media_type == "image/png"
    assert result.pixel_count == 1280 * 720


def test_check_vlm_asset_rejects_undecodable_payload(tmp_path: Path) -> None:
    bad = tmp_path / "figure.png"
    bad.write_bytes(b"not a real PNG body")
    result = check_vlm_asset(bad)
    assert result.ok is False
    assert "cannot decode image" in result.error


def test_minimax_provider_short_circuits_on_oversize_asset(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    _write_png(image, width=6000, height=6000)  # 36M pixels

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called when the gate fails")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxM3Provider(
        api_key="test-key",
        client=client,
        max_image_bytes=10 * 1024 * 1024,
    )
    try:
        response = provider.analyze_image(image, "triangle ABC")
    finally:
        client.close()

    assert response.error.startswith("asset_rejected:")
    assert "pixels" in response.error
    assert response.relations == []


def test_minimax_provider_short_circuits_on_bad_suffix(tmp_path: Path) -> None:
    bad = tmp_path / "figure.bmp"
    bad.write_bytes(b"not checked")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called when the gate fails")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxM3Provider(api_key="test-key", client=client)
    try:
        response = provider.analyze_image(bad, "triangle ABC")
    finally:
        client.close()

    assert response.error.startswith("asset_rejected:")
    assert "unsupported" in response.error


def test_sensenova_provider_short_circuits_on_oversize_asset(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    _write_png(image, width=6000, height=6000)  # 36M pixels

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called when the gate fails")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SenseNovaProvider(api_key="test-key", client=client)
    try:
        response = provider.analyze_image(image, "triangle ABC")
    finally:
        client.close()

    assert response.error.startswith("asset_rejected:")


def test_hybrid_analyzer_marks_asset_gate_rejection(tmp_path: Path) -> None:
    """A rejected asset must surface as ``rejected`` status with the
    rules result preserved; the analyzer must never raise."""
    image = tmp_path / "figure.png"
    _write_png(image, width=6000, height=6000)  # 36M pixels

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called when the gate fails")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    from pdf2dt.bookview.builder import BookItem

    item = BookItem(
        item_id="i-1",
        item_type="definition",
        title="",
        text="",
        chapter_path=(),
    )
    provider = MiniMaxM3Provider(
        api_key="test-key",
        client=client,
        max_image_bytes=10 * 1024 * 1024,
    )
    try:
        analyzer = HybridGeometryAnalyzer(provider=provider)
        figure = analyzer.analyze(
            item=item, asset_id="asset-1", asset_path=image
        )
    finally:
        client.close()
    assert figure is not None
    record = analyzer.call_records[0]
    assert record.status == "rejected"
    assert record.error.startswith("asset_rejected:")
    assert "pixels" in record.error
    observations = " ".join(figure.visual_observations)
    assert "asset_rejected" in observations


def test_hybrid_analyzer_uses_pillow_to_detect_pixel_count(tmp_path: Path) -> None:
    """Even with a tiny PNG and a small byte cap, the gate sees the
    decoded pixel count and rejects a 5k-by-5k image under any default
    pixel cap."""
    image = tmp_path / "figure.png"
    _write_png(image, width=5001, height=5001)

    # Force a tiny pixel cap so the test exercises the pixel branch.
    result = check_vlm_asset(image, max_pixels=1_000_000)
    assert result.ok is False
    assert "pixels" in result.error


# ---------------------------------------------------------------------- #
# P1 #1 regression: real MIME detection, not extension-only.
# ---------------------------------------------------------------------- #


def test_check_vlm_asset_rejects_jpeg_renamed_to_png(tmp_path: Path) -> None:
    """Codex P1 #1: a JPEG saved as ``figure.png`` must be rejected
    instead of being passed through as ``image/png``."""
    payload = tmp_path / "figure.png"
    Image.new("RGB", (64, 64), color=(200, 100, 50)).save(
        payload, format="JPEG"
    )

    result = check_vlm_asset(payload)

    assert result.ok is False
    assert result.detected_format == "JPEG"
    assert "does not match extension" in result.error
    assert "image/png" not in result.error  # MIME must not be reported.


def test_check_vlm_asset_rejects_png_renamed_to_jpg(tmp_path: Path) -> None:
    """Mirror of the renamed-as-PNG case: PNG bytes inside a ``.jpg``
    file must be rejected."""
    payload = tmp_path / "figure.jpg"
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(
        payload, format="PNG"
    )

    result = check_vlm_asset(payload)

    assert result.ok is False
    assert result.detected_format == "PNG"
    assert "does not match extension" in result.error


def test_check_vlm_asset_accepts_jpeg_with_jpg_suffix(tmp_path: Path) -> None:
    """A real JPEG with the matching ``.jpg`` suffix must still pass
    and report the truthful ``image/jpeg`` MIME."""
    payload = tmp_path / "figure.jpg"
    Image.new("RGB", (32, 32), color=(255, 0, 0)).save(
        payload, format="JPEG"
    )

    result = check_vlm_asset(payload)

    assert result.ok is True
    assert result.media_type == "image/jpeg"
    assert result.detected_format == "JPEG"


def test_check_vlm_asset_accepts_jpeg_with_jpeg_suffix(tmp_path: Path) -> None:
    """A real JPEG with the alternate ``.jpeg`` suffix must also pass
    (both suffixes share the same Pillow format mapping)."""
    payload = tmp_path / "figure.jpeg"
    Image.new("RGB", (32, 32), color=(0, 255, 0)).save(
        payload, format="JPEG"
    )

    result = check_vlm_asset(payload)

    assert result.ok is True
    assert result.media_type == "image/jpeg"
    assert result.detected_format == "JPEG"


def test_check_vlm_asset_accepts_png_with_png_suffix(tmp_path: Path) -> None:
    """The happy path: a real PNG with the matching suffix returns
    ``image/png`` and exposes the detected format for downstream
    auditing."""
    payload = tmp_path / "figure.png"
    _write_png(payload, width=32, height=32)

    result = check_vlm_asset(payload)

    assert result.ok is True
    assert result.media_type == "image/png"
    assert result.detected_format == "PNG"


def test_check_vlm_asset_rejects_truncated_payload(tmp_path: Path) -> None:
    """A truncated PNG keeps the correct header but breaks deeper
    parsing.  Pillow's ``Image.open`` returns the declared size from
    the header; the gate must additionally catch the truncated body
    via ``Image.verify()`` / ``Image.load()`` so the asset is not
    silently accepted as a valid image.  Codex P1 follow-up: the
    earlier test accepted either branch (decoded or rejected), which
    did not enforce the contract.  The gate now must reject."""
    from PIL import Image

    payload = tmp_path / "figure.png"
    img = Image.new("RGB", (32, 32), color=(0, 0, 0))
    # Save the full image, then chop off the trailing IDAT/IEND so the
    # body is invalid even though the file header still names PNG.
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    head = buf.getvalue()[:64]
    payload.write_bytes(head)

    result = check_vlm_asset(payload)

    # Codex P1: the gate must reject truncated bytes.  A truncated
    # header keeps a recognisable "PNG" signature so the earlier
    # header-only check passed; the new verify() + load() pass
    # must catch it and the asset must never reach the VLM.
    assert result.ok is False, (
        "truncated PNG slipped through the gate; verify()/load() "
        "must be enforced"
    )
    assert "decode" in result.error or "verify" in result.error.lower()


def test_check_vlm_asset_rejects_truncated_jpeg(tmp_path: Path) -> None:
    """Same contract for JPEG: a truncated body must be rejected
    before the asset is sent to a provider."""
    import io

    payload = tmp_path / "figure.jpg"
    img = Image.new("RGB", (32, 32), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    head = buf.getvalue()[:64]
    payload.write_bytes(head)

    result = check_vlm_asset(payload)
    assert result.ok is False
    assert "decode" in result.error or "verify" in result.error.lower()


def test_minimax_provider_reports_actual_mime_not_suffix(tmp_path: Path) -> None:
    """End-to-end P1 #1: when the gate would have lied about the MIME
    (pre-fix), the post-fix gate rejects the asset before any HTTP
    traffic."""
    payload = tmp_path / "figure.png"
    Image.new("RGB", (32, 32), color=(1, 2, 3)).save(
        payload, format="JPEG"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called when the gate fails")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxM3Provider(api_key="test-key", client=client)
    try:
        response = provider.analyze_image(payload, "triangle ABC")
    finally:
        client.close()

    assert response.error.startswith("asset_rejected:")
    assert "does not match extension" in response.error
    assert response.relations == []


def test_minimax_provider_rejects_truncated_payload(tmp_path: Path) -> None:
    """Codex P1 follow-up: a truncated PNG must be rejected by the
    gate before any HTTP request is made, even when the asset's
    header parses cleanly.  MockTransport's handler asserts the
    provider never reaches the network layer."""
    import io

    payload = tmp_path / "figure.png"
    img = Image.new("RGB", (32, 32), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    payload.write_bytes(buf.getvalue()[:64])

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "provider must not be called when the gate rejects a "
            "truncated payload"
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MiniMaxM3Provider(api_key="test-key", client=client)
    try:
        response = provider.analyze_image(payload, "triangle ABC")
    finally:
        client.close()

    assert response.error.startswith("asset_rejected:")
    assert "decode" in response.error or "verify" in response.error.lower()
    assert response.relations == []
