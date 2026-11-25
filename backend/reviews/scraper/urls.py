from urllib.parse import urlencode

REVIEWLIST_BASE = "https://www.booking.com/reviewlist.html"


def hotel_page_url(country_code: str, page_name: str, lang: str = "en-gb") -> str:
    """Public property page in a fixed UI language, so dates and labels parse consistently."""
    return f"https://www.booking.com/hotel/{country_code}/{page_name}.{lang}.html"


def build_reviewlist_url(
    country_code: str, page_name: str, *, offset: int = 0, rows: int = 25, lang: str = "en-gb"
) -> str:
    """
    LEGACY: Booking's old server-rendered review list. Returned 404 when probed
    on 2026-11-25; only used by the optional `--source http`.
    - sort=f_recent_desc: newest first, which is what makes incremental runs cheap.
    - lang=en-gb: fixes the UI language so dates/labels parse consistently.
      (Review bodies stay in the guest's original language.)
    """
    params = {
        "cc1": country_code,
        "pagename": page_name,
        "type": "total",
        "sort": "f_recent_desc",
        "lang": lang,
        "rows": rows,
        "offset": offset,
    }
    return f"{REVIEWLIST_BASE}?{urlencode(params)}"
