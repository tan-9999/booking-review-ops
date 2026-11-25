"""
Where review pages come from. The runner only consumes an iterator of
`FetchResult | FetchError`, so the collection method is swappable.

BrowserReviewSource (default): does what a person does. It opens the public
  property page, clicks "Read all reviews", picks "Newest first", reads each
  page, then clicks Next. Since 2026 Booking loads reviews through an internal
  GraphQL API bound to the browser session, so this is the dependable public
  route. It is a normal browser: no stealth plugins and no CAPTCHA solving.
  If a challenge is still showing, the runner marks the run BLOCKED.

HttpReviewListSource (legacy, opt-in): the old server-rendered
  /reviewlist.html. Cheap and simple, but it returned 404 when probed on
  2026-11-25. Kept in case Booking serves it again.
"""
import logging
import queue
import random
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Protocol, Union

from .errors import BlockedError, FetchError, PermanentFetchError, TransientFetchError
from .fetchers import RETRYABLE_STATUS, FetchResult, HttpFetcher, backoff_delay
from .parsing import parse_review_date
from .urls import build_reviewlist_url, hotel_page_url

logger = logging.getLogger(__name__)

PageOutcome = Union[FetchResult, FetchError]


class PageSource(Protocol):
    name: str

    def pages(self, country_code: str, page_name: str) -> Iterator[PageOutcome]: ...

    def close(self) -> None: ...


def polite_pause(sleep: Callable[[float], None], delay: float) -> None:
    """Fixed delay plus jitter between page loads, so we never burst requests."""
    if delay > 0:
        sleep(delay + random.uniform(0, delay / 2))


# --------------------------------------------------------------------------- legacy HTTP

class HttpReviewListSource:
    name = "http"

    def __init__(self, fetcher: HttpFetcher, *, page_size: int = 25, max_pages: int = 200,
                 delay_seconds: float = 3.0, sleep: Callable[[float], None] = time.sleep):
        self.fetcher = fetcher
        self.page_size = page_size
        self.max_pages = max_pages
        self.delay_seconds = delay_seconds
        self._sleep = sleep

    def pages(self, country_code: str, page_name: str) -> Iterator[PageOutcome]:
        for index in range(self.max_pages):
            if index:
                polite_pause(self._sleep, self.delay_seconds)
            url = build_reviewlist_url(
                country_code, page_name, offset=index * self.page_size, rows=self.page_size
            )
            try:
                yield self.fetcher.fetch(url)
            except FetchError as exc:
                yield exc  # offset-based paging can skip a failed page and carry on

    def close(self) -> None:
        self.fetcher.close()


# --------------------------------------------------------------------------- browser

CARD = '[data-testid="review-card"]'
READ_ALL_BUTTONS = ('[data-testid="fr-read-all-reviews"]', '[data-testid="review-score-read-all-actionable"]')
READ_ALL = ", ".join(READ_ALL_BUTTONS)
PANEL_WAIT_MS = 10_000
EMPTY_STATE_TEXT = "no reviews that match your filters"
SHOW_ALL_TEXT = "Show all reviews"
SORTER = '[data-testid="reviews-sorter-component"]'
NEXT_PAGE = 'button[aria-label="Next page"]'
REPLY_TOGGLE = '[data-testid="review-pr-toggle"]'
COOKIE_REJECT = "#onetrust-reject-all-handler"
NEWEST_FIRST = "NEWEST_FIRST"

# Whole-list signature. Comparing only the first card failed in practice: the
# newest review can also be the top "most relevant" one, so sorting changed
# the list but not its first card.
_LIST_SIGNATURE = """() => [...document.querySelectorAll('[data-testid="review-card"]')]
  .map(c => c.textContent).join('\\u0001')"""
_LIST_CHANGED = """prev => {
  const s = [...document.querySelectorAll('[data-testid="review-card"]')].map(c => c.textContent).join('\\u0001');
  return s.length > 0 && s !== prev;
}"""
_REVIEW_DATES = """() => [...document.querySelectorAll('[data-testid="review-date"]')].map(e => e.textContent)"""
_EXPAND_REPLIES = """() => {
  let clicked = 0;
  document.querySelectorAll('[data-testid="review-pr-toggle"]').forEach(b => {
    if (/continue|read more/i.test(b.textContent)) { b.click(); clicked++; }
  });
  return clicked;
}"""
_NO_COLLAPSED_REPLIES = """() => ![...document.querySelectorAll('[data-testid="review-pr-toggle"]')]
  .some(b => /continue|read more/i.test(b.textContent))"""
