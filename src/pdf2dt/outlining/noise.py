"""Heuristics for recognising items that should never be exported.

These are textual artefacts of the OCR + markdown-split pipeline:

* Publisher watermarks (微信公众号 / 教辅资料站 banners) that the OCR
  picks up from page headers and footers.
* Single-character ASCII noise such as ``U`` or ``#`` — page ornaments
  the splitter mistook for headings.
* Page numbers (1-3 digit pure-digit titles).
* Single-character Chinese titles whose text body is also empty.

The rules are deliberately conservative: any title that *could* belong
to a real problem (e.g. ``"习"`` whose body contains ``"5. 观察下图…"``)
must NOT be flagged. We only flag items where the title itself is the
entire signal AND that signal is a known publisher phrase, a pure
punctuation/letter, or a page number.

Two thresholds let the caller opt in:

* ``min_body_chars`` (default 30) — items whose body is shorter than
  this AND whose title is one character / a few ASCII characters are
  treated as noise. This avoids swallowing ``例题 5`` whose body is
  long.
* The function is exposed so the renderer can re-validate any item
  at render time as a defence in depth.
"""
from __future__ import annotations

from dataclasses import dataclass

# Reuse the same publisher list the figure-role mock already trusts.
# Tests assert the two constants stay in sync so that the watermark
# filter and the figure role filter agree on what counts as noise.
NOISE_TITLE_PATTERNS: tuple[str, ...] = (
    "微信公众号 教辅资料站",
    "微信公众号",
    "教辅资料站",
    "学而思",
    "新东方",
    "高思教育",
)

# Single-character punctuation / symbols that show up as page
# ornaments or stray OCR tokens. Anything not in this set is kept.
_NOISE_SINGLE_ASCII = set("#@$%&*?/\\|<>~^`")
# Pure ASCII letters the OCR pipeline has been seen to mis-split as
# headings ("U", "L", "M"). Limited to short (<= 2) titles where the
# body is also short so that "American M." / "M.2" style proper nouns
# are not caught.
_NOISE_SHORT_ASCII_TITLE_MAX_LEN = 2

# Page numbers usually come through as 1-3 digit pure-digit strings.
_MAX_PAGE_NUMBER_DIGITS = 3


@dataclass(frozen=True)
class NoiseVerdict:
    """Why we flagged (or didn't) an item.

    ``reason`` is human-readable; ``is_noise`` is the boolean the
    matcher and renderer consume.
    """

    is_noise: bool
    reason: str


def classify_noise(
    item,
    *,
    min_body_chars: int = 30,
) -> NoiseVerdict:
    """Return whether the item is noise and the reason.

    ``item`` may be either a dict (``item.get("title")`` /
    ``item.get("text")``) or an :class:`outlining.items.Item`
    dataclass. Both shapes are tolerated because ``extract_items``
    produces ``Item`` objects while downstream stages often carry
    plain dicts.
    """
    if isinstance(item, dict):
        title = (item.get("title") or "").strip()
        body = (item.get("text") or "").strip()
    else:
        title = (getattr(item, "title", "") or "").strip()
        body = (getattr(item, "text", "") or "").strip()

    if not title:
        # An item with no title is suspect. But many real items also
        # lack a title (e.g. a stray paragraph mid-chapter). We flag
        # only when the body is also empty / a single punctuation
        # token — that combination never represents math content.
        if not body or len(body) <= 3 and not any(ch.isalnum() for ch in body):
            return NoiseVerdict(True, "empty title and empty/punctuation body")
        return NoiseVerdict(False, "no title but body has content")

    # 1. Watermark phrase anywhere in the title.
    for pattern in NOISE_TITLE_PATTERNS:
        if pattern in title:
            return NoiseVerdict(True, f"watermark phrase {pattern!r} in title")

    # 2. Single-character ASCII noise. We accept ASCII letters as
    # "noise" only when the title length is 1 and the body is short,
    # so that acronyms and abbreviations ("习" / "U.S.A." style) are
    # spared.
    if len(title) == 1:
        ch = title[0]
        if ch in _NOISE_SINGLE_ASCII:
            return NoiseVerdict(True, f"single ASCII punctuation {ch!r}")
        if ch.isascii() and ch.isalpha() and len(body) < min_body_chars:
            return NoiseVerdict(
                True,
                f"single ASCII letter {ch!r} with empty body",
            )

    # 3. Pure-digit page-number title. 1-3 digits to cover Chinese
    # textbook page numbering ("10", "118", "8-2" is handled below).
    if title.isdigit() and 1 <= len(title) <= _MAX_PAGE_NUMBER_DIGITS:
        return NoiseVerdict(True, f"page number {title!r}")

    # 4. Short pure-ASCII title with empty body (catches "习" + "U"
    # style OCR junk that lives entirely in the title). Pure-digit
    # titles are caught above as page numbers so we skip them here.
    if (
        title.isascii()
        and len(title) <= _NOISE_SHORT_ASCII_TITLE_MAX_LEN
        and not title.isdigit()
        and len(body) < min_body_chars
    ):
        return NoiseVerdict(
            True,
            f"short ASCII title {title!r} with empty body",
        )

    # 5. Short range like "8-2" used as a page range / chapter ref.
    if (
        len(title) <= 5
        and title.replace("-", "").isdigit()
        and title.count("-") == 1
        and len(body) < min_body_chars
    ):
        return NoiseVerdict(True, f"page range {title!r}")

    return NoiseVerdict(False, "title does not match any noise rule")


def is_noise_item(item: dict, *, min_body_chars: int = 30) -> bool:
    """Boolean shortcut over :func:`classify_noise`."""
    return classify_noise(item, min_body_chars=min_body_chars).is_noise


def partition_items(
    items: list[dict],
    *,
    min_body_chars: int = 30,
) -> tuple[list[dict], list[dict]]:
    """Split ``items`` into (kept, dropped) by noise classification.

    The dropped list preserves order so call sites can write an
    audit report.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for it in items:
        if is_noise_item(it, min_body_chars=min_body_chars):
            dropped.append(it)
        else:
            kept.append(it)
    return kept, dropped
