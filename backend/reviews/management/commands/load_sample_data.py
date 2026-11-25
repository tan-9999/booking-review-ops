"""
Load sample_data/reviews.json into the database, so the dashboard can be tried
without running the scraper (which needs a visible browser and live access to
Booking.com). Uses the normal ingest path: validation, dedup and topic tagging
all apply, and loading twice changes nothing.
"""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from reviews.models import Property, Review
from reviews.scraper.schema import ParsedReview
from reviews.services.ingest import IngestStats, UpsertResult, upsert_review


class Command(BaseCommand):
    help = "Load sample_data/reviews.json (properties + reviews) into the database."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(settings.BASE_DIR.parent / "sample_data" / "reviews.json"))

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"Not found: {path}. Run export_sample_data first, or check the path.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"{path} is not valid JSON: {exc}") from exc

        if Review.objects.exclude(external_id__startswith="sample-").exists():
            # Exported records may omit reviewer names, which are part of the
            # scraped reviews' fingerprint, so mixing the two would duplicate reviews.
            raise CommandError(
                "This database already contains scraped reviews. Sample data is meant for a fresh "
                "database: delete backend/db.sqlite3 and run migrate first."
            )

        props = {}
        for p in data.get("properties", []):
            props[p["slug"]], _ = Property.objects.update_or_create(
                slug=p["slug"], defaults={"name": p["name"], "booking_url": p["url"], "is_active": True}
            )

        stats = IngestStats()
        for rec in data.get("reviews", []):
            prop = props.get(rec["property"])
            if prop is None:
                stats.record(UpsertResult.INVALID, f"unknown property {rec['property']}")
                continue
            item = ParsedReview(
                # Exported records carry a stable id; using it as the external id keeps
                # dedup exact even when reviewer names were omitted from the export.
                external_id=f"sample-{rec['id']}" if not str(rec["id"]).startswith("sample-") else rec["id"],
                review_date=date.fromisoformat(rec["review_date"]),
                rating=Decimal(str(rec["rating"])),
                title=rec.get("title", ""),
                positive_text=rec.get("positive_text", ""),
                negative_text=rec.get("negative_text", ""),
                hotel_response=rec.get("hotel_response", ""),
                reviewer_name=rec.get("reviewer_name", ""),
                reviewer_country=rec.get("reviewer_country", ""),
                traveller_type=rec.get("traveller_type", ""),
                room_type=rec.get("room_type", ""),
                nights=rec.get("nights"),
                stay_date=date.fromisoformat(rec["stay_date"]) if rec.get("stay_date") else None,
                language=rec.get("language", ""),
                source_url=f"sample:{path.name}",
            )
            result, detail = upsert_review(prop, item)
            stats.record(result, detail)

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {path.name}: {stats.created} new, {stats.updated} updated, "
            f"{stats.unchanged} unchanged, {stats.invalid} invalid."
        ))
