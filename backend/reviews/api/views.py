"""
Read-only JSON API for the operations dashboard.

Conventions
  * Filters: ?property=slug[,slug]&start=YYYY-MM-DD&end=YYYY-MM-DD (validated, 400 on bad input)
  * Ratings are Booking's 1–10 scale, rounded to 2 decimals; shares are percentages
  * Every aggregate carries its review_count and a low_sample flag, so the UI
    can grey out numbers that rest on too few reviews
"""
from dataclasses import asdict
from datetime import date, timedelta

from django.db.models import Avg, Count, Max, Q
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from reviews.insights.topics import TOPIC_LABELS
from reviews.models import Property, Review, ReviewTopic, ScrapeRun
from reviews.services.insights import (
    MIN_REVIEWS,
    Period,
    generate_insights,
    topic_stats,
    week_comparison,
)

from .filters import feed, parse_filters, scope
from .serializers import ReviewSerializer

STALE_AFTER = timedelta(hours=48)   # no successful scrape for this long -> "stale"
DEFAULT_TREND_WEEKS = 26


def today() -> date:
    """Single source of 'today' (patched in tests)."""
    return timezone.localdate()


# ------------------------------------------------------------------ helpers

def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _rating(value) -> float | None:
    return round(float(value), 2) if value is not None else None


def period_stats(qs) -> dict:
    agg = qs.aggregate(
        avg=Avg("rating"),
        n=Count("id"),
        pos=Count("id", filter=Q(sentiment=Review.Sentiment.POSITIVE)),
        neg=Count("id", filter=Q(sentiment=Review.Sentiment.NEGATIVE)),
    )
    n = agg["n"]
    return {
        "average_rating": _rating(agg["avg"]),
        "review_count": n,
        "positive_share": _pct(agg["pos"], n),
        "negative_share": _pct(agg["neg"], n),
        "low_sample": n < MIN_REVIEWS,
    }


def _compare(current: dict, previous: dict) -> dict:
    change = None
    if current["average_rating"] is not None and previous["average_rating"] is not None:
        change = round(current["average_rating"] - previous["average_rating"], 2)
    return {"current": current, "previous": previous, "rating_change": change}


def _period_json(p: Period) -> dict:
    return {"start": p.start, "end": p.end, "label": p.label}


def _in(qs, p: Period):
    return qs.filter(review_date__range=(p.start, p.end))


class PeriodParams(serializers.Serializer):
    weeks_ago = serializers.IntegerField(required=False, default=0, min_value=0, max_value=104)
    granularity = serializers.ChoiceField(choices=["week", "month"], required=False, default="week")


def _period_params(request) -> dict:
    s = PeriodParams(data=request.query_params)
    s.is_valid(raise_exception=True)
    return s.validated_data


def _date_range(filters: dict) -> tuple[date, date]:
    end = filters.get("end") or today()
    start = filters.get("start") or end - timedelta(weeks=DEFAULT_TREND_WEEKS)
    return start, end


def _buckets(start: date, end: date, granularity: str) -> list[date]:
    """Every week/month start in range, so charts show gaps as zero instead of joining points."""
    if granularity == "month":
        current, out = start.replace(day=1), []
        while current <= end:
            out.append(current)
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        return out
    current, out = start - timedelta(days=start.weekday()), []
    while current <= end:
        out.append(current)
        current += timedelta(days=7)
    return out


def _bucket_key(value) -> date:
    return value.date() if hasattr(value, "date") and callable(value.date) else value


# ------------------------------------------------------------------ endpoints

@api_view(["GET"])
def meta(request):
    return Response({
        "topics": [{"key": k, "label": v} for k, v in TOPIC_LABELS.items()],
        "sentiments": [{"key": v, "label": label} for v, label in Review.Sentiment.choices],
        "min_reviews_for_percentages": MIN_REVIEWS,
        "definitions": {
            "negative_review": "Booking score below 6.",
            "mixed_review": "Booking score from 6 to 7.9.",
            "positive_review": "Booking score of 8 or more.",
            "complaint": "Topic found in the guest's 'disliked' text.",
            "praise": "Topic found in the guest's 'liked' text.",
            "this_week": "Monday to today, compared with the same weekdays last week.",
        },
    })


@api_view(["GET"])
def properties(request):
    now = timezone.now()
    rows = (
        Property.objects.filter(is_active=True)
        .annotate(review_count=Count("reviews"), average_rating=Avg("reviews__rating"),
                  newest_review=Max("reviews__review_date"))
        .order_by("name")
    )
    data = []
    for p in rows:
        last_run = p.scrape_runs.order_by("-started_at").first()
        last_ok = p.scrape_runs.filter(status=ScrapeRun.Status.SUCCESS).order_by("-finished_at").first()
        if last_run and last_run.status in (ScrapeRun.Status.FAILED, ScrapeRun.Status.BLOCKED):
            freshness = "failing"
        elif last_ok and last_ok.finished_at and now - last_ok.finished_at <= STALE_AFTER:
            freshness = "ok"
        else:
            freshness = "stale"
        data.append({
            "slug": p.slug,
            "name": p.name,
            "booking_url": p.booking_url,
            "review_count": p.review_count,
            "average_rating": _rating(p.average_rating),
            "newest_review": p.newest_review,
            "last_successful_scrape": last_ok.finished_at if last_ok else None,
            "last_run": {
                "status": last_run.status,
                "started_at": last_run.started_at,
                "message": (last_run.error_message.splitlines() or [""])[0],
            } if last_run else None,
            "freshness": freshness,
        })
    return Response(data)


