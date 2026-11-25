"""
Orchestrates one scrape of one property:

  for each page the source yields (newest first):
      error   -> blocked?           stop, status BLOCKED (never hammer)
                 transient/permanent? count it; 3 in a row -> stop
      parse   -> no cards?           end of list / blocked / layout changed (snapshot)
      ingest  -> dedup + validation (services.ingest)
      lookback: stop once a whole page is older than the history window
      incremental mode: stop after N consecutive pages with nothing new

Every run is recorded in ScrapeRun so ops can see freshness and failures.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from reviews.models import Property, ScrapeRun
from reviews.services.ingest import ingest_reviews

from .blocking import detect_block
from .errors import BlockedError, FetchError, PermanentFetchError
from .parsing import parse_review_page
from .snapshots import save_snapshot
from .sources import PageSource

logger = logging.getLogger(__name__)

DRIFT_INVALID_RATIO = 0.5  # >50% of a page failing validation smells like a layout change

_COUNTER_FIELDS = [
    "pages_fetched", "pages_failed", "reviews_seen",
    "reviews_created", "reviews_updated", "reviews_invalid",
]


@dataclass
class RunConfig:
    mode: str = ScrapeRun.Mode.INCREMENTAL
    max_pages: int = 200  # the source enforces it; the runner reports when it was hit
    stop_after_known_pages: int = 2
    max_consecutive_failures: int = 3
    snapshot_dir: Path | None = None
    since: date | None = None  # history window: reviews older than this are not collected

    @classmethod
    def from_settings(cls, mode: str, **overrides) -> "RunConfig":
        s = settings.SCRAPER
        config = cls(
            mode=mode,
            max_pages=s["MAX_PAGES_PER_RUN"],
            stop_after_known_pages=s["INCREMENTAL_STOP_AFTER_KNOWN_PAGES"],
            snapshot_dir=Path(s["SNAPSHOT_DIR"]),
            since=history_start(s["HISTORY_DAYS"]),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(config, key, value)
        return config


def history_start(days: int | None) -> date | None:
    """0 / None = keep everything Booking lists."""
    return timezone.localdate() - timedelta(days=days) if days else None


def _final_status(run: ScrapeRun, errors: list[str]) -> str:
    if run.pages_fetched == 0 and run.pages_failed:
        return ScrapeRun.Status.FAILED
    if run.reviews_seen == 0 and run.pages_failed:
        return ScrapeRun.Status.FAILED
    if run.pages_failed or errors:
        return ScrapeRun.Status.PARTIAL
    return ScrapeRun.Status.SUCCESS


def scrape_property(prop: Property, source: PageSource, config: RunConfig) -> ScrapeRun:
    run = ScrapeRun.objects.create(property=prop, mode=config.mode, fetcher=source.name)
    errors: list[str] = []
    seen_keys: set[str] = set()
    known_streak = 0
    consecutive_failures = 0

    def snapshot(label: str, html: str) -> str:
        if not config.snapshot_dir:
            return "-"
        try:
            return str(save_snapshot(config.snapshot_dir, prop.slug, label, html))
        except OSError as exc:
            logger.warning("Could not save snapshot: %s", exc)
            return "-"

    page_index = -1
    try:
        for page_index, outcome in enumerate(source.pages(prop.country_code, prop.page_name)):
            where = f"page {page_index + 1}"

            # ---- fetch outcome
            if isinstance(outcome, BlockedError):
                errors.append(f"{where}: blocked ({outcome})")
                run.status = ScrapeRun.Status.BLOCKED
                break
            if isinstance(outcome, FetchError):
                run.pages_failed += 1
                consecutive_failures += 1
                errors.append(f"{where}: {outcome}")
                if isinstance(outcome, PermanentFetchError) and page_index == 0:
                    break
                if consecutive_failures >= config.max_consecutive_failures:
                    errors.append(f"stopped after {consecutive_failures} consecutive failures")
                    break
                continue
            consecutive_failures = 0
            run.pages_fetched += 1
            result = outcome

            # ---- parse
            page = parse_review_page(result.html, result.url)
            if page.cards_found == 0:
                reason = detect_block(result.status, result.html)
                if reason:
                    path = snapshot(f"blocked_p{page_index + 1}", result.html)
                    errors.append(f"{where}: blocked ({reason}); snapshot={path}")
                    run.status = ScrapeRun.Status.BLOCKED
                    break
                if page.looks_empty:
                    break  # natural end of the list
                path = snapshot(f"unrecognised_p{page_index + 1}", result.html)
                run.pages_failed += 1
                detail = result.note or "no review cards found; page layout may have changed"
                errors.append(f"{where}: {detail}. snapshot={path}")
                break

            # Guard against pagination that keeps returning the same page.
            keys = {r.external_id or r.fingerprint(prop.slug) for r in page.reviews}
            if keys and keys <= seen_keys:
                logger.info("%s: %s repeats an earlier page; stopping", prop.slug, where)
                break
            seen_keys |= keys

            # ---- history window (pages are newest first)
            reviews = page.reviews
            if config.since:
                reviews = [r for r in reviews if r.review_date is None or r.review_date >= config.since]
                if not reviews:
                    logger.info("%s %s: older than %s; history window complete", prop.slug, where, config.since)
                    break

            # ---- ingest
            stats = ingest_reviews(prop, reviews)
            invalid = stats.invalid + len(page.card_errors)
            run.reviews_seen += len(reviews) + len(page.card_errors)
            run.reviews_created += stats.created
            run.reviews_updated += stats.updated
            run.reviews_invalid += invalid
            for detail in (page.card_errors + stats.invalid_samples)[:5]:
                errors.append(f"{where}: invalid review ({detail})")
            if invalid / max(1, len(reviews) + len(page.card_errors)) > DRIFT_INVALID_RATIO:
                path = snapshot(f"drift_p{page_index + 1}", result.html)
                errors.append(f"{where}: {invalid}/{page.cards_found} reviews failed validation; "
                              f"possible layout drift. snapshot={path}")
            run.save(update_fields=_COUNTER_FIELDS)
            logger.info("%s %s: %s cards, %s new, %s updated (%s)",
                        prop.slug, where, page.cards_found, stats.created, stats.updated, page.strategy)

            # ---- incremental stop
            if config.mode == ScrapeRun.Mode.INCREMENTAL:
                known_streak = known_streak + 1 if stats.new_or_changed == 0 else 0
                if known_streak >= config.stop_after_known_pages:
                    break
        else:
            if page_index + 1 >= config.max_pages:
                errors.append(f"reached max_pages={config.max_pages}; older reviews may be missing")

    except KeyboardInterrupt:
        # Ctrl+C mid-backfill must never be recorded as a completed run,
        # otherwise "auto" mode would treat the property as fully collected.
        errors.append("interrupted by user before the run finished")
        run.status = ScrapeRun.Status.FAILED
        raise
    except Exception as exc:  # a bug must still leave a FAILED run record behind
        logger.exception("Unexpected error scraping %s", prop.slug)
        errors.append(f"unexpected error: {type(exc).__name__}: {exc}")
        run.status = ScrapeRun.Status.FAILED
    finally:
        if run.status == ScrapeRun.Status.RUNNING:
            run.status = _final_status(run, errors)
        run.error_message = "\n".join(errors[:100])
        run.finished_at = timezone.now()
        run.save()
    return run
