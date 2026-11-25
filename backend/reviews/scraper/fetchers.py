"""
Plain HTTPS fetching with retries: exponential backoff + jitter, honours
Retry-After, never retries a block. Used by the legacy HTTP source; the browser
source (sources.py) drives a real browser instead.
"""
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urlparse

import httpx

from .blocking import detect_block
from .errors import BlockedError, PermanentFetchError, TransientFetchError

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    status: int
    html: str
    fetcher: str
    note: str = ""  # why a source handed back a non-review page (diagnostics)


class Fetcher(Protocol):
    name: str

    def fetch(self, url: str) -> FetchResult: ...

    def close(self) -> None: ...


def backoff_delay(attempt: int, base: float, cap: float = 60.0, retry_after: float | None = None) -> float:
    delay = min(cap, base ** attempt) + random.uniform(0, 1)
    if retry_after:
        delay = max(delay, min(retry_after, cap * 2))
    return delay


def _retry_after_seconds(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None


class HttpFetcher:
    name = "http"

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        client: httpx.Client | None = None,
    ):
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self._sleep = sleep
        self._client = client or httpx.Client(
            timeout=timeout, follow_redirects=True, headers=DEFAULT_HEADERS
        )

    def fetch(self, url: str) -> FetchResult:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            retry_after = None
            try:
                response = self._client.get(url)
            except httpx.TimeoutException as exc:
                last_error = TransientFetchError(f"timeout: {exc}")
            except httpx.TransportError as exc:
                last_error = TransientFetchError(f"network error: {exc}")
            else:
                status = response.status_code
                if status in RETRYABLE_STATUS:
                    retry_after = _retry_after_seconds(response)
                    last_error = TransientFetchError(f"HTTP {status}")
                else:
                    if status in (202, 403, 405):
                        reason = detect_block(status, response.text)
                        if reason:
                            raise BlockedError(reason)
                    if status >= 400:
                        raise PermanentFetchError(f"HTTP {status} for {url}")
                    if 300 <= status < 400:
                        # Only reachable if the client isn't following redirects; never treat it as content.
                        location = response.headers.get("Location", "?")
                        raise PermanentFetchError(f"unexpected redirect (HTTP {status}) to {location}")
                    final_url = str(response.url)
                    if urlparse(final_url).path != urlparse(url).path:
                        raise PermanentFetchError(f"redirected away from review list to {final_url}")
                    return FetchResult(final_url, status, response.text, self.name)

            if attempt < self.max_retries:
                delay = backoff_delay(attempt, self.backoff_base, retry_after=retry_after)
                logger.warning("Attempt %s/%s failed (%s); retrying in %.1fs",
                               attempt, self.max_retries, last_error, delay)
                self._sleep(delay)

        assert last_error is not None
        raise last_error

    def close(self) -> None:
        self._client.close()
