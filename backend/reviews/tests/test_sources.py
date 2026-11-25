"""
Page sources:
  * legacy HTTP paging
  * the browser source's threading contract, tested with a fake "browser"
    (the real Playwright steps are exercised by a live run; see README)
"""
import threading
import unittest
from urllib.parse import parse_qs, urlparse

from reviews.scraper.errors import TransientFetchError
from reviews.scraper.fetchers import FetchResult
from reviews.scraper.sources import BrowserReviewSource, HttpReviewListSource


class StubFetcher:
    def __init__(self, fail_offsets=()):
        self.fail_offsets = set(fail_offsets)
        self.offsets: list[int] = []

    def fetch(self, url):
        offset = int(parse_qs(urlparse(url).query)["offset"][0])
        self.offsets.append(offset)
        if offset in self.fail_offsets:
            raise TransientFetchError("HTTP 503")
        return FetchResult(url, 200, "<html></html>", "http")

    def close(self):
        pass


class HttpReviewListSourceTests(unittest.TestCase):
    def test_pages_by_offset_and_pauses_between_pages(self):
        sleeps: list[float] = []
        fetcher = StubFetcher()
        source = HttpReviewListSource(fetcher, page_size=25, max_pages=3, delay_seconds=2, sleep=sleeps.append)
        outcomes = list(source.pages("au", "x"))
        self.assertEqual(fetcher.offsets, [0, 25, 50])
        self.assertEqual(len(outcomes), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(2 <= s <= 3 for s in sleeps))  # delay + up to 50% jitter

    def test_failed_page_is_yielded_and_paging_continues(self):
        fetcher = StubFetcher(fail_offsets={25})
        source = HttpReviewListSource(fetcher, page_size=25, max_pages=3, delay_seconds=0)
        outcomes = list(source.pages("au", "x"))
        self.assertIsInstance(outcomes[1], TransientFetchError)
        self.assertIsInstance(outcomes[2], FetchResult)


class FakeBrowserSource(BrowserReviewSource):
    """Real threading/queue machinery, fake browser steps."""

    def __init__(self, pages_per_property=5, start_error=None, explode_at=None):
        self.pages_per_property = pages_per_property
        self.start_error = start_error
        self.explode_at = explode_at
        self.produced: list[str] = []
        self.worker_threads: set[int] = set()
        self.stopped = False
        super().__init__(delay_seconds=0)

    @staticmethod
    def _load_playwright_error():
        return RuntimeError  # stands in for playwright's Error class

    def _start_browser(self):
        self.worker_threads.add(threading.get_ident())
        if self.start_error:
            raise RuntimeError(self.start_error)

    def _stop_browser(self):
        self.stopped = True

    def _iter_pages(self, country_code, page_name):
        for i in range(self.pages_per_property):
            self.worker_threads.add(threading.get_ident())
            if self.explode_at == i:
                raise ValueError("boom")
            self.produced.append(f"{page_name}-{i}")
            yield FetchResult(f"fake://{page_name}/{i}", 200, f"<html>{i}</html>", self.name)


class BrowserSourceThreadingTests(unittest.TestCase):
    def test_browser_work_runs_off_the_main_thread(self):
        source = FakeBrowserSource(pages_per_property=3)
        try:
            outcomes = list(source.pages("au", "x"))
        finally:
            source.close()
        self.assertEqual(len(outcomes), 3)
        self.assertNotIn(threading.get_ident(), source.worker_threads)
        self.assertTrue(source.stopped)

    def test_pages_are_produced_on_demand_only(self):
        source = FakeBrowserSource(pages_per_property=10)
        try:
            for i, _ in enumerate(source.pages("au", "x")):
                if i == 1:
                    break  # runner stops after 2 pages (e.g. incremental stop)
        finally:
            source.close()
        self.assertEqual(source.produced, ["x-0", "x-1"])  # no wasted third page load

    def test_source_is_reusable_across_properties_after_early_stop(self):
        source = FakeBrowserSource(pages_per_property=4)
        try:
            first = source.pages("au", "a")
            next(first)
            first.close()  # stop early
            second = list(source.pages("au", "b"))
        finally:
            source.close()
        self.assertEqual(len(second), 4)
        self.assertEqual(source.produced, ["a-0", "b-0", "b-1", "b-2", "b-3"])

    def test_startup_failure_raises_on_main_thread(self):
        with self.assertRaises(RuntimeError) as ctx:
            FakeBrowserSource(start_error="Could not start Chromium")
        self.assertIn("Could not start Chromium", str(ctx.exception))

    def test_unexpected_worker_error_becomes_transient_outcome(self):
        source = FakeBrowserSource(pages_per_property=5, explode_at=1)
        try:
            outcomes = list(source.pages("au", "x"))
        finally:
            source.close()
        self.assertIsInstance(outcomes[0], FetchResult)
        self.assertIsInstance(outcomes[1], TransientFetchError)
        self.assertIn("boom", str(outcomes[1]))
        self.assertEqual(len(outcomes), 2)
