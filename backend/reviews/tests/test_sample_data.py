import csv
import io
import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from reviews.models import Property, Review, ReviewTopic
from reviews.scraper.schema import ParsedReview
from reviews.services.ingest import upsert_review


class SampleDataTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        prop = Property.objects.create(slug="p1", name="Alpha", booking_url="https://www.booking.com/hotel/au/p1.html")
        upsert_review(prop, ParsedReview(review_date=date(2026, 11, 2), rating=Decimal("4.0"), reviewer_name="Priya",
                                         title="Noisy", negative_text="Dirty bathroom and loud music"))
        # Two score-only reviews that differ only by guest: must both survive a name-less export.
        for name in ("Tom", "Ana"):
            upsert_review(prop, ParsedReview(review_date=date(2026, 11, 3), rating=Decimal("7.0"),
                                             reviewer_name=name, title="Good"))

    def export(self, *args):
        call_command("export_sample_data", "--out", str(self.out), *args, stdout=io.StringIO())

    def test_export_writes_json_and_csv_without_names(self):
        self.export()
        data = json.loads((self.out / "reviews.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["reviews"]), 3)
        self.assertFalse(data["reviewer_names_included"])
        self.assertTrue(all(r["reviewer_name"] == "" for r in data["reviews"]))
        rows = list(csv.DictReader(open(self.out / "reviews.csv", encoding="utf-8-sig")))
        noisy = next(r for r in rows if r["title"] == "Noisy")
        self.assertIn("Cleanliness", noisy["complaint_topics"])
        self.assertIn("Noise", noisy["complaint_topics"])
        self.assertTrue((self.out / "scrape_runs.csv").exists())

    def test_include_names_flag(self):
        self.export("--include-names")
        data = json.loads((self.out / "reviews.json").read_text(encoding="utf-8"))
        self.assertIn("Priya", {r["reviewer_name"] for r in data["reviews"]})

    def test_round_trip_into_fresh_database_is_exact_and_idempotent(self):
        self.export()
        Review.objects.all().delete()
        Property.objects.all().delete()
        path = str(self.out / "reviews.json")
        call_command("load_sample_data", "--file", path, stdout=io.StringIO())
        call_command("load_sample_data", "--file", path, stdout=io.StringIO())  # second load: no change
        self.assertEqual(Review.objects.count(), 3)  # name-less score-only reviews not merged
        self.assertEqual(Property.objects.get().name, "Alpha")
        self.assertTrue(ReviewTopic.objects.filter(topic="noise", polarity="negative").exists())

    def test_refuses_to_mix_sample_data_with_scraped_reviews(self):
        self.export()
        with self.assertRaises(CommandError):
            call_command("load_sample_data", "--file", str(self.out / "reviews.json"), stdout=io.StringIO())
