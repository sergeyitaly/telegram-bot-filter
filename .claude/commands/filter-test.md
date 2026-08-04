Test the current filter logic against a battery of inputs — both things that SHOULD be caught and things that SHOULD pass through.

Run the following Python test script inline (no test framework needed):

```python
import sys
sys.path.insert(0, '.')
from bot.filters import classify_text, classify_media, _normalize
from bot.keywords import (has_coordinates, has_map_url, has_url,
                           has_strike_term, has_location_term)

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
errors = 0

def check(label, got, want):
    global errors
    icon = PASS if got == want else FAIL
    if got != want:
        errors += 1
        print(f"{icon} {label}: expected {want}, got {got}")
    else:
        print(f"{icon} {label}")

# --- Normalization ---
check("NFKC fullwidth", _normalize("ａ"), "а")   # fullwidth → Cyrillic а
check("Latin homoglyph a→а", "а" in _normalize("бaвовна"), True)
check("Zero-width strip", "\u200b" not in _normalize("вибух\u200bи"), True)
check("Tag block strip", "\U000e0062" not in _normalize("б\U000e0062авовна"), True)
check("RTL override strip", "\u202e" not in _normalize("при\u202eліт"), True)
check("Emoji digit 5️⃣→5", "5" in _normalize("5️⃣0.45"), True)

# --- Coordinates: must be caught unconditionally ---
check("Decimal coords", has_coordinates("50.45, 30.52"), True)
check("Decimal coords no space", has_coordinates("50.45,30.52"), True)
check("2-decimal coords", has_coordinates("50.45, 30.52"), True)
check("Negative lon", has_coordinates("-50.45, 30.52"), True)
check("DMS coords", has_coordinates("50°27'N 30°31'E"), True)
check("Plus code", has_coordinates("8G7G+XH Kyiv"), True)
check("Innocent number", has_coordinates("Зателефонуй 050.123"), False)

# --- Map URLs ---
check("Google Maps", has_map_url("https://maps.google.com/..."), True)
check("goo.gl/maps", has_map_url("goo.gl/maps/abc123"), True)
check("Apple Maps", has_map_url("maps.apple.com/?q=..."), True)
check("Waze", has_map_url("waze.com/ul?ll=..."), True)
check("Innocent URL", has_map_url("https://t.me/channel"), False)

# --- Strike keywords (strict mode) ---
check("приліт → flagged strict", classify_text("приліт на вулиці", strict=True).flagged, True)
check("бавовна → flagged strict", classify_text("знову бавовна", strict=True).flagged, True)
check("шахед → flagged strict", classify_text("шахед впав", strict=True).flagged, True)
check("жахнуло → flagged strict", classify_text("як жахнуло!", strict=True).flagged, True)
check("keyword NOT strict", classify_text("приліт знову", strict=False).flagged, False)

# --- Homoglyph bypass (Latin letters in Cyrillic words) ---
check("Latin a in бавовна", classify_text("бaвовна", strict=True).flagged, True)
check("Latin e in прилeт", classify_text("прилeт", strict=True).flagged, True)

# --- Map URL in classify_text (unconditional) ---
check("map URL unconditional", classify_text("https://maps.google.com/123", strict=False).flagged, True)
check("map URL strict=True", classify_text("https://maps.google.com/123", strict=True).flagged, True)

# --- Generic URL only during alarm ---
check("https:// not flagged non-alarm", classify_text("https://t.me/channel", strict=True, alarm_active=False).flagged, False)
check("https:// flagged during alarm", classify_text("https://t.me/channel", strict=True, alarm_active=True).flagged, True)

# --- Innocent text passes through ---
check("innocent text passes", classify_text("Добрий день всім!", strict=True).flagged, False)
check("innocent text non-strict", classify_text("Де найближча аптека?", strict=False).flagged, False)

print(f"\n{'All tests passed!' if errors == 0 else f'{errors} test(s) FAILED'}")
sys.exit(errors)
```

Run it with: `python -c "exec(open('.claude/commands/filter-test.md').read().split('```python')[1].split('```')[0])"`

Or just paste the script into a temp file and run it. Report any failures with the actual vs expected values.

After fixing any failures, update this test battery with the new cases.
