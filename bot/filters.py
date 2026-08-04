"""Message classification: decide whether text/media should be blocked."""
from dataclasses import dataclass

from bot import keywords


@dataclass
class Verdict:
    flagged: bool
    reason: str = ""


def classify_text(text: str, alarm_active: bool) -> Verdict:
    if not text:
        return Verdict(False)

    if keywords.has_strike_term(text):
        return Verdict(True, "strike-result keyword")

    if keywords.has_coordinates(text):
        return Verdict(True, "coordinates shared")

    # An address alone is normal chat; only risky once tied to a strike term,
    # or during an active alarm when any location chatter is unsafe.
    if keywords.has_location_term(text):
        if alarm_active:
            return Verdict(True, "location mentioned during active alarm")

    return Verdict(False)


def classify_media(caption: str, alarm_active: bool) -> Verdict:
    """Photos/videos are riskier than plain text: during an active alarm we
    blur everything, since a bystander's photo can confirm a hit before any
    official statement. Outside an alarm, only caption content matters."""
    if alarm_active:
        return Verdict(True, "media posted during active alarm")

    if caption and (keywords.has_strike_term(caption) or keywords.has_coordinates(caption)):
        return Verdict(True, "strike-result caption")

    return Verdict(False)
