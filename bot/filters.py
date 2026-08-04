"""Message classification: decide whether text/media should be blocked."""
from dataclasses import dataclass

from bot import keywords


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

    if caption and keywords.has_coordinates(caption):
        return Verdict(True, "coordinates in caption")

    if strict and caption and keywords.has_strike_term(caption):
        return Verdict(True, "strike-result caption")

    return Verdict(False)
