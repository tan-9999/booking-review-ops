from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from reviews.models import Property, ScrapeRun
from reviews.scraper.fetchers import HttpFetcher
from reviews.scraper.runner import RunConfig, history_start, scrape_property
from reviews.scraper.sources import BrowserReviewSource, HttpReviewListSource


class Command(BaseCommand):
    help = (
        "Collect public Booking.com reviews for tracked properties. "
        "Default mode 'auto' = full backfill until one has completed successfully, incremental after that."
    )

    def add_arguments(self, parser):
        parser.add_argument("--property", action="append", dest="properties",
                            help="Property slug (repeatable). Default: all active properties.")
        parser.add_argument("--mode", choices=["auto", "incremental", "full"], default="auto")
        parser.add_argument("--source", choices=["browser", "http"], default="browser",
                            help="browser = live 'Read all reviews' panel (default); "
                                 "http = legacy /reviewlist.html endpoint.")
        parser.add_argument("--max-pages", type=int, help="Override SCRAPER_MAX_PAGES_PER_RUN.")
        parser.add_argument("--history-days", type=int,
                            help="Override SCRAPER_HISTORY_DAYS (0 = everything Booking lists).")
        parser.add_argument("--headless", action="store_true",
                            help="Run the browser without a window. Off by default: on 2026-11-25 "
                                 "Booking returned no review data to headless Chromium.")

    def handle(self, *args, **options):
        properties = self._properties(options["properties"])
        max_pages = options["max_pages"] or settings.SCRAPER["MAX_PAGES_PER_RUN"]
        source = self._build_source(options["source"], max_pages, options["headless"])
        runs: list[ScrapeRun] = []
        try:
            for prop in properties:
                mode = options["mode"]
                if mode == "auto":
                    mode = self._auto_mode(prop)
                config = RunConfig.from_settings(mode, max_pages=max_pages)
                if options["history_days"] is not None:
                    config.since = history_start(options["history_days"])

                window = f", since {config.since}" if config.since else ""
                self.stdout.write(f"\n> {prop.name} [{mode}, {source.name}{window}]")
                run = scrape_property(prop, source, config)
                runs.append(run)
                self._report(run)

                if run.status == ScrapeRun.Status.BLOCKED:
                    self.stdout.write(self.style.WARNING(
                        "Blocked by an anti-bot challenge. Skipping remaining properties; "
                        "try again later (or use import_review_html with saved pages)."
                    ))
                    break
        finally:
            source.close()

        bad = [r for r in runs if r.status in (ScrapeRun.Status.FAILED, ScrapeRun.Status.BLOCKED)]
        if bad:
            # Non-zero exit code so cron / CI alerting notices.
            raise CommandError(f"{len(bad)} run(s) failed or were blocked. See Scrape runs in the admin.")

    @staticmethod
    def _auto_mode(prop: Property) -> str:
        """
        Incremental only once a full backfill has completed successfully.
        "Has reviews" is not enough: a capped or interrupted first run would
        otherwise leave older pages missing forever, because incremental mode
        stops at the first pages it already knows.
        """
        backfilled = prop.scrape_runs.filter(
            mode=ScrapeRun.Mode.FULL, status=ScrapeRun.Status.SUCCESS
        ).exists()
        return "incremental" if backfilled else "full"

    def _properties(self, slugs):
        if slugs:
            found = list(Property.objects.filter(slug__in=slugs))
            missing = set(slugs) - {p.slug for p in found}
            if missing:
                raise CommandError(f"Unknown property slug(s): {', '.join(sorted(missing))}")
            return found
        found = list(Property.objects.filter(is_active=True))
        if not found:
            raise CommandError("No active properties. Run: python manage.py seed_properties")
        return found

    def _build_source(self, kind: str, max_pages: int, headless: bool):
        s = settings.SCRAPER
        if kind == "http":
            fetcher = HttpFetcher(timeout=s["TIMEOUT_SECONDS"], max_retries=s["MAX_RETRIES"])
            return HttpReviewListSource(fetcher, page_size=s["PAGE_SIZE"], max_pages=max_pages,
                                        delay_seconds=s["REQUEST_DELAY_SECONDS"])
        try:
            return BrowserReviewSource(timeout=s["TIMEOUT_SECONDS"], max_pages=max_pages,
                                       delay_seconds=s["REQUEST_DELAY_SECONDS"],
                                       max_retries=s["MAX_RETRIES"], headless=headless,
                                       screenshot_dir=s["SNAPSHOT_DIR"] / "_screenshots")
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc

    def _report(self, run: ScrapeRun):
        style = {
            ScrapeRun.Status.SUCCESS: self.style.SUCCESS,
            ScrapeRun.Status.PARTIAL: self.style.WARNING,
        }.get(run.status, self.style.ERROR)
        self.stdout.write(style(
            f"  {run.get_status_display()}: pages={run.pages_fetched} (failed {run.pages_failed}), "
            f"seen={run.reviews_seen}, new={run.reviews_created}, "
            f"updated={run.reviews_updated}, invalid={run.reviews_invalid}"
        ))
        if run.error_message:
            for line in run.error_message.splitlines()[:5]:
                self.stdout.write(f"    - {line}")
