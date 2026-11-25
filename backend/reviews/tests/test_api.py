from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from reviews.models import Property, ScrapeRun
from reviews.scraper.schema import ParsedReview
from reviews.services.ingest import upsert_review

TODAY = date(2026, 11, 25)  # Wednesday: this week = 23–25 Nov, compared with 16–18 Nov


@patch("reviews.api.views.today", return_value=TODAY)
class ApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.p1 = Property.objects.create(slug="p1", name="Alpha", booking_url="https://www.booking.com/hotel/au/p1.html")
        cls.p2 = Property.objects.create(slug="p2", name="Beta", booking_url="https://www.booking.com/hotel/au/p2.html")
        rows = [
            (cls.p1, date(2026, 11, 23), "9.0", "Great location", ""),
            (cls.p1, date(2026, 11, 24), "4.0", "", "Dirty bathroom"),
            (cls.p1, date(2026, 11, 17), "6.0", "", "Noisy at night"),
            (cls.p2, date(2026, 11, 25), "8.0", "Friendly staff", ""),
            (cls.p2, date(2026, 11, 16), "10.0", "Clean", ""),
            (cls.p2, date(2026, 10, 1), "5.0", "", "Rude staff"),
        ]
        for i, (prop, day, rating, liked, disliked) in enumerate(rows):
            upsert_review(prop, ParsedReview(review_date=day, rating=Decimal(rating), reviewer_name=f"G{i}",
                                             positive_text=liked, negative_text=disliked))

    # ---------------------------------------------------------------- summary
    def test_summary_compares_same_weekdays(self, _today):
        data = self.client.get("/api/summary/").json()
        self.assertEqual(data["period"]["start"], "2026-11-23")
        self.assertEqual(data["period"]["end"], "2026-11-25")
        self.assertEqual(data["previous_period"]["end"], "2026-11-18")
        overall = data["overall"]
        self.assertEqual(overall["current"]["average_rating"], 7.0)   # 9, 4, 8
        self.assertEqual(overall["previous"]["average_rating"], 8.0)  # 6, 10
        self.assertEqual(overall["rating_change"], -1.0)
        self.assertEqual(overall["current"]["negative_share"], 33.3)
        self.assertTrue(overall["current"]["low_sample"])

    def test_summary_per_property(self, _today):
        data = self.client.get("/api/summary/").json()
        alpha = next(p for p in data["properties"] if p["slug"] == "p1")
        self.assertEqual(alpha["current"]["average_rating"], 6.5)
        self.assertEqual(alpha["rating_change"], 0.5)

    def test_summary_property_filter(self, _today):
        data = self.client.get("/api/summary/?property=p2").json()
        self.assertEqual([p["slug"] for p in data["properties"]], ["p2"])
        self.assertEqual(data["overall"]["current"]["review_count"], 1)

    # ---------------------------------------------------------------- feed
    def test_feed_filters(self, _today):
        get = lambda q: self.client.get(f"/api/reviews/?{q}").json()
        self.assertEqual(get("property=p1")["count"], 3)
        self.assertEqual(get("sentiment=negative")["count"], 2)
        self.assertEqual(get("topic=cleanliness")["count"], 2)                       # complaint + praise
        self.assertEqual(get("topic=cleanliness&polarity=negative")["count"], 1)
        self.assertEqual(get("search=noisy")["count"], 1)
        self.assertEqual(get("start=2026-11-23&end=2026-11-25")["count"], 3)
        self.assertEqual(get("min_rating=8")["count"], 3)

    def test_feed_ordering_pagination_and_shape(self, _today):
        data = self.client.get("/api/reviews/?ordering=lowest&page_size=2").json()
        self.assertEqual(data["count"], 6)
        self.assertEqual(len(data["results"]), 2)
        first = data["results"][0]
        self.assertEqual(first["rating"], 4.0)
        self.assertEqual(first["property_name"], "Alpha")
        self.assertEqual(first["topics"][0]["label"], "Cleanliness")
        self.assertEqual(first["topics"][0]["snippet"], "Dirty bathroom")

    def test_bad_filters_return_400(self, _today):
        cases = {
            "property=nope": "property",
            "start=2026-11-30&end=2026-11-01": "end",
            "start=yesterday": "start",
            "polarity=negative": "polarity",
            "topic=wifi": "topic",
            "min_rating=11": "min_rating",
        }
        for query, field in cases.items():
            response = self.client.get(f"/api/reviews/?{query}")
            self.assertEqual(response.status_code, 400, query)
            self.assertIn(field, response.json(), query)

    # ---------------------------------------------------------------- trends / topics
    def test_weekly_trend_fills_empty_weeks(self, _today):
        data = self.client.get("/api/trends/?start=2026-11-02&end=2026-11-25").json()
        weeks = [p["period_start"] for p in data["points"]]
        self.assertEqual(weeks, ["2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23"])
        self.assertEqual(data["points"][0]["review_count"], 0)
        self.assertIsNone(data["points"][0]["average_rating"])
        week16 = data["points"][2]
        self.assertEqual((week16["review_count"], week16["average_rating"]), (2, 8.0))
        self.assertEqual((week16["positive"], week16["mixed"]), (1, 1))

    def test_monthly_trend(self, _today):
        data = self.client.get("/api/trends/?start=2026-10-01&end=2026-11-25&granularity=month").json()
        self.assertEqual([p["review_count"] for p in data["points"]], [1, 5])

    def test_topics_stats_and_trend(self, _today):
        data = self.client.get("/api/topics/?start=2026-11-16&end=2026-11-25").json()
        self.assertEqual(data["review_count"], 5)
        clean = next(t for t in data["topics"] if t["topic"] == "cleanliness")
        self.assertEqual((clean["complaints"], clean["praise"]), (1, 1))
        this_week = data["trend"][-1]
        self.assertEqual(this_week["complaints"]["cleanliness"], 1)
        self.assertEqual(this_week["complaint_share"]["cleanliness"], 33.3)

    # ---------------------------------------------------------------- insights
    def test_insights_guard_small_samples(self, _today):
        data = self.client.get("/api/insights/").json()
        self.assertEqual(data["period"]["label"], "this week so far")
        self.assertEqual(len(data["insights"]), 1)
        self.assertTrue(data["insights"][0]["text"].startswith("Only 3 reviews"))

    def test_insights_custom_range_compares_previous_equal_period(self, _today):
        data = self.client.get("/api/insights/?start=2026-11-16&end=2026-11-25").json()
        self.assertEqual(data["previous_period"]["label"], "the previous 10 days")
        self.assertEqual(data["previous_period"]["end"], "2026-11-15")

    # ---------------------------------------------------------------- properties / meta
    def test_properties_freshness(self, _today):
        ScrapeRun.objects.create(property=self.p1, mode="full", status=ScrapeRun.Status.FAILED,
                                 error_message="page 1: blocked\nmore")
        ScrapeRun.objects.create(property=self.p2, mode="full", status=ScrapeRun.Status.SUCCESS,
                                 finished_at=timezone.now())
        data = {p["slug"]: p for p in self.client.get("/api/properties/").json()}
        self.assertEqual(data["p1"]["freshness"], "failing")
        self.assertEqual(data["p1"]["last_run"]["message"], "page 1: blocked")
        self.assertEqual(data["p2"]["freshness"], "ok")
        self.assertEqual(data["p2"]["review_count"], 3)

    def test_meta_lists_topics_and_definitions(self, _today):
        data = self.client.get("/api/meta/").json()
        self.assertEqual(len(data["topics"]), 9)
        self.assertIn("negative_review", data["definitions"])
