"""End-to-end runner tests with a fake page source: pagination, incremental stop, blocks, failures."""
import io
import tempfile
from datetime import date
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from reviews.models import Property, Review, ScrapeRun
from reviews.scraper.errors import BlockedError, PermanentFetchError, TransientFetchError
from reviews.scraper.fetchers import FetchResult
from reviews.scraper.runner import RunConfig, scrape_property

FIXTURES = Path(__file__).parent / "fixtures"


def card(review_id: str, rating: str = "8.0", day: int = 20, month: str = "October", year: int = 2026) -> str:
    """A review card in the live data-testid markup."""
    return f"""
    <div data-testid="review-card">
      <div data-testid="review-avatar"><h4>Guest {review_id}</h4><span>Australia</span></div>
      <span data-testid="review-date">Reviewed: {day} {month} {year}</span>
      <h4 data-testid="review-title">Title {review_id}</h4>
      <div data-testid="review-score">Scored {rating}</div>
      <div data-testid="review-positive-text"><span>Review text {review_id}</span></div>
    </div>"""


def page(*cards: str) -> str:
    return f"<html><body><div data-testid='review-list'>{''.join(cards)}</div></body></html>"


class FakeSource:
    """Yields pre-baked outcomes; records how many the runner actually consumed."""

    name = "fake"

    def __init__(self, outcomes: list):
        self.outcomes = outcomes
        self.served = 0

    def pages(self, country_code, page_name):
        for outcome in self.outcomes:
            self.served += 1
            if isinstance(outcome, KeyboardInterrupt):
                raise outcome  # simulates Ctrl+C while the browser is working
            if isinstance(outcome, (Exception, FetchResult)):
                yield outcome
            else:
                yield FetchResult(f"fake://page-{self.served}", 200, outcome, self.name)

    def close(self):
        pass


