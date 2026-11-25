import re
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

_BOOKING_HOTEL_PATH = re.compile(r"^/hotel/(?P<cc>[a-z]{2})/(?P<page>[^/.]+)", re.IGNORECASE)


def parse_booking_url(url: str) -> tuple[str, str]:
    """
    'https://www.booking.com/hotel/in/sample-inn.en-gb.html?aid=1' -> ('in', 'sample-inn')
    These two values identify the property on Booking's review list pages.
    """
    parsed = urlparse(url)
    if "booking.com" not in parsed.netloc:
        raise ValueError(f"Not a booking.com URL: {url}")
    match = _BOOKING_HOTEL_PATH.match(parsed.path)
    if not match:
        raise ValueError(f"Unrecognised Booking hotel URL format: {url}")
    return match.group("cc").lower(), match.group("page")


class Property(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=255)
    booking_url = models.URLField(max_length=500, unique=True)
    country_code = models.CharField(max_length=2, editable=False)
    page_name = models.CharField(max_length=255, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "properties"

    def __str__(self) -> str:
        return self.name

    def clean(self):
        try:
            parse_booking_url(self.booking_url)
        except ValueError as exc:
            raise ValidationError({"booking_url": str(exc)}) from exc

    def save(self, *args, **kwargs):
        self.country_code, self.page_name = parse_booking_url(self.booking_url)
        super().save(*args, **kwargs)


class Review(models.Model):
    class Sentiment(models.TextChoices):
        POSITIVE = "positive", "Positive (8–10)"
        MIXED = "mixed", "Mixed (6–7.9)"
        NEGATIVE = "negative", "Negative (below 6)"

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="reviews")

    # Identity / dedup
    external_id = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Booking's own review id, when the page exposes it.",
    )
    fingerprint = models.CharField(
        max_length=64, help_text="sha256 of normalized identity fields (fallback dedup key)."
    )
    content_hash = models.CharField(
        max_length=64, help_text="sha256 of mutable fields; changes when a review is edited."
    )

    # Core review data
    review_date = models.DateField(db_index=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1)  # Booking scale 1.0–10.0
    title = models.CharField(max_length=500, blank=True, default="")
    positive_text = models.TextField(blank=True, default="")
    negative_text = models.TextField(blank=True, default="")
    language = models.CharField(max_length=16, blank=True, default="")

    # Context
    reviewer_name = models.CharField(max_length=255, blank=True, default="")
    reviewer_country = models.CharField(max_length=100, blank=True, default="")
    traveller_type = models.CharField(max_length=100, blank=True, default="")
    room_type = models.CharField(max_length=255, blank=True, default="")
    nights = models.PositiveSmallIntegerField(null=True, blank=True)
    stay_date = models.DateField(null=True, blank=True, help_text="Month of stay (day set to 1).")
    hotel_response = models.TextField(blank=True, default="")
    helpful_votes = models.PositiveIntegerField(default=0)

    # Derived by reviews.services.classify (re-run when the classifier version changes)
    sentiment = models.CharField(max_length=8, choices=Sentiment.choices, blank=True, default="", db_index=True)
    classifier_version = models.PositiveSmallIntegerField(default=0)

    # Provenance
    source_url = models.URLField(max_length=1000, blank=True, default="")
    raw = models.JSONField(default=dict, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-review_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "fingerprint"], name="uniq_review_fingerprint_per_property"
            ),
            models.UniqueConstraint(
                fields=["property", "external_id"],
                condition=~Q(external_id=""),
                name="uniq_review_external_id_per_property",
            ),
            models.CheckConstraint(
                condition=Q(rating__gte=1) & Q(rating__lte=10), name="review_rating_1_to_10"
            ),
        ]
        indexes = [models.Index(fields=["property", "review_date"])]

    def __str__(self) -> str:
        return f"{self.property.slug} {self.review_date} {self.rating}"


class ReviewTopic(models.Model):
    """One operational topic mentioned in a review, with polarity and the sentence as evidence."""

    class Polarity(models.TextChoices):
        POSITIVE = "positive", "Praise"
        NEGATIVE = "negative", "Complaint"

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="topics")
    topic = models.CharField(max_length=32)  # key in reviews.insights.topics.TOPICS
    polarity = models.CharField(max_length=8, choices=Polarity.choices)
    snippet = models.TextField()
    matched = models.CharField(max_length=64, help_text="Phrase that triggered the tag (explainability).")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["review", "topic", "polarity"], name="uniq_topic_per_review_polarity"),
        ]
        indexes = [models.Index(fields=["topic", "polarity"])]

    def __str__(self) -> str:
        return f"{self.topic} ({self.polarity})"


class ScrapeRun(models.Model):
    class Mode(models.TextChoices):
        FULL = "full", "Full backfill"
        INCREMENTAL = "incremental", "Incremental"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial (some pages failed)"
        BLOCKED = "blocked", "Blocked by anti-bot challenge"
        FAILED = "failed", "Failed"

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="scrape_runs")
    mode = models.CharField(max_length=16, choices=Mode.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    fetcher = models.CharField(max_length=16, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    pages_fetched = models.PositiveIntegerField(default=0)
    pages_failed = models.PositiveIntegerField(default=0)
    reviews_seen = models.PositiveIntegerField(default=0)
    reviews_created = models.PositiveIntegerField(default=0)
    reviews_updated = models.PositiveIntegerField(default=0)
    reviews_invalid = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.property.slug} {self.mode} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}"
