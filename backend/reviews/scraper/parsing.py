"""
HTML -> ParsedReview. Pure functions: no Django, no network, so it is easy to
test against saved pages.

Booking has served (at least) two markups for review lists:
  * the current "Read all reviews" panel with data-testid attributes
    (review-card, review-score, ...): what the live site serves today
  * the older server-rendered reviewlist markup (li.review_list_new_item_block),
    kept so saved pages and the legacy endpoint still parse

Each markup is a "strategy"; the first one that finds review cards wins.
When Booking ships a new layout, you add or fix a strategy here and nothing
else (fetching, dedup, storage) has to change.
"""
import copy
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag

from .schema import ParsedReview, normalize_text

# ---------------------------------------------------------------- value parsers

_MONTHS: dict[str, int] = {}
for _i, _name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"],
    start=1,
):
    _MONTHS[_name] = _i
    _MONTHS[_name[:3]] = _i
_MONTHS["sept"] = 9

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_MONTH_DAY_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_NUMBER = re.compile(r"\d{1,2}(?:[.,]\d{1,2})?")
_INT = re.compile(r"\d+")
_NIGHTS = re.compile(r"(\d+)\s*nights?\b", re.IGNORECASE)
_COLLAPSED_TOGGLE = re.compile(r"continue reading|read more", re.IGNORECASE)

NO_COMMENT_PHRASES = {"there are no comments available for this review"}


def _safe_date(year, month, day) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def parse_review_date(text: str) -> date | None:
    """'Reviewed: 12 November 2025' / 'Reviewed: November 12, 2025' / '2025-11-12'."""
    if not text:
        return None
    if m := _ISO_DATE.search(text):
        return _safe_date(*m.groups())
    for m in _DAY_MONTH_YEAR.finditer(text):
        month = _MONTHS.get(m.group(2).lower())
        if month:
            return _safe_date(m.group(3), month, m.group(1))
    for m in _MONTH_DAY_YEAR.finditer(text):
        month = _MONTHS.get(m.group(1).lower())
        if month:
            return _safe_date(m.group(3), month, m.group(2))
    return None


def parse_month_year(text: str) -> date | None:
    """'3 nights · October 2025' -> date(2025, 10, 1)."""
    if not text:
        return None
    for m in _MONTH_YEAR.finditer(text):
        month = _MONTHS.get(m.group(1).lower())
        if month:
            return _safe_date(m.group(2), month, 1)
    return None


def parse_rating(text: str) -> Decimal | None:
    """'Scored 8.0' / '8,5' / '10' -> Decimal. Returns None if no 1–10 number is present."""
    if not text:
        return None
    for m in _NUMBER.finditer(text):
        try:
            value = Decimal(m.group().replace(",", "."))
        except InvalidOperation:
            continue
        if Decimal("1") <= value <= Decimal("10"):
            return value.quantize(Decimal("0.1"))
    return None


def parse_int(text: str) -> int | None:
    m = _INT.search(text or "")
    return int(m.group()) if m else None


# ---------------------------------------------------------------- DOM helpers

def _text(node: Tag, *selectors: str) -> str:
    """Text of the first selector that matches and is non-empty."""
    for selector in selectors:
        el = node.select_one(selector)
        if el is not None:
            value = normalize_text(el.get_text(" ", strip=True))
            if value:
                return value
    return ""


def _clean_body(text: str) -> str:
    value = normalize_text(text)
    if value.casefold() in NO_COMMENT_PHRASES:
        return ""
    return value


