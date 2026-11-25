"""
Export the collected data to sample_data/ (committed to the repository):

  reviews.json      full records incl. topic tags (re-importable with load_sample_data)
  reviews.csv       one row per review, opens directly in Excel / Google Sheets
  scrape_runs.csv   every collection run: evidence of how reliable collection was

Reviewer names are left out by default. They are public on Booking.com, but a
code repository doesn't need personal names; pass --include-names to keep them.
"""
import csv
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from reviews.insights.topics import CLASSIFIER_VERSION, TOPIC_LABELS
from reviews.models import Property, Review, ScrapeRun

CSV_FIELDS = [
    "property", "property_name", "review_date", "rating", "sentiment", "title",
    "positive_text", "negative_text", "complaint_topics", "praise_topics", "hotel_response",
    "reviewer_country", "traveller_type", "room_type", "nights", "stay_date",
]


def review_key(review: Review) -> str:
    """Stable id for the exported record (dedup key when re-imported)."""
    return review.external_id or review.fingerprint[:16]


class Command(BaseCommand):
    help = "Export collected reviews and scrape runs to sample_data/ (JSON + CSV)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(settings.BASE_DIR.parent / "sample_data"))
        parser.add_argument("--include-names", action="store_true", help="Keep reviewer first names.")

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        include_names = options["include_names"]

        reviews = (
            Review.objects.select_related("property").prefetch_related("topics")
            .order_by("property__slug", "-review_date", "id")
        )
        records = []
        for r in reviews:
            topics = sorted(r.topics.all(), key=lambda t: (t.polarity, t.topic))
            records.append({
                "id": review_key(r),
                "property": r.property.slug,
                "review_date": r.review_date.isoformat(),
                "rating": float(r.rating),
                "sentiment": r.sentiment,
                "title": r.title,
                "positive_text": r.positive_text,
                "negative_text": r.negative_text,
                "hotel_response": r.hotel_response,
                "reviewer_name": r.reviewer_name if include_names else "",
                "reviewer_country": r.reviewer_country,
                "traveller_type": r.traveller_type,
                "room_type": r.room_type,
                "nights": r.nights,
                "stay_date": r.stay_date.isoformat() if r.stay_date else None,
                "language": r.language,
                "topics": [{"topic": t.topic, "polarity": t.polarity, "snippet": t.snippet} for t in topics],
            })

        payload = {
            "exported_at": timezone.now().isoformat(),
            "classifier_version": CLASSIFIER_VERSION,
            "history_days": settings.SCRAPER["HISTORY_DAYS"],
            "reviewer_names_included": include_names,
            "properties": [
                {"slug": p.slug, "name": p.name, "url": p.booking_url}
                for p in Property.objects.order_by("slug")
            ],
            "reviews": records,
        }
        (out / "reviews.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

        names = {p.slug: p.name for p in Property.objects.all()}
        # utf-8-sig: Excel opens accented / non-Latin review text correctly
        with open(out / "reviews.csv", "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for rec in records:
                writer.writerow({
                    **{k: rec.get(k) for k in CSV_FIELDS if k in rec},
                    "property_name": names.get(rec["property"], ""),
                    "complaint_topics": "; ".join(TOPIC_LABELS[t["topic"]] for t in rec["topics"] if t["polarity"] == "negative"),
                    "praise_topics": "; ".join(TOPIC_LABELS[t["topic"]] for t in rec["topics"] if t["polarity"] == "positive"),
                })

        with open(out / "scrape_runs.csv", "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["property", "mode", "source", "status", "started_at", "finished_at", "pages_fetched",
                             "pages_failed", "reviews_seen", "created", "updated", "invalid", "first_error"])
            for run in ScrapeRun.objects.select_related("property").order_by("started_at"):
                writer.writerow([
                    run.property.slug, run.mode, run.fetcher, run.status, run.started_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else "", run.pages_fetched, run.pages_failed,
                    run.reviews_seen, run.reviews_created, run.reviews_updated, run.reviews_invalid,
                    (run.error_message.splitlines() or [""])[0],
                ])

        self.stdout.write(self.style.SUCCESS(
            f"Exported {len(records)} reviews and {ScrapeRun.objects.count()} scrape runs to {out}"
            + ("" if include_names else " (reviewer names omitted)")
        ))
