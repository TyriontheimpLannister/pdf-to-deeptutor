"""Unit tests for the template-decor perceptual-hash clusterer."""
from __future__ import annotations

from PIL import Image, ImageDraw

from pdf2dt.review.template_decor import find_template_decor_assets


def _write_banner(path, seed=0) -> None:
    """Write a 248x92 banner with a unique but reproducible pattern.

    seed controls the pattern so we can make near-identical copies
    (same seed) or clearly different images (different seeds).
    """
    w, h = 248, 92
    im = Image.new("RGB", (w, h), color=(240, 240, 245))
    draw = ImageDraw.Draw(im)
    # Horizontal gradient bands
    for y in range(h):
        t = y / h
        r = int(100 + 80 * t + seed * 3) % 256
        g = int(150 + 60 * t)
        b = int(200 - 40 * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    # Decorative circles at fixed positions
    draw.ellipse([10, 10, 30, 30], fill=(255, 200, 100), outline=(200, 150, 50))
    draw.ellipse([w - 30, h - 30, w - 10, h - 10], fill=(100, 200, 255), outline=(50, 150, 200))
    im.save(path, "PNG")


def _write_outlier(path) -> None:
    """Write a clearly different image (big diagram-like image)."""
    w, h = 800, 600
    im = Image.new("RGB", (w, h), color="white")
    draw = ImageDraw.Draw(im)
    # Draw a triangle (diagram-like)
    draw.polygon([(400, 100), (100, 500), (700, 500)], outline="black", width=3)
    # Add some grid lines
    for x in range(0, w, 50):
        draw.line([(x, 0), (x, h)], fill=(230, 230, 230))
    for y in range(0, h, 50):
        draw.line([(0, y), (w, y)], fill=(230, 230, 230))
    im.save(path, "PNG")


def test_finds_three_near_identical_banners(tmp_path) -> None:
    """Three near-identical 248x92 banner images should cluster."""
    a1 = tmp_path / "a1.png"
    a2 = tmp_path / "a2.png"
    a3 = tmp_path / "a3.png"
    a4 = tmp_path / "outlier.png"
    _write_banner(a1, seed=0)
    _write_banner(a2, seed=0)
    _write_banner(a3, seed=0)
    _write_outlier(a4)
    ids = find_template_decor_assets(
        [("a1", a1), ("a2", a2), ("a3", a3), ("a4", a4)]
    )
    assert {"a1", "a2", "a3"} <= ids
    assert "a4" not in ids


def test_two_near_identical_does_not_count_as_template(tmp_path) -> None:
    """Min cluster size is 3 — two near-identical images are not
    flagged as a template (could be a coincidence).
    """
    a1 = tmp_path / "a1.png"
    a2 = tmp_path / "a2.png"
    _write_banner(a1, seed=0)
    _write_banner(a2, seed=0)
    ids = find_template_decor_assets([("a1", a1), ("a2", a2)])
    assert ids == set()


def test_handles_missing_files(tmp_path) -> None:
    a1 = tmp_path / "a1.png"
    _write_banner(a1)
    ids = find_template_decor_assets(
        [("a1", a1), ("missing", tmp_path / "missing.png")]
    )
    assert ids == set()


def test_handles_corrupt_files(tmp_path) -> None:
    a1 = tmp_path / "a1.png"
    _write_banner(a1)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    ids = find_template_decor_assets([("a1", a1), ("bad", bad)])
    assert ids == set()


def test_empty_input(tmp_path) -> None:
    assert find_template_decor_assets([]) == set()