def _nights(text: str) -> int | None:
    m = _NIGHTS.search(text or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- strategies

class LegacyReviewListStrategy:
    """Server-rendered markup served by /reviewlist.html."""

    name = "legacy-reviewlist"

    def find_cards(self, soup: BeautifulSoup) -> list[Tag]:
        return soup.select("li.review_list_new_item_block") or soup.select("div.c-review-block")

    def parse_card(self, card: Tag, source_url: str) -> ParsedReview:
        positive, negative, language = self._bodies(card)
        stay_text = _text(card, ".c-review-block__stay-date .bui-list__body", ".c-review-block__stay-date")
        stay_month_text = _text(card, ".c-review-block__stay-date .c-review-block__date")
        return ParsedReview(
            external_id=(card.get("data-review-url") or card.get("data-review-id") or "").strip(),
            review_date=self._review_date(card),
            rating=parse_rating(_text(card, ".bui-review-score__badge", ".review-score-badge")),
            title=_text(card, ".c-review-block__title"),
            positive_text=positive,
            negative_text=negative,
            language=language,
            reviewer_name=_text(card, ".bui-avatar-block__title"),
            reviewer_country=_text(card, ".bui-avatar-block__subtitle"),
            traveller_type=_text(card, ".review-panel-wide__traveller_type .bui-list__body"),
            room_type=_text(card, ".c-review-block__room-info-row .bui-list__body", ".c-review-block__room-link"),
            nights=_nights(stay_text),
            stay_date=parse_month_year(stay_month_text) or parse_month_year(stay_text),
            hotel_response=_text(card, ".c-review-block__response__body"),
            helpful_votes=parse_int(_text(card, ".review-helpful__vote-others-helpful")) or 0,
            source_url=source_url,
        )

    @staticmethod
    def _review_date(card: Tag) -> date | None:
        candidates = [
            el for el in card.select(".c-review-block__date")
            if el.find_parent(class_="c-review-block__stay-date") is None
        ]
        # Prefer the element that says "Reviewed: ..."
        candidates.sort(key=lambda el: "review" not in el.get_text().lower())
        for el in candidates:
            parsed = parse_review_date(el.get_text(" ", strip=True))
            if parsed:
                return parsed
        return None

    @staticmethod
    def _bodies(card: Tag) -> tuple[str, str, str]:
        positive: list[str] = []
        negative: list[str] = []
        language = ""
        for index, row in enumerate(card.select(".c-review__row")):
            body = row.select_one(".c-review__body")
            if body is None:
                continue
            text = _clean_body(body.get_text(" ", strip=True))
            if not text:
                continue
            language = language or (body.get("lang") or "")
            prefix = row.select_one(".c-review__prefix")
            prefix_classes = " ".join(prefix.get("class", [])) if prefix else ""
            row_classes = " ".join(row.get("class", []))
            label = normalize_text(prefix.get_text(" ", strip=True)).casefold() if prefix else ""

            if "green" in prefix_classes or label.startswith("liked"):
                positive.append(text)
            elif "red" in prefix_classes or "lalala" in row_classes or label.startswith("disliked"):
                negative.append(text)
            elif index == 0:
                positive.append(text)  # unlabelled: Booking renders "liked" first
            else:
                negative.append(text)
        return " ".join(positive), " ".join(negative), language


class DataTestIdStrategy:
    """
    Current markup of the "Read all reviews" panel (verified live 2026-11-25).
    Cards carry no review id, so dedup falls back to the content fingerprint.
    """

    name = "testid-cards"

    def find_cards(self, soup: BeautifulSoup) -> list[Tag]:
        return soup.select('[data-testid="review-card"]')

    def parse_card(self, card: Tag, source_url: str) -> ParsedReview:
        def by_id(test_id: str) -> str:
            return _text(card, f'[data-testid="{test_id}"]')

        name, country = self._avatar(card)
        reply, truncated = self._partner_reply(card)
        return ParsedReview(
            external_id=(card.get("data-review-id") or "").strip(),
            review_date=parse_review_date(by_id("review-date")),
            rating=parse_rating(by_id("review-score")),
            title=by_id("review-title"),
            positive_text=_clean_body(by_id("review-positive-text")),
            negative_text=_clean_body(by_id("review-negative-text")),
            reviewer_name=name,
            reviewer_country=country,
            traveller_type=by_id("review-traveler-type"),
            room_type=by_id("review-room-name"),
            nights=_nights(by_id("review-num-nights")),
            stay_date=parse_month_year(by_id("review-stay-date")),
            hotel_response=reply,
            hotel_response_truncated=truncated,
            source_url=source_url,
        )

    @staticmethod
    def _partner_reply(card: Tag) -> tuple[str, bool]:
        reply = card.select_one('[data-testid="review-partner-reply"]')
        if reply is None:
            return "", False
        truncated = any(
            _COLLAPSED_TOGGLE.search(b.get_text(" ", strip=True)) for b in reply.select("button")
        )
        reply = copy.copy(reply)  # deep copy: don't mutate the parsed page
        # Drop the "Hotel response:" heading, the "Continue reading" toggle and icons.
        for el in reply.select('h3, button, [aria-hidden="true"]'):
            el.decompose()
        return normalize_text(reply.get_text(" ", strip=True)), truncated

    @staticmethod
    def _avatar(card: Tag) -> tuple[str, str]:
        avatar = card.select_one('[data-testid="review-avatar"]')
        if avatar is None:
            return "", ""
        # Skip the single-letter avatar placeholder.
        parts = [s for s in (normalize_text(x) for x in avatar.stripped_strings) if len(s) > 1]
        return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")


STRATEGIES = (LegacyReviewListStrategy(), DataTestIdStrategy())


@dataclass
class PageParseResult:
    reviews: list[ParsedReview]
    strategy: str | None
    cards_found: int
    card_errors: list[str] = field(default_factory=list)
    looks_empty: bool = False  # page is a valid review list with nothing in it (end of pagination)


def parse_review_page(html: str, source_url: str = "") -> PageParseResult:
    soup = BeautifulSoup(html or "", "lxml")
    for strategy in STRATEGIES:
        cards = strategy.find_cards(soup)
        if not cards:
            continue
        reviews: list[ParsedReview] = []
        errors: list[str] = []
        for card in cards:
            try:
                reviews.append(strategy.parse_card(card, source_url))
            except Exception as exc:  # one bad card must not lose the whole page
                errors.append(f"{strategy.name}: {type(exc).__name__}: {exc}")
        return PageParseResult(reviews, strategy.name, len(cards), errors)

    # Only an actual, empty review-list container means "end of list". Matching
    # page text (e.g. "no reviews") produced false successes on normal property
    # pages, so a page without the container is treated as unrecognised.
    looks_empty = soup.select_one("ul.review_list") is not None
    return PageParseResult([], None, 0, [], looks_empty)