class RunnerTests(TestCase):
    def setUp(self):
        self.prop = Property.objects.create(
            slug="p1", name="P1", booking_url="https://www.booking.com/hotel/au/p1.html"
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_scrape(self, source, mode=ScrapeRun.Mode.FULL, max_pages=50, since=None):
        config = RunConfig(mode=mode, max_pages=max_pages, stop_after_known_pages=2,
                           snapshot_dir=Path(self.tmp.name), since=since)
        return scrape_property(self.prop, source, config)

    def snapshots(self, pattern: str) -> list[Path]:
        return list(Path(self.tmp.name, "p1").glob(pattern))

    def test_full_run_reads_every_page(self):
        source = FakeSource([page(card("a"), card("b")), page(card("c"), card("d"))])
        run = self.run_scrape(source)
        self.assertEqual(run.status, ScrapeRun.Status.SUCCESS)
        self.assertEqual(source.served, 2)
        self.assertEqual(run.reviews_created, 4)
        self.assertEqual(run.fetcher, "fake")

    def test_empty_list_page_ends_run_cleanly(self):
        source = FakeSource([page(card("a")), "<html><ul class='review_list'></ul></html>", page(card("z"))])
        run = self.run_scrape(source)
        self.assertEqual(run.status, ScrapeRun.Status.SUCCESS)
        self.assertEqual(source.served, 2)

    def test_rerun_creates_no_duplicates(self):
        pages = [page(card("a"), card("b")), page(card("c"))]
        self.run_scrape(FakeSource(pages))
        run = self.run_scrape(FakeSource(pages))
        self.assertEqual(run.reviews_created, 0)
        self.assertEqual(Review.objects.count(), 3)

    def test_incremental_stops_after_known_pages(self):
        old = [page(card(f"old{i}a"), card(f"old{i}b")) for i in range(5)]
        self.run_scrape(FakeSource(old))

        newer = [page(card("new1"), card("old0a"))] + old[1:]  # one new review at the top
        source = FakeSource(newer)
        run = self.run_scrape(source, mode=ScrapeRun.Mode.INCREMENTAL)

        self.assertEqual(source.served, 3)  # page 1 had news, then 2 known pages -> stop
        self.assertEqual(run.reviews_created, 1)
        self.assertEqual(run.status, ScrapeRun.Status.SUCCESS)

    def test_blocked_outcome_stops_run_and_keeps_earlier_pages(self):
        source = FakeSource([page(card("a"), card("b")), BlockedError("HTTP 403"), page(card("c"))])
        run = self.run_scrape(source)
        self.assertEqual(run.status, ScrapeRun.Status.BLOCKED)
        self.assertEqual(source.served, 2)
        self.assertEqual(run.reviews_created, 2)

    def test_challenge_page_is_blocked_with_snapshot(self):
        html = (FIXTURES / "waf_challenge.html").read_text(encoding="utf-8")
        run = self.run_scrape(FakeSource([html]))
        self.assertEqual(run.status, ScrapeRun.Status.BLOCKED)
        self.assertTrue(self.snapshots("*blocked*.html"))

    def test_transient_failure_marks_partial(self):
        source = FakeSource([page(card("a"), card("b")), TransientFetchError("HTTP 503"),
                             page(card("e"), card("f"))])
        run = self.run_scrape(source)
        self.assertEqual(run.status, ScrapeRun.Status.PARTIAL)
        self.assertEqual(run.pages_failed, 1)
        self.assertEqual(run.reviews_created, 4)

    def test_consecutive_failures_fail_the_run(self):
        source = FakeSource([TransientFetchError("down")] * 10)
        run = self.run_scrape(source)
        self.assertEqual(run.status, ScrapeRun.Status.FAILED)
        self.assertEqual(source.served, 3)

    def test_permanent_error_on_first_page_fails_fast(self):
        source = FakeSource([PermanentFetchError("HTTP 404"), page(card("a"))])
        run = self.run_scrape(source)
        self.assertEqual(run.status, ScrapeRun.Status.FAILED)
        self.assertEqual(source.served, 1)

    def test_unrecognised_layout_saves_snapshot_and_fails(self):
        run = self.run_scrape(FakeSource(["<html><body><div class='new-layout'>x</div></body></html>"]))
        self.assertEqual(run.status, ScrapeRun.Status.FAILED)
        self.assertIn("layout may have changed", run.error_message)
        self.assertTrue(self.snapshots("*unrecognised*.html"))

    def test_panel_not_opening_fails_with_reason_not_false_success(self):
        property_page = "<html><body><h1>Hotel</h1><p>no reviews</p></body></html>"
        note = "clicked 'Read all reviews' but no review cards appeared"
        source = FakeSource([FetchResult("fake://hotel", 200, property_page, "fake", note=note)])
        run = self.run_scrape(source)
        self.assertEqual(run.status, ScrapeRun.Status.FAILED)
        self.assertIn(note, run.error_message)

    def test_history_window_stops_at_first_fully_old_page(self):
        source = FakeSource([
            page(card("a", day=20), card("b", day=10)),
            page(card("c", day=5), card("d", day=25, month="September")),  # straddles the cutoff
            page(card("e", day=1, month="September")),                      # entirely too old
            page(card("f", day=1, month="August")),
        ])
        run = self.run_scrape(source, since=date(2026, 10, 1))
        self.assertEqual(source.served, 3)
        self.assertEqual(run.reviews_created, 3)  # a, b, c; d is outside the window
        self.assertEqual(run.status, ScrapeRun.Status.SUCCESS)
        self.assertFalse(Review.objects.filter(review_date__lt=date(2026, 10, 1)).exists())

    def test_ctrl_c_is_recorded_as_failed_not_success(self):
        source = FakeSource([page(card("a"), card("b")), KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self.run_scrape(source)
        run = ScrapeRun.objects.latest("started_at")
        self.assertEqual(run.status, ScrapeRun.Status.FAILED)
        self.assertIn("interrupted", run.error_message)
        self.assertEqual(Review.objects.count(), 2)  # work done before Ctrl+C is kept

    def test_repeated_page_guard(self):
        same = page(card("a"), card("b"))
        source = FakeSource([same] * 10)
        run = self.run_scrape(source)
        self.assertEqual(source.served, 2)
        self.assertEqual(run.reviews_created, 2)

    def test_invalid_reviews_flag_drift(self):
        run = self.run_scrape(FakeSource([page(card("a", rating="n/a"), card("b", rating="n/a"))]))
        self.assertEqual(run.reviews_invalid, 2)
        self.assertEqual(run.status, ScrapeRun.Status.PARTIAL)
        self.assertIn("possible layout drift", run.error_message)
        self.assertTrue(self.snapshots("*drift*.html"))

    def test_max_pages_reached_is_reported(self):
        source = FakeSource([page(card("a")), page(card("b"))])
        run = self.run_scrape(source, max_pages=2)
        self.assertEqual(run.status, ScrapeRun.Status.PARTIAL)
        self.assertIn("max_pages", run.error_message)


class AutoModeTests(TestCase):
    def setUp(self):
        from reviews.management.commands.scrape_reviews import Command
        self.auto_mode = Command._auto_mode
        self.prop = Property.objects.create(slug="p1", name="P1",
                                            booking_url="https://www.booking.com/hotel/au/p1.html")

    def test_new_property_gets_full_backfill(self):
        self.assertEqual(self.auto_mode(self.prop), "full")

    def test_capped_first_run_does_not_count_as_backfilled(self):
        ScrapeRun.objects.create(property=self.prop, mode=ScrapeRun.Mode.FULL,
                                 status=ScrapeRun.Status.PARTIAL)
        self.assertEqual(self.auto_mode(self.prop), "full")

    def test_successful_backfill_switches_to_incremental(self):
        ScrapeRun.objects.create(property=self.prop, mode=ScrapeRun.Mode.FULL,
                                 status=ScrapeRun.Status.SUCCESS)
        self.assertEqual(self.auto_mode(self.prop), "incremental")


class ImportCommandTests(TestCase):
    def test_import_saved_html_is_idempotent(self):
        Property.objects.create(slug="p1", name="P1",
                                booking_url="https://www.booking.com/hotel/au/p1.html")
        path = str(FIXTURES / "review_cards_testid.html")
        call_command("import_review_html", path, property="p1", stdout=io.StringIO())
        call_command("import_review_html", path, property="p1", stdout=io.StringIO())
        self.assertEqual(Review.objects.count(), 4)
        self.assertEqual(ScrapeRun.objects.filter(fetcher="file").count(), 2)