@api_view(["GET"])
def summary(request):
    filters = parse_filters(request.query_params)
    period, previous = week_comparison(today(), _period_params(request)["weeks_ago"])
    base = scope(filters, include_dates=False)

    props = filters.get("property") or list(Property.objects.filter(is_active=True).order_by("name"))
    return Response({
        "period": _period_json(period),
        "previous_period": _period_json(previous),
        "overall": _compare(period_stats(_in(base, period)), period_stats(_in(base, previous))),
        "properties": [
            {
                "slug": p.slug,
                "name": p.name,
                **_compare(period_stats(_in(base.filter(property=p), period)),
                           period_stats(_in(base.filter(property=p), previous))),
            }
            for p in props
        ],
    })


@api_view(["GET"])
def trends(request):
    filters = parse_filters(request.query_params)
    granularity = _period_params(request)["granularity"]
    start, end = _date_range(filters)
    trunc = TruncMonth if granularity == "month" else TruncWeek
    rows = (
        scope(filters, include_dates=False)
        .filter(review_date__range=(start, end))
        .annotate(bucket=trunc("review_date"))
        .values("bucket")
        .annotate(
            avg=Avg("rating"),
            n=Count("id"),
            positive=Count("id", filter=Q(sentiment=Review.Sentiment.POSITIVE)),
            mixed=Count("id", filter=Q(sentiment=Review.Sentiment.MIXED)),
            negative=Count("id", filter=Q(sentiment=Review.Sentiment.NEGATIVE)),
        )
    )
    by_bucket = {_bucket_key(r["bucket"]): r for r in rows}
    points = []
    for b in _buckets(start, end, granularity):
        r = by_bucket.get(b, {})
        n = r.get("n", 0)
        points.append({
            "period_start": b,
            "average_rating": _rating(r.get("avg")),
            "review_count": n,
            "positive": r.get("positive", 0),
            "mixed": r.get("mixed", 0),
            "negative": r.get("negative", 0),
            "low_sample": n < MIN_REVIEWS,
        })
    return Response({"granularity": granularity, "start": start, "end": end, "points": points})


@api_view(["GET"])
def topics(request):
    filters = parse_filters(request.query_params)
    start, end = _date_range(filters)
    qs = scope(filters, include_dates=False).filter(review_date__range=(start, end))

    weekly_totals = {
        _bucket_key(r["bucket"]): r["n"]
        for r in qs.annotate(bucket=TruncWeek("review_date")).values("bucket").annotate(n=Count("id"))
    }
    weekly_complaints: dict[date, dict[str, int]] = {}
    complaint_rows = (
        ReviewTopic.objects.filter(review__in=qs, polarity=ReviewTopic.Polarity.NEGATIVE)
        .annotate(bucket=TruncWeek("review__review_date"))
        .values("bucket", "topic")
        .annotate(n=Count("review", distinct=True))
    )
    for r in complaint_rows:
        weekly_complaints.setdefault(_bucket_key(r["bucket"]), {})[r["topic"]] = r["n"]

    trend = []
    for b in _buckets(start, end, "week"):
        total = weekly_totals.get(b, 0)
        counts = {k: weekly_complaints.get(b, {}).get(k, 0) for k in TOPIC_LABELS}
        trend.append({
            "period_start": b,
            "review_count": total,
            "complaints": counts,
            "complaint_share": {k: _pct(v, total) for k, v in counts.items()},
        })
    return Response({
        "start": start,
        "end": end,
        "review_count": qs.count(),
        "topics": [asdict(s) for s in topic_stats(qs)],
        "trend": trend,
    })


@api_view(["GET"])
def insights(request):
    filters = parse_filters(request.query_params)
    base = scope(filters, include_dates=False)
    if filters.get("start") and filters.get("end"):
        days = (filters["end"] - filters["start"]).days + 1
        period = Period(filters["start"], filters["end"], "in the selected period")
        previous = Period(filters["start"] - timedelta(days=days), filters["start"] - timedelta(days=1),
                          f"the previous {days} days")
    else:
        period, previous = week_comparison(today(), _period_params(request)["weeks_ago"])
    current, before = _in(base, period), _in(base, previous)
    return Response({
        "period": _period_json(period),
        "previous_period": _period_json(previous),
        "review_count": current.count(),
        "insights": generate_insights(current, before, period=period.label, previous_period=previous.label),
    })


class FeedPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class ReviewFeed(ListAPIView):
    serializer_class = ReviewSerializer
    pagination_class = FeedPagination

    def get_queryset(self):
        filters = parse_filters(self.request.query_params)
        return feed(filters).select_related("property").prefetch_related("topics")
