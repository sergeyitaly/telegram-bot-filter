"""Message classification: decide whether text/media should be blocked."""
import logging
import unicodedata
from dataclasses import dataclass

from bot import keywords

log = logging.getLogger(__name__)

# Map visually identical Latin characters to their Cyrillic equivalents so
# homoglyph substitution (e.g. Latin 'a' instead of Cyrillic 'а') cannot
# bypass Cyrillic keyword patterns.
_LATIN_TO_CYRILLIC = str.maketrans(
    "aABCcEeHiIKMOoPpTXxy",
    "аАВСсЕеНіІКМОоРрТХху",
)

# Zero-width, soft-hyphen, and bidirectional control characters that are used
# to inject invisible content or reorder displayed text (RTL override, etc.)
_ZERO_WIDTH = {
    "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",  # LRE RLE PDF LRO RLO
    "\u2066", "\u2067", "\u2068", "\u2069",             # LRI RLI FSI PDI
}

# Emoji digit sequences (e.g. 5️⃣) → ASCII digit, so coordinate patterns fire.
_EMOJI_DIGITS: list[tuple[str, str]] = [
    ("\u0030\ufe0f\u20e3", "0"), ("\u0031\ufe0f\u20e3", "1"),
    ("\u0032\ufe0f\u20e3", "2"), ("\u0033\ufe0f\u20e3", "3"),
    ("\u0034\ufe0f\u20e3", "4"), ("\u0035\ufe0f\u20e3", "5"),
    ("\u0036\ufe0f\u20e3", "6"), ("\u0037\ufe0f\u20e3", "7"),
    ("\u0038\ufe0f\u20e3", "8"), ("\u0039\ufe0f\u20e3", "9"),
]


def _normalize(text: str) -> str:
    """NFKC-normalize, strip invisible/control chars, map Latin homoglyphs to
    Cyrillic, and decode emoji digits — so bypass attempts using lookalike
    letters, zero-width spacers, Unicode tag blocks, or encoded numbers are
    caught by the same patterns as regular text."""
    # NFKC handles fullwidth digits/letters, compatibility forms, etc.
    text = unicodedata.normalize("NFKC", text)
    # Strip bidirectional overrides and zero-width chars
    text = "".join(ch for ch in text if ch not in _ZERO_WIDTH)
    # Strip Unicode tag block U+E0000–U+E007F (invisible text encoding)
    text = "".join(c for c in text if not (0xE0000 <= ord(c) <= 0xE007F))
    # Normalize emoji digit sequences before applying the homoglyph map
    for seq, digit in _EMOJI_DIGITS:
        text = text.replace(seq, digit)
    # Map Latin visual lookalikes to Cyrillic counterparts
    text = text.translate(_LATIN_TO_CYRILLIC)
    return text


def ocr_image(path: str) -> str:
    """Extract text from an image via OCR. Returns empty string if
    pytesseract is not installed — graceful no-op in deployments without it."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        return pytesseract.image_to_string(Image.open(path), lang="ukr+rus+eng")
    except Exception as exc:
        log.debug("OCR failed for %s: %s", path, exc)
        return ""


@dataclass
class Verdict:
    flagged: bool
    reason: str = ""


def classify_text(text: str, strict: bool, alarm_active: bool = False) -> Verdict:
    """`strict` = alarm currently active OR still within the post-alarm grace
    window. `alarm_active` is a subset of strict used for the most aggressive
    checks (URL blocking) that we don't want to apply for the full 2-hour grace.
    Coordinates are a direct location leak regardless of timing."""
    if not text:
        return Verdict(False)

    text = _normalize(text)

    # Map service URLs — the link preview exposes location even after deletion.
    # Blocked unconditionally like coordinates (a maps.google.com link IS a
    # coordinate leak regardless of alarm state).
    if keywords.has_map_url(text):
        return Verdict(True, "map URL shared")

    if keywords.has_coordinates(text):
        return Verdict(True, "coordinates shared")

    # Generic URLs only during active alarm: news articles reveal strike details
    # via Telegram's server-side link preview before the message can be deleted.
    # During the grace window we allow URLs so the chat can share official updates.
    if alarm_active and keywords.has_url(text):
        return Verdict(True, "URL shared during active alarm")

    if strict and keywords.has_strike_term(text):
        return Verdict(True, "strike-result keyword")

    if strict and keywords.has_location_term(text):
        return Verdict(True, "location mentioned during active alarm or grace period")

    return Verdict(False)


def classify_media(caption: str, alarm_active: bool, strict: bool) -> Verdict:
    """Photos/videos are riskier than plain text: during an active alarm we
    blur everything, since a bystander's photo can confirm a hit before any
    official statement. Outside an active alarm, only caption content
    matters, same strict-window rule as text."""
    if alarm_active:
        return Verdict(True, "media posted during active alarm")

    if caption:
        caption = _normalize(caption)

    if caption and keywords.has_coordinates(caption):
        return Verdict(True, "coordinates in caption")

    if strict and caption and keywords.has_strike_term(caption):
        return Verdict(True, "strike-result caption")

    return Verdict(False)
