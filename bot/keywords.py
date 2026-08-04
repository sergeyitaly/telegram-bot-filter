"""Keyword/regex sets used to flag strike-related content.

Split into two tiers:
- STRIKE_TERMS: words that on their own strongly imply a message describes
  the *result* of an attack (impact, damage, casualties). These are checked
  at all times, alarm or not.
- LOCATION_TERMS: address/landmark words that are only meaningful together
  with a strike term, or on their own during an active alarm window (when
  any location chatter is risky).
"""
import re

from bot import store

STRIKE_TERMS = [
    # Ukrainian
    r"приліт", r"прильот", r"влучанн", r"влучив", r"влучила", r"влучили",
    r"наслідки удару", r"наслідки атаки", r"наслідки обстрілу",
    r"уламк", r"збил[иао]", r"падінн\w* уламків", r"детонац",
    r"руйнуванн", r"пожежа після удару", r"вибух", r"вибухи", r"вибухнул",
    r"дим над", r"горить будинок", r"приліт[іу]в", r"поранен", r"загибл", r"жертв",
    # Russian
    r"прилет", r"попадани", r"последстви\w* удара", r"последстви\w* атаки",
    r"обломк", r"взрыв", r"разрушени", r"пожар после удара", r"сбил[иао]",
    r"пострадавш", r"погибш", r"ранен",
]

LOCATION_TERMS = [
    r"\bвул\.", r"вулиц[яії]", r"проспект", r"просп\.", r"бульвар", r"мікрорайон",
    r"перехрест", r"будинок №?\s*\d", r"будинку №?\s*\d", r"поверх\w*",
    r"\bул\.", r"улиц[аы]", r"перекрёст", r"перекресток", r"дом №?\s*\d",
]

# lat,long shared as plain text (native Telegram location pins are handled separately).
COORDINATE_RE = re.compile(r"-?\d{1,3}[.,]\d{3,6}\s*,\s*-?\d{1,3}[.,]\d{3,6}")

_STRIKE_RE = re.compile("|".join(STRIKE_TERMS), re.IGNORECASE)
_LOCATION_RE = re.compile("|".join(LOCATION_TERMS), re.IGNORECASE)

_CUSTOM_TERMS_KEY = "custom_keywords"

# Raw (unescaped) terms added at runtime via /addkeyword, tracked separately
# from the hardcoded baseline so only the additions get persisted/restored —
# not a frozen copy of the whole list, which would drift from code changes.
_custom_strike_terms: list[str] = []
_custom_location_terms: list[str] = []


def _add_term_in_memory(term: str, tier: str) -> None:
    global _STRIKE_RE, _LOCATION_RE
    escaped = re.escape(term)
    if tier == "location":
        LOCATION_TERMS.append(escaped)
        _LOCATION_RE = re.compile("|".join(LOCATION_TERMS), re.IGNORECASE)
        _custom_location_terms.append(term)
    else:
        STRIKE_TERMS.append(escaped)
        _STRIKE_RE = re.compile("|".join(STRIKE_TERMS), re.IGNORECASE)
        _custom_strike_terms.append(term)


async def hydrate() -> None:
    """Load runtime-added keywords from Redis (no-op if not configured).
    Call once at startup, before polling begins."""
    data = await store.get_json(_CUSTOM_TERMS_KEY, {"strike": [], "location": []})
    for term in data.get("strike", []):
        _add_term_in_memory(term, "strike")
    for term in data.get("location", []):
        _add_term_in_memory(term, "location")


async def add_term(term: str, tier: str = "strike") -> None:
    """Allow admins to extend the lists at runtime via /addkeyword."""
    term = term.strip()
    if not term:
        return
    _add_term_in_memory(term, tier)
    await store.set_json(
        _CUSTOM_TERMS_KEY,
        {"strike": _custom_strike_terms, "location": _custom_location_terms},
    )


def has_strike_term(text: str) -> bool:
    return bool(text) and bool(_STRIKE_RE.search(text))


def has_location_term(text: str) -> bool:
    return bool(text) and bool(_LOCATION_RE.search(text))


def has_coordinates(text: str) -> bool:
    return bool(text) and bool(COORDINATE_RE.search(text))
