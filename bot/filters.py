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

# Zero-width and soft-hyphen characters commonly injected to break regex matches.
_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"}


def _normalize(text: str) -> str:
    """NFKC-normalize, strip zero-width chars, and map Latin homoglyphs to
    Cyrillic so bypass attempts using lookalike letters or invisible spacers
    are caught by the same patterns as regular text."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch not in _ZERO_WIDTH)
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


def classify_text(text: str, strict: bool) -> Verdict:
    """`strict` = alarm currently active OR still within the post-alarm grace
    window. Coordinates are a direct location leak regardless of timing, so
    they're blocked unconditionally; strike-result wording and address
    chatter wind down once the strict window closes, so a chat isn't
    permanently barred from ever mentioning a past strike."""
    if not text:
        return Verdict(False)

    text = _normalize(text)

    if keywords.has_coordinates(text):
        return Verdict(True, "coordinates shared")

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
