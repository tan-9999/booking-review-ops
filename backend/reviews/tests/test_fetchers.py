"""HttpFetcher retry/block behaviour using httpx's in-memory MockTransport (no network)."""
import unittest

import httpx

from reviews.scraper.errors import BlockedError, PermanentFetchError, TransientFetchError
from reviews.scraper.fetchers import HttpFetcher

URL = "https://www.booking.com/reviewlist.html?cc1=in&pagename=x&offset=0"


def make_fetcher(handler, max_retries=4, follow_redirects=True):
    sleeps: list[float] = []
    # Mirror the real HttpFetcher client, which follows redirects.
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=follow_redirects)
    fetcher = HttpFetcher(max_retries=max_retries, sleep=sleeps.append, client=client)
    return fetcher, sleeps


class HttpFetcherTests(unittest.TestCase):
    def test_retries_5xx_then_succeeds(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, text="<ul class='review_list'></ul>")

        fetcher, sleeps = make_fetcher(handler)
        result = fetcher.fetch(URL)
        self.assertEqual(result.status, 200)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(sleeps), 2)

    def test_honours_retry_after(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "30"})
            return httpx.Response(200, text="ok")

        fetcher, sleeps = make_fetcher(handler)
        fetcher.fetch(URL)
        self.assertGreaterEqual(sleeps[0], 30)

    def test_blocked_is_not_retried(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(403, text="denied")

        fetcher, sleeps = make_fetcher(handler)
        with self.assertRaises(BlockedError):
            fetcher.fetch(URL)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(sleeps, [])

    def test_waf_202_challenge_is_blocked(self):
        fetcher, _ = make_fetcher(lambda request: httpx.Response(202, text=""))
        with self.assertRaises(BlockedError):
            fetcher.fetch(URL)

    def test_404_is_permanent(self):
        fetcher, sleeps = make_fetcher(lambda request: httpx.Response(404))
        with self.assertRaises(PermanentFetchError):
            fetcher.fetch(URL)
        self.assertEqual(sleeps, [])

    def test_timeouts_exhaust_retries(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        fetcher, sleeps = make_fetcher(handler, max_retries=3)
        with self.assertRaises(TransientFetchError):
            fetcher.fetch(URL)
        self.assertEqual(len(sleeps), 2)

    def test_redirect_away_from_review_list_is_permanent(self):
        def handler(request):
            if request.url.path == "/reviewlist.html":
                return httpx.Response(302, headers={"Location": "https://www.booking.com/index.html"})
            return httpx.Response(200, text="home page")

        fetcher, _ = make_fetcher(handler)
        with self.assertRaises(PermanentFetchError):
            fetcher.fetch(URL)

    def test_unfollowed_redirect_is_permanent(self):
        def handler(request):
            return httpx.Response(302, headers={"Location": "https://www.booking.com/index.html"})

        fetcher, _ = make_fetcher(handler, follow_redirects=False)
        with self.assertRaises(PermanentFetchError):
            fetcher.fetch(URL)
