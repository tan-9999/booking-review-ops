from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from reviews.insights.topics import CLASSIFIER_VERSION
from reviews.models import Property, Review, ReviewTopic
from reviews.scraper.schema import ParsedReview
from reviews.services.classify import classify_pending
from reviews.services.ingest import UpsertResult, upsert_review
from reviews.services.insights import generate_insights, topic_stats, week_comparison, week_range

WEEK = date(2026, 11, 2)  # a Monday
PREV_WEEK = WEEK - timedelta(days=7)


class Base(TestCase):
    def setUp(self):
        self.prop = Property.objects.create(slug="p1", name="P1",
                                            booking_url="https://www.booking.com/hotel/au/p1.html")
        self.n = 0

    def add(self, rating, day=WEEK, liked="", disliked="", external_id=""):
        self.n += 1
        return upsert_review(self.prop, ParsedReview(
            review_date=day, rating=Decimal(rating), reviewer_name=f"Guest{self.n}",
            positive_text=liked, negative_text=disliked, external_id=external_id,
        ))[0]

    def week(self, start):
        return Review.objects.filter(review_date__range=(start, start + timedelta(days=6)))

    def texts(self, insights):
        return [i["text"] for i in insights]


class ClassificationOnIngestTests(Base):
    def test_new_review_is_tagged_and_scored(self):
        self.add("4.0", disliked="The bathroom was dirty.")
        review = Review.objects.get()
        self.assertEqual(review.sentiment, Review.Sentiment.NEGATIVE)
        self.assertEqual(review.classifier_version, CLASSIFIER_VERSION)
        topic = review.topics.get(topic="cleanliness")
        self.assertEqual(topic.polarity, ReviewTopic.Polarity.NEGATIVE)
        self.assertEqual(topic.snippet, "The bathroom was dirty.")

    def test_edited_review_is_retagged(self):
        self.add("4.0", disliked="Dirty floors", external_id="r1")
        self.n -= 1  # same guest
        result = self.add("4.0", disliked="Very noisy street", external_id="r1")
        self.assertEqual(result, UpsertResult.UPDATED)
        topics = set(Review.objects.get().topics.values_list("topic", flat=True))
        self.assertIn("noise", topics)
        self.assertNotIn("cleanliness", topics)

    def test_classify_pending_tags_outdated_reviews(self):
        self.add("9.0", liked="Great location")
        ReviewTopic.objects.all().delete()
        Review.objects.update(classifier_version=0, sentiment="")
        self.assertEqual(classify_pending(), 1)
        self.assertEqual(classify_pending(), 0)  # nothing left to do
        self.assertTrue(ReviewTopic.objects.filter(topic="location").exists())
        self.assertEqual(Review.objects.get().sentiment, Review.Sentiment.POSITIVE)


class InsightTests(Base):
    def seed_week(self):
        self.add("3.0", disliked="Dirty room")
        self.add("4.0", disliked="Filthy bathroom and noisy street")
        self.add("5.0", disliked="Noisy at night")
        self.add("2.0", disliked="Rude staff")
        self.add("9.0", liked="Great location")
        self.add("9.0", liked="Great location, clean")

    def test_share_of_negative_reviews_sentence(self):
        self.seed_week()
        texts = self.texts(generate_insights(self.week(WEEK)))
        self.assertIn("50% of negative reviews this week mentioned cleanliness issues (2 of 4).", texts)
        self.assertIn("Location is the most praised topic this week: 33% of reviews (2 of 6).", texts)

    def test_topic_stats_count_reviews_not_mentions(self):
        self.seed_week()
        stats = {s.topic: s for s in topic_stats(self.week(WEEK))}
        self.assertEqual(stats["cleanliness"].complaints, 2)
        self.assertEqual(stats["cleanliness"].praise, 1)
        self.assertEqual(stats["noise"].share_of_negative_reviews, 50.0)

    def test_too_few_reviews_gives_no_percentages(self):
        self.add("3.0", disliked="Dirty")
        self.add("9.0", liked="Clean")
        insights = generate_insights(self.week(WEEK))
        self.assertEqual(len(insights), 1)
        self.assertTrue(insights[0]["text"].startswith("Only 2 reviews this week"))

    def test_week_on_week_rise_is_flagged(self):
        for _ in range(5):
            self.add("9.0", day=PREV_WEEK, liked="Great location")
        for _ in range(3):
            self.add("4.0", disliked="Very noisy at night")
        for _ in range(2):
            self.add("9.0", liked="Great location")
        texts = self.texts(generate_insights(self.week(WEEK), self.week(PREV_WEEK)))
        self.assertIn("Noise complaints rose from 0% of reviews last week to 60% this week.", texts)

    def test_week_range_is_monday_to_sunday(self):
        self.assertEqual(week_range(date(2026, 11, 5)), (date(2026, 11, 2), date(2026, 11, 8)))


class WeekComparisonTests(TestCase):
    def test_unfinished_week_is_compared_like_for_like(self):
        wednesday = date(2026, 11, 25)
        period, previous = week_comparison(wednesday)
        self.assertEqual((period.start, period.end), (date(2026, 11, 23), wednesday))
        self.assertEqual((previous.start, previous.end), (date(2026, 11, 16), date(2026, 11, 18)))
        self.assertEqual((period.label, previous.label), ("this week so far", "the same days last week"))

    def test_past_week_uses_full_weeks_and_dated_label(self):
        period, previous = week_comparison(date(2026, 11, 25), weeks_ago=1)
        self.assertEqual((period.start, period.end), (date(2026, 11, 16), date(2026, 11, 22)))
        self.assertEqual((previous.start, previous.end), (date(2026, 11, 9), date(2026, 11, 15)))
        self.assertEqual(period.label, "in the week of 16 Nov")

    def test_sunday_counts_as_complete_week(self):
        period, previous = week_comparison(date(2026, 11, 29))
        self.assertEqual(period.label, "this week")
        self.assertEqual(previous.end, date(2026, 11, 22))