SORT_WAIT_MS = 10_000
# Capture only the review cards: a full Booking page is ~1.5 MB of markup.
_CAPTURE_CARDS = """() => {
  const cards = [...document.querySelectorAll('[data-testid="review-card"]')];
  if (!cards.length) return null;
  return '<html><body><div data-testid="review-list">' + cards.map(c => c.outerHTML).join('') + '</div></body></html>';
}"""


_DONE = object()  # end-of-stream marker passed from the worker thread


class BrowserReviewSource:
    """
    Threading model: Playwright's sync API runs an asyncio event loop on the
    thread that starts it, and Django refuses ORM calls on a thread with a
    running loop (SynchronousOnlyOperation). So every Playwright call lives on
    one dedicated worker thread, and the runner (which does the DB work) stays
    on the main thread. They talk through queues:

      main thread                      worker thread ("playwright")
      pages(): request (cc, page) ---> opens panel
               demand.release()  --->  produce ONE page (only when asked)
               outcomes.get()    <---  outcome
               ... runner stops? stop.set() + release -> worker ends the stream

    Pages are produced on demand, so stopping early (incremental mode, block)
    never loads an extra page.
    """

    name = "browser"

    def __init__(self, *, timeout: float = 30.0, max_pages: int = 200, delay_seconds: float = 3.0,
                 max_retries: int = 3, backoff_base: float = 2.0, headless: bool = False,
                 screenshot_dir: Path | None = None, sleep: Callable[[float], None] = time.sleep):
        self._Error = self._load_playwright_error()
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self.timeout_ms = int(timeout * 1000)
        self.max_pages = max_pages
        self.delay_seconds = delay_seconds
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self.headless = headless
        self._sleep = sleep

        self._requests: queue.Queue = queue.Queue()
        self._outcomes: queue.Queue = queue.Queue(maxsize=1)
        self._demand = threading.Semaphore(0)
        self._stop = threading.Event()
        self._ready: queue.Queue = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._worker, name="playwright", daemon=True)
        self._thread.start()

        startup_error = self._ready.get()
        if startup_error:
            self._thread.join(timeout=10)
            raise RuntimeError(startup_error)

    # ------------------------------------------------------------------ main-thread API

    def pages(self, country_code: str, page_name: str) -> Iterator[PageOutcome]:
        self._stop.clear()
        self._requests.put((country_code, page_name))
        finished = False
        try:
            while True:
                self._demand.release()  # ask the worker for exactly one more page
                outcome = self._outcomes.get()
                if outcome is _DONE:
                    finished = True
                    return
                yield outcome
        finally:
            if not finished:  # runner stopped early: end the worker's stream and drain it
                self._stop.set()
                self._demand.release()
                while self._outcomes.get() is not _DONE:
                    pass

    def close(self) -> None:
        if self._thread.is_alive():
            self._stop.set()        # end any stream still in progress (e.g. after Ctrl+C)
            self._demand.release()
            self._requests.put(None)
            self._thread.join(timeout=30)

    # ------------------------------------------------------------------ worker thread

    @staticmethod
    def _load_playwright_error():
        try:
            from playwright.sync_api import Error as PlaywrightError
        except ImportError as exc:
            raise RuntimeError(
                f"The browser source needs Playwright ({type(exc).__name__}: {exc}).\n"
                "From the project root (where requirements.txt is):\n"
                "  pip install -r requirements.txt\n"
                "  python -m playwright install chromium"
            ) from exc
        return PlaywrightError

    def _worker(self) -> None:
        try:
            self._start_browser()
        except Exception as exc:
            self._ready.put(str(exc) or type(exc).__name__)
            return
        self._ready.put(None)
        try:
            while True:
                request = self._requests.get()
                if request is None:
                    return
                self._stream(*request)
        finally:
            self._stop_browser()

    def _stream(self, country_code: str, page_name: str) -> None:
        stream = self._iter_pages(country_code, page_name)
        try:
            while True:
                self._demand.acquire()
                if self._stop.is_set():
                    break
                try:
                    outcome = next(stream)
                except StopIteration:
                    break
                except Exception as exc:  # unexpected bug in browser code: report, end stream
                    self._outcomes.put(TransientFetchError(f"browser worker error: {type(exc).__name__}: {exc}"))
                    break
                self._outcomes.put(outcome)
        finally:
            stream.close()
            self._outcomes.put(_DONE)

    def _start_browser(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
        except self._Error as exc:
            self._pw.stop()
            raise RuntimeError(
                "Could not start Chromium. Download it once with:\n"
                "  python -m playwright install chromium\n"
                f"(details: {str(exc).splitlines()[0]})"
            ) from exc
        self._context = self._browser.new_context(locale="en-GB", viewport={"width": 1366, "height": 900})
        self._page = self._context.new_page()

    def _stop_browser(self) -> None:
        for closer in (self._context.close, self._browser.close, self._pw.stop):
            try:
                closer()
            except Exception:  # best-effort shutdown
                pass

    def _iter_pages(self, country_code: str, page_name: str) -> Iterator[PageOutcome]:
        """Runs on the worker thread. Same flow as a person: open, sort, read, Next."""
        url = hotel_page_url(country_code, page_name)
        try:
            diagnostic = self._open_review_panel(url)
        except FetchError as exc:
            yield exc
            return
        except self._Error as exc:
            yield TransientFetchError(f"could not open the reviews panel: {exc}")
            return
        if diagnostic is not None:
            # Panel didn't open: hand the page to the runner, which decides
            # between "blocked" and "layout changed" and saves a snapshot.
            yield diagnostic
            return

        for index in range(self.max_pages):
            try:
                html = self._capture_page()
            except self._Error as exc:
                yield TransientFetchError(f"could not read reviews page {index + 1}: {exc}")
                return
            yield FetchResult(f"{url}#reviews-page-{index + 1}", 200, html, self.name)
            try:
                if not self._has_next_page():
                    return
                polite_pause(self._sleep, self.delay_seconds)
                self._go_to_next_page()
            except self._Error as exc:
                # "Next" depends on the current page, so a failure ends this run
                # (PARTIAL). The next run catches up via incremental mode.
                yield TransientFetchError(f"browser error after reviews page {index + 1}: {exc}")
                return

    # ------------------------------------------------------------------ steps

    def _open_review_panel(self, url: str) -> FetchResult | None:
        response = self._goto_with_retries(url)
        status = response.status if response is not None else 0
        if status in (403, 405):
            raise BlockedError(f"HTTP {status} on property page")
        if status >= 400:
            raise PermanentFetchError(f"HTTP {status} for {url}")

        try:
            # A JavaScript challenge (HTTP 202) reloads the page by itself in a
            # normal browser; this wait covers that. A CAPTCHA would not clear.
            self._page.wait_for_selector(READ_ALL, timeout=self.timeout_ms)
        except self._Error:
            return self._diagnostic(status, "'Read all reviews' button never appeared")

        self._decline_cookies()
        try:
            # The button exists in the HTML before its click handler is attached
            # (hydration). Wait for the page to finish loading; then retry.
            self._page.wait_for_load_state("load", timeout=self.timeout_ms)
        except self._Error:
            pass  # slow third-party assets; the click retries below still apply

        outcome = self._click_until_cards_appear()
        if outcome == "data-not-loaded":
            # Observed 2026-11-25 in headless Chromium: the panel opens but Booking's
            # review API returns nothing ("no reviews match your filters" on a
            # property with 129 reviews). That is access being limited, not a
            # layout change, so report BLOCKED. We don't try to disguise the browser.
            raise BlockedError(
                "reviews panel opened but its review data did not load "
                "('no reviews match your filters'). Booking appears to limit this "
                "browser's access to review data; retry later, and don't use --headless"
                + self._screenshot_suffix()
            )
        if outcome == "not-opened":
            return self._diagnostic(status, "clicked 'Read all reviews' but no review cards appeared")

        self._sort_newest_first()
        return None

    def _click_until_cards_appear(self, rounds: int = 3) -> str:
        """Returns 'ok', 'data-not-loaded' (panel shell but empty) or 'not-opened'."""
        panel_ready = f'{CARD}, :text("{EMPTY_STATE_TEXT}")'
        for round_no in range(rounds):
            for selector in READ_ALL_BUTTONS:
                button = self._page.locator(selector).first
                try:
                    if button.count() == 0:
                        continue
                    button.scroll_into_view_if_needed(timeout=5000)
                    button.click(timeout=5000)
                    self._page.wait_for_selector(panel_ready, timeout=PANEL_WAIT_MS)
                except self._Error:
                    logger.info("Reviews panel not open yet (round %s, %s); retrying", round_no + 1, selector)
                    continue
                if self._page.locator(CARD).count():
                    return "ok"
                return self._retry_empty_panel()
            self._page.wait_for_timeout(2000)
        return "not-opened"

    def _retry_empty_panel(self) -> str:
        """Panel opened empty: press its own 'Show all reviews' once, as a person would."""
        try:
            self._page.get_by_text(SHOW_ALL_TEXT, exact=True).first.click(timeout=5000)
            self._page.wait_for_selector(CARD, timeout=PANEL_WAIT_MS)
            return "ok"
        except self._Error:
            return "data-not-loaded"

    def _screenshot_suffix(self) -> str:
        if not self.screenshot_dir:
            return ""
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%S")
            path = self.screenshot_dir / f"{stamp}_{self._page.url.split('/')[-1].split('.')[0]}.png"
            self._page.screenshot(path=str(path), full_page=False)
            return f"; screenshot={path}"
        except (self._Error, OSError) as exc:
            logger.warning("Could not save screenshot: %s", exc)
            return ""

    def _diagnostic(self, status: int, note: str) -> FetchResult:
        """Return the page for the runner to classify, plus a screenshot for humans."""
        note = note + self._screenshot_suffix()
        return FetchResult(self._page.url, status, self._page.content(), self.name, note=note)

    def _goto_with_retries(self, url: str):
        last_error: FetchError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                if response is not None and response.status in RETRYABLE_STATUS:
                    last_error = TransientFetchError(f"HTTP {response.status} on property page")
                else:
                    return response
            except self._Error as exc:
                last_error = TransientFetchError(f"navigation failed: {exc}")
            if attempt < self.max_retries:
                self._sleep(backoff_delay(attempt, self.backoff_base))
        assert last_error is not None
        raise last_error

    def _decline_cookies(self) -> None:
        try:
            self._page.locator(COOKIE_REJECT).click(timeout=3000)
        except self._Error:
            pass  # banner not shown (or already answered)

    def _sort_newest_first(self) -> None:
        sorter = self._page.locator(SORTER)
        if sorter.count() == 0:
            # Incremental mode relies on newest-first order; fail loudly rather
            # than silently stop early on a "most relevant" ordering.
            raise PermanentFetchError("'Newest first' sort control not found; layout may have changed")
        if sorter.input_value() == NEWEST_FIRST:
            return
        before = self._page.evaluate(_LIST_SIGNATURE)
        sorter.select_option(NEWEST_FIRST)
        try:
            self._page.wait_for_function(_LIST_CHANGED, arg=before, timeout=SORT_WAIT_MS)
        except self._Error:
            # The list may legitimately look the same (e.g. relevance order happens
            # to be date order). Accept only if the control and the dates agree.
            if sorter.input_value() != NEWEST_FIRST or not self._dates_descending():
                raise PermanentFetchError("could not confirm 'Newest first' ordering of reviews")

    def _dates_descending(self) -> bool:
        dates = [d for d in (parse_review_date(t) for t in self._page.evaluate(_REVIEW_DATES)) if d]
        return bool(dates) and all(a >= b for a, b in zip(dates, dates[1:]))

    def _capture_page(self) -> str:
        # Expand "Continue reading" hotel replies via the page's own buttons, then
        # wait until none are left collapsed. Any reply still collapsed is flagged
        # as truncated by the parser, so it never overwrites a full stored reply.
        for _ in range(2):
            if not self._page.evaluate(_EXPAND_REPLIES):
                break
            try:
                self._page.wait_for_function(_NO_COLLAPSED_REPLIES, timeout=5000)
                break
            except self._Error:
                continue
        return self._page.evaluate(_CAPTURE_CARDS) or self._page.content()

    def _has_next_page(self) -> bool:
        button = self._page.locator(NEXT_PAGE)
        return button.count() > 0 and button.first.is_enabled()

    def _go_to_next_page(self) -> None:
        before = self._page.evaluate(_LIST_SIGNATURE)
        self._page.locator(NEXT_PAGE).first.click()
        self._page.wait_for_function(_LIST_CHANGED, arg=before, timeout=self.timeout_ms)
