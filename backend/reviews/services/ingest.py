import logging
from dataclasses import dataclass, field
from enum import Enum

from django.db import IntegrityError, transaction
from django.utils import timezone

from reviews.models import Property, Review
from reviews.scraper.schema import ParsedReview
from reviews.services.classify import classify

logger = logging.getLogger(__name__)


class UpsertResult(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    INVALID = "invalid"


@dataclass
class IngestStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    invalid: int = 0
    invalid_samples: list[str] = field(default_factory=list)

    @property
    def seen(self) -> int:
        return self.created + self.updated + self.unchanged + self.invalid

    @property
    def new_or_changed(self) -> int:
        return self.created + self.updated

    def record(self, result: UpsertResult, detail: str = "") -> None:
        setattr(self, result.value, getattr(self, result.value) + 1)
        if result is UpsertResult.INVALID and len(self.invalid_samples) < 10:
            self.invalid_samples.append(detail)


_MUTABLE_FIELDS = [
    "rating", "title", "positive_text", "negative_text", "language",
    "reviewer_country", "traveller_type", "room_type", "nights", "stay_date",
    "hotel_response", "helpful_votes", "source_url",
]


def _find_existing(prop: Property, item: ParsedReview, fingerprint: str) -> Review | None:
    if item.external_id:
        match = Review.objects.filter(property=prop, external_id=item.external_id).first()
        if match:
            return match
    return Review.objects.filter(property=prop, fingerprint=fingerprint).first()


def upsert_review(prop: Property, item: ParsedReview) -> tuple[UpsertResult, str]:
    errors = item.validate()
    if errors:
        return UpsertResult.INVALID, "; ".join(errors)

    now = timezone.now()
    fingerprint = item.fingerprint(prop.slug)
    existing = _find_existing(prop, item, fingerprint)
    if (
        existing is not None
        and item.hotel_response_truncated
        and len(existing.hotel_response) > len(item.hotel_response)
    ):
        # Never downgrade a complete stored reply to the collapsed preview, and
        # don't count "preview vs full" as an edit (it caused false updates).
        item.hotel_response = existing.hotel_response
        item.hotel_response_truncated = False
    content_hash = item.content_hash()

    if existing is None:
        try:
            with transaction.atomic():
                review = Review.objects.create(
                    property=prop,
                    external_id=item.external_id,
                    fingerprint=fingerprint,
                    content_hash=content_hash,
                    review_date=item.review_date,
                    reviewer_name=item.reviewer_name,
                    raw=item.to_raw(),
                    last_seen_at=now,
                    **{name: getattr(item, name) for name in _MUTABLE_FIELDS},
                )
                classify(review)
            return UpsertResult.CREATED, ""
        except IntegrityError:
            # Another process inserted it between our lookup and insert.
            logger.info(
                "Concurrent insert for %s / %s; treating as unchanged", prop.slug, fingerprint[:12]
            )
            return UpsertResult.UNCHANGED, ""

    if existing.content_hash == content_hash:
        Review.objects.filter(pk=existing.pk).update(
            last_seen_at=now, helpful_votes=item.helpful_votes
        )
        return UpsertResult.UNCHANGED, ""

    for name in _MUTABLE_FIELDS:
        setattr(existing, name, getattr(item, name))
    existing.content_hash = content_hash
    existing.raw = item.to_raw()
    existing.last_seen_at = now
    if item.external_id and not existing.external_id:
        existing.external_id = item.external_id
    existing.save()
    classify(existing)  # text may have changed -> topics may have changed
    return UpsertResult.UPDATED, ""


def ingest_reviews(prop: Property, items: list[ParsedReview]) -> IngestStats:
    stats = IngestStats()
    for item in items:
        result, detail = upsert_review(prop, item)
        stats.record(result, detail)
        if result is UpsertResult.INVALID:
            logger.warning("Skipped invalid review for %s: %s", prop.slug, detail)
    return stats
