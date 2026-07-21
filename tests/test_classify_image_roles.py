"""Tests for the mock figure-role provider in
``scripts/classify_image_roles.py``.

The provider is a deterministic heuristic: it flags any figure whose
context contains a known publisher/watermark phrase as ``decor`` and
falls back to ``content`` otherwise. These tests pin that contract.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "classify_image_roles.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("classify_image_roles", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


def test_mock_provider_module_importable(script_module) -> None:
    assert hasattr(script_module, "MockFigureRoleProvider")
    assert hasattr(script_module, "DECOR_CONTEXT_PATTERNS")
    assert "微信公众号 教辅资料站" in script_module.DECOR_CONTEXT_PATTERNS


def test_mock_decor_context(script_module) -> None:
    p = script_module.MockFigureRoleProvider()
    for ctx in (
        "微信公众号 教辅资料站",
        "本资料由微信公众号 教辅资料站发布",
        "高思教育出品",
        "学而思网校",
    ):
        resp = p.analyze_image("dummy.jpg", ctx)
        payload = json.loads(resp.raw_response)
        assert payload["role"] == "decor", ctx
        assert payload["confidence"] >= 0.9, ctx


def test_mock_chapter_header_image_is_kept_as_content(script_module) -> None:
    """A figure bound to a chapter item whose body is long enough
    to include real problem text is *not* auto-flagged decor just
    because it sits under a chapter heading. The book view builder
    frequently groups chapter heading AND several example
    problems under a single chapter item, so flagging every
    chapter-header image would silently drop real content.
    """
    p = script_module.MockFigureRoleProvider()
    ctx = (
        "[item_type:chapter]\n"
        "[chapter:加减法巧算]\n"
        "[title:加减法巧算]\n"
        "加减法巧算\n"
        "![image](assets/bb5b02c8d103.jpg)\n"
        "在进行加减法计算时, 先计算括号中的部分再从左往右依次计算."
    )
    resp = p.analyze_image("dummy.jpg", ctx)
    payload = json.loads(resp.raw_response)
    # Falls through to default content because the chapter item's
    # body is long enough that the figure may be a real problem
    # diagram. The mock has no VLM, so the conservative choice
    # is to keep it; the real provider can override.
    assert payload["role"] == "content"


def test_mock_section_banner_under_heading_is_decor(script_module) -> None:
    """When the mock can confirm via the actual_section marker
    (set by ``build_image_to_preceding_heading``) that the image
    sits under a known banner heading, the figure is almost always
    a publisher template icon.
    """
    p = script_module.MockFigureRoleProvider()
    ctx = (
        "[item_type:chapter]\n"
        "[chapter:加减法巧算]\n"
        "[actual_section:本讲知识点汇总]\n"
        "[title:加减法巧算]\n"
        "本讲知识点汇总\n"
        "![image](assets/953dd176cc30.jpg)\n"
    )
    resp = p.analyze_image("dummy.jpg", ctx)
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "decor"
    assert "section banner" in payload["reason"]


def test_mock_section_banner_with_long_body_keeps_content(script_module) -> None:
    """A figure bound to a 本讲知识点汇总 heading with a long body
    (real problem text) is *not* a pure banner — the figure may be
    a problem diagram and should not be auto-dropped.
    """
    p = script_module.MockFigureRoleProvider()
    ctx = (
        "[item_type:section]\n"
        "[actual_section:本讲知识点汇总]\n"
        "[title:本讲知识点汇总]\n"
        "本讲知识点汇总\n"
        "![image](assets/953dd176cc30.jpg)\n"
        + ("这一讲我们学习了加法结合律. " * 5)
    )
    resp = p.analyze_image("dummy.jpg", ctx)
    payload = json.loads(resp.raw_response)
    # Falls through to default content because the body is long
    # enough that we cannot be sure the figure is a banner.
    assert payload["role"] == "content"


def test_mock_tiny_image_is_decor(script_module, tmp_path) -> None:
    """A 90x90 PNG is a decorative icon, not a math problem."""
    from PIL import Image

    img_path = tmp_path / "icon.png"
    Image.new("RGB", (90, 90), color="white").save(img_path)
    p = script_module.MockFigureRoleProvider()
    ctx = (
        "[item_type:section]\n"
        "[section:例题 1]\n"
        "[title:例题 1]\n"
        "例题 1\n![image](assets/icon.png)\n"
    )
    resp = p.analyze_image(str(img_path), ctx)
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "decor"
    assert "tiny" in payload["reason"]


def test_mock_extreme_aspect_banner_is_decor(script_module, tmp_path) -> None:
    """A 1200x100 banner PNG is a publisher template bar."""
    from PIL import Image

    img_path = tmp_path / "banner.png"
    Image.new("RGB", (1200, 100), color="white").save(img_path)
    p = script_module.MockFigureRoleProvider()
    ctx = (
        "[item_type:section]\n"
        "[section:例题 1]\n"
        "[title:例题 1]\n"
        "例题 1\n![image](assets/banner.png)\n"
    )
    resp = p.analyze_image(str(img_path), ctx)
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "decor"
    assert "aspect" in payload["reason"]


def test_mock_geometry_content(script_module) -> None:
    p = script_module.MockFigureRoleProvider()
    resp = p.analyze_image(
        "dummy.jpg",
        "在 $\\triangle ABC$ 中, $AB \\parallel CD$, 求证 $AB = CD$.",
    )
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "content"


def test_mock_empty_context_is_content(script_module) -> None:
    p = script_module.MockFigureRoleProvider()
    resp = p.analyze_image("dummy.jpg", "")
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "content"


def test_mock_template_decor_match(script_module, tmp_path) -> None:
    """An asset in the pre-computed template-decor set must be
    flagged decor regardless of its surrounding context, because
    the structural signal (cluster of near-identical images) is
    more reliable than the position signal.
    """
    p = script_module.MockFigureRoleProvider(template_decor_ids={"aabbccdd"})
    resp = p.analyze_image(
        str(tmp_path / "aabbccdd.png"),
        "Example body — nothing matches the banner heuristic here.",
    )
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "decor"
    assert "template-decor" in payload["reason"]


def test_mock_template_decor_match_uses_image_stem(script_module, tmp_path) -> None:
    """The mock must look up the template-decor set by image file
    stem, not by full path or any other field.
    """
    p = script_module.MockFigureRoleProvider(template_decor_ids={"abc123"})
    # Path may be a Windows-style absolute path. Stem is the
    # final component without extension.
    resp = p.analyze_image(
        str(tmp_path / "subdir" / "abc123.png"),
        "Anything goes here.",
    )
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "decor"
    assert "template-decor" in payload["reason"]


def test_mock_template_decor_setter_replaces(script_module) -> None:
    p = script_module.MockFigureRoleProvider(template_decor_ids={"a"})
    p.set_template_decor_ids({"b"})
    # ``a`` is no longer in the set, ``b`` is.
    resp_a = p.analyze_image("a.png", "")
    resp_b = p.analyze_image("b.png", "")
    assert json.loads(resp_a.raw_response)["role"] == "content"
    assert json.loads(resp_b.raw_response)["role"] == "decor"


def test_mock_watermark_wins_over_template_decor(script_module) -> None:
    """Watermark is rule 1, template-decor is rule 3. When both
    could fire, watermark wins. The test pins that ordering so a
    future refactor does not silently downgrade watermark to a
    fallback.
    """
    p = script_module.MockFigureRoleProvider(template_decor_ids={"abc123"})
    resp = p.analyze_image(
        "abc123.png",
        "微信公众号 教辅资料站",
    )
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "decor"
    assert "watermark" in payload["reason"]


def test_mock_default_content(script_module) -> None:
    p = script_module.MockFigureRoleProvider()
    resp = p.analyze_image("unknown.png", "Some plain math content")
    payload = json.loads(resp.raw_response)
    assert payload["role"] == "content"
