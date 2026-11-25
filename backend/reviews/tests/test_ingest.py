from datetime import date
from decimal import Decimal

from django.test import TestCase

from reviews.models import Property, Review, parse_booking_url
from reviews.scraper.schema import ParsedReview
from reviews.services.ingest import UpsertResult, ingest_reviews, upsert_review


def make_review(**overrides) -> ParsedReview:
    data = dict(
        review_date=date(2026, 11, 20),
        rating=Decimal("8.0"),
        reviewer_name="Asha",
        title="Great stay",
        positive_text="Friendly staff and clean room.",
        negative_text="A bit noisy at night.",
    )
    data.update(overrides)
    return ParsedReview(**data)


class ParseBookingUrlTests(TestCase):
    def test_extracts_country_and_page(self):
        cc, page = parse_booking_url("https://www.booking.com/hotel/in/sample-inn.en-gb.html?aid=1")
        self.assertEqual((cc, page), ("in", "sample-inn"))

    def test_rejects_non_booking(self):
        with self.assertRaises(ValueError):
            parse_booking_url("https://example.com/hotel/in/x.html")


class UpsertTests(TestCase):
    def setUp(self):
        self.prop = Property.objects.create(
            slug="p1", name="P1", booking_url="https://www.booking.com/hotel/in/p1.html"
        )

    def test_create_then_unchanged(self):
        self.assertEqual(upsert_review(self.prop, make_review())[0], UpsertResult.CREATED)
        self.assertEqual(upsert_review(self.prop, make_review())[0], UpsertResult.UNCHANGED)
        self.assertEqual(Review.objects.count(), 1)

    def test_whitespace_and_case_do_not_create_duplicates(self):
        upsert_review(self.prop, make_review())
        result, _ = upsert_review(
            self.prop, make_review(title="  GREAT   stay ", reviewer_name="asha")
        )
        # Same identity -> no new row. Title casing differs, so content hash differs -> UPDATED.
        self.assertEqual(result, UpsertResult.UPDATED)
        self.assertEqual(Review.objects.count(), 1)

    def test_external_id_tracks_edits(self):
        upsert_review(self.prop, make_review(external_id="abc123"))
        result, _ = upsert_review(
            self.prop, make_review(external_id="abc123", negative_text="Edited: very noisy.")
        )
        self.assertEqual(result, UpsertResult.UPDATED)
        self.assertEqual(Review.objects.get().negative_text, "Edited: very noisy.")

    def test_hotel_response_counts_as_update(self):
        upsert_review(self.prop, make_review(external_id="r1"))
        result, _ = upsert_review(
            self.prop, make_review(external_id="r1", hotel_response="Thank you!")
        )
        self.assertEqual(result, UpsertResult.UPDATED)

    def test_truncated_reply_never_overwrites_full_reply(self):
        full = "Thank you for staying with us. We have fixed the air conditioning."
        upsert_review(self.prop, make_review(hotel_response=full))
        result, _ = upsert_review(self.prop, make_review(
            hotel_response="Thank you for staying...", hotel_response_truncated=True))
        self.assertEqual(result, UpsertResult.UNCHANGED)  # not a false "update"
        self.assertEqual(Review.objects.get().hotel_response, full)

    def test_full_reply_repairs_truncated_one(self):
        upsert_review(self.prop, make_review(
            hotel_response="Thank you for staying...", hotel_response_truncated=True))
        full = "Thank you for staying with us. We have fixed the air conditioning."
        result, _ = upsert_review(self.prop, make_review(hotel_response=full))
        self.assertEqual(result, UpsertResult.UPDATED)
        self.assertEqual(Review.objects.get().hotel_response, full)

    def test_invalid_rating_rejected(self):
        result, detail = upsert_review(self.prop, make_review(rating=Decimal("11")))
        self.assertEqual(result, UpsertResult.INVALID)
        self.assertIn("rating", detail)
        self.assertEqual(Review.objects.count(), 0)

    def test_future_date_rejected(self):
        result, _ = upsert_review(self.prop, make_review(review_date=date(2099, 1, 1)))
        self.assertEqual(result, UpsertResult.INVALID)

    def test_score_only_review_is_valid(self):
        result, _ = upsert_review(
            self.prop, make_review(title="", positive_text="", negative_text="")
        )
        self.assertEqual(result, UpsertResult.CREATED)

    def test_batch_stats(self):
        stats = ingest_reviews(self.prop, [
            make_review(), make_review(),
            make_review(reviewer_name="Ben"),
            make_review(rating=None),
        ])
        self.assertEqual(
            (stats.created, stats.unchanged, stats.invalid, stats.seen), (2, 1, 1, 4)
        )
