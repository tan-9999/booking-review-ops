"""
Manual fallback: import review pages saved from a browser (Ctrl+S).
Uses exactly the same parser, validation and dedup as the live scraper, so
re-importing the same file is harmless.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from reviews.models import Property, ScrapeRun
from reviews.scraper.parsing import parse_review_page
from reviews.services.ingest import ingest_reviews


class Command(BaseCommand):
    help = "Import reviews from saved Booking.com review HTML files."

    def add_arguments(self, parser):
        parser.add_argument("files", nargs="+", help="Paths to saved .html files")
        parser.add_argument("--property", required=True, help="Property slug the files belong to")

    def handle(self, *args, **options):
        try:
            prop = Property.objects.get(slug=options["property"])
        except Property.DoesNotExist as exc:
            raise CommandError(f"Unknown property slug: {options['property']}") from exc

        for raw_path in options["files"]:
            path = Path(raw_path)
            if not path.exists():
                self.stderr.write(self.style.ERROR(f"Not found: {path}"))
                continue

            page = parse_review_page(path.read_text(encoding="utf-8", errors="replace"),
                                     source_url=f"file:{path.name}")
            if page.cards_found == 0:
                self.stderr.write(self.style.ERROR(
                    f"{path.name}: no review cards recognised (is this the reviews list page?)"
                ))
                continue

            stats = ingest_reviews(prop, page.reviews)
            problems = page.card_errors + stats.invalid_samples
            ScrapeRun.objects.create(
                property=prop,
                mode=ScrapeRun.Mode.FULL,
                fetcher="file",
                status=ScrapeRun.Status.PARTIAL if problems else ScrapeRun.Status.SUCCESS,
                finished_at=timezone.now(),
                pages_fetched=1,
                reviews_seen=page.cards_found,
                reviews_created=stats.created,
                reviews_updated=stats.updated,
                reviews_invalid=stats.invalid + len(page.card_errors),
                error_message="\n".join([f"file {path.name}"] + problems),
            )
            self.stdout.write(self.style.SUCCESS(
                f"{path.name} [{page.strategy}]: {page.cards_found} cards, "
                f"{stats.created} new, {stats.updated} updated, {stats.unchanged} unchanged, "
                f"{stats.invalid + len(page.card_errors)} invalid"
            ))
