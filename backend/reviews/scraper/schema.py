"""
The contract between the scraper (which parses messy HTML) and storage
(which trusts nothing). Every parsed review becomes a ParsedReview, is
normalized, validated, and then hashed for dedup.
"""
import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta
from decimal import Decimal

_WHITESPACE = re.compile(r"\s+")
EARLIEST_PLAUSIBLE_DATE = date(2010, 1, 1)


def normalize_text(value: str | None) -> str:
    """Unicode-normalize and collapse whitespace. Keeps original casing for display."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    return _WHITESPACE.sub(" ", value).strip()


def _key(value: str) -> str:
    """Comparison form: normalized + case-folded."""
    return normalize_text(value).casefold()


def _sha256(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


@dataclass
class ParsedReview:
    review_date: date | None
    rating: Decimal | None
    external_id: str = ""
    title: str = ""
    positive_text: str = ""
    negative_text: str = ""
    language: str = ""
    reviewer_name: str = ""
    reviewer_country: str = ""
    traveller_type: str = ""
    room_type: str = ""
    nights: int | None = None
    stay_date: date | None = None
    hotel_response: str = ""
    helpful_votes: int = 0
    source_url: str = ""
    # True when the page showed only the start of the hotel reply ("Continue reading"
    # not expanded). Ingest then keeps any complete reply already stored.
    hotel_response_truncated: bool = False

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, str):
                setattr(self, f.name, normalize_text(value))

    def validate(self, today: date | None = None) -> list[str]:
        """Return a list of problems; empty list means the record is safe to store."""
        today = today or date.today()
        errors: list[str] = []

        if self.review_date is None:
            errors.append("missing review_date")
        elif self.review_date > today + timedelta(days=1):  # 1 day slack for time zones
            errors.append(f"review_date in the future: {self.review_date}")
        elif self.review_date < EARLIEST_PLAUSIBLE_DATE:
            errors.append(f"review_date implausibly old: {self.review_date}")

        if self.rating is None:
            errors.append("missing rating")
        elif not (Decimal("1") <= self.rating <= Decimal("10")):
            errors.append(f"rating out of range: {self.rating}")

        if self.nights is not None and not (1 <= self.nights <= 365):
            errors.append(f"nights out of range: {self.nights}")

        # Score-only reviews (no text) are valid on Booking and still count
        # toward averages, so we do NOT require text.
        return errors

    def fingerprint(self, property_slug: str) -> str:
        """Identity of a review. A stable id wins; otherwise normalized content."""
        if self.external_id:
            # With an id, content is not identity: two score-only "Good" reviews on
            # the same day are different reviews (seen when importing name-less sample data).
            return _sha256(property_slug, "id", self.external_id)
        return _sha256(
            property_slug,
            _key(self.reviewer_name),
            self.review_date.isoformat() if self.review_date else "",
            _key(self.title),
            _key(self.positive_text),
            _key(self.negative_text),
        )

    def content_hash(self) -> str:
        """Changes when anything we'd want to re-process changes (edit, hotel reply)."""
        return _sha256(
            str(self.rating),
            self.title,
            self.positive_text,
            self.negative_text,
            self.hotel_response,
        )

    def to_raw(self) -> dict:
        """JSON-safe copy kept on the row for auditing / reprocessing."""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, (date, Decimal)):
                data[key] = str(value)
        return data
