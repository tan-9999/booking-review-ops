"""
Recognise anti-bot / challenge responses.

Only called when a page has NO review cards (or on 202/403/405), so a stray
word inside a normal review page can't cause a false positive.

Note: normal Booking pages embed `awsWafCookieDomainList`, so it is NOT a
marker (verified against the live site, 2026-11-25). Only markers that appear
on the challenge interstitial itself are listed.
"""

CHALLENGE_MARKERS = (
    "awswafintegration",
    "gokuprops",
    "captcha-container",
    "px-captcha",
    "g-recaptcha",
    "h-captcha",
    "verify you are human",
    "are you a robot",
)


def detect_block(status: int, html: str) -> str | None:
    """Return a human-readable reason if this response is a block/challenge, else None."""
    if status in (403, 405):
        return f"HTTP {status} (access denied)"
    if status == 202 and len((html or "").strip()) < 5000:
        return "HTTP 202 with near-empty body (JavaScript challenge)"
    lower = (html or "").lower()
    for marker in CHALLENGE_MARKERS:
        if marker in lower:
            return f"challenge page detected ({marker})"
    return None
