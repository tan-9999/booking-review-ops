"""
Turn topic tags into numbers and plain-English findings for ops staff.

Definitions (shown in the README and dashboard tooltips):
  * negative review = Booking score below 6 (sentiment "negative")
  * a review "mentions X as a complaint" when X was found in its *disliked* text
  * shares are counted per review (a review mentioning cleanliness twice counts once)

Every sentence carries its counts ("6 of 15"), and nothing is claimed from a
sample that is too small to mean anything.
"""
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from django.db.models import Count, QuerySet

from reviews.insights.topics import TOPIC_LABELS
from reviews.models import Review, ReviewTopic

MIN_REVIEWS = 5           # below this, say "too few reviews" instead of percentages
MIN_NEGATIVE_REVIEWS = 3  # for "% of negative reviews mentioned X"
MIN_CHANGE_POINTS = 10    # week-on-week change worth flagging (percentage points)
MIN_CHANGE_COUNT = 3      # ...and at least this many reviews behind it


@dataclass
class TopicStat:
    topic: str
    label: str
    complaints: int                 # reviews with a negative mention
    praise: int                     # reviews with a positive mention
    complaint_share: float          # complaints / all reviews in period
    praise_share: float
    share_of_negative_reviews: float  # negative reviews mentioning it as a complaint / negative reviews


@dataclass
class Insight:
    kind: str       # "complaint" | "change" | "strength" | "data"
    severity: str   # "high" | "medium" | "info"
    text: str
    topic: str = ""


def week_range(day: date) -> tuple[date, date]:
    """Monday..Sunday containing `day`."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


@dataclass
class Period:
    start: date
    end: date
    label: str  # used inside insight sentences, e.g. "this week so far"


def week_comparison(today: date, weeks_ago: int = 0) -> tuple[Period, Period]:
    """
    The week to report on and a FAIR comparison period.

    An unfinished current week is compared like-for-like with the same weekdays
    of the previous week (Mon–Wed vs Mon–Wed). Comparing 3 days with a full
    week made complaints look like they "fell" just because fewer days had passed.
    """
    start, end = week_range(today - timedelta(weeks=weeks_ago))
    if weeks_ago == 0 and today < end:
        return (
            Period(start, today, "this week so far"),
            Period(start - timedelta(days=7), today - timedelta(days=7), "the same days last week"),
        )
    label = "this week" if weeks_ago == 0 else f"in the week of {start:%d %b}"
    previous_label = "last week" if weeks_ago == 0 else "the week before"
    return (
        Period(start, end, label),
        Period(start - timedelta(days=7), end - timedelta(days=7), previous_label),
    )


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _per_topic(reviews: QuerySet, polarity: str) -> dict[str, int]:
    rows = (
        ReviewTopic.objects.filter(review__in=reviews, polarity=polarity)
        .values("topic")
        .annotate(n=Count("review", distinct=True))
    )
    return {row["topic"]: row["n"] for row in rows}


def topic_stats(reviews: QuerySet) -> list[TopicStat]:
    total = reviews.count()
    negative_reviews = reviews.filter(sentiment=Review.Sentiment.NEGATIVE)
    negative_total = negative_reviews.count()
    complaints = _per_topic(reviews, ReviewTopic.Polarity.NEGATIVE)
    praise = _per_topic(reviews, ReviewTopic.Polarity.POSITIVE)
    in_negative = _per_topic(negative_reviews, ReviewTopic.Polarity.NEGATIVE)
    stats = [
        TopicStat(
            topic=key,
            label=label,
            complaints=complaints.get(key, 0),
            praise=praise.get(key, 0),
            complaint_share=_pct(complaints.get(key, 0), total),
            praise_share=_pct(praise.get(key, 0), total),
            share_of_negative_reviews=_pct(in_negative.get(key, 0), negative_total),
        )
        for key, label in TOPIC_LABELS.items()
    ]
    return sorted(stats, key=lambda s: (-s.complaints, s.label))


def generate_insights(current: QuerySet, previous: QuerySet | None = None,
                      period: str = "this week", previous_period: str = "last week") -> list[dict]:
    insights: list[Insight] = []
    total = current.count()
    if total < MIN_REVIEWS:
        insights.append(Insight(
            "data", "info",
            f"Only {total} review{'s' if total != 1 else ''} {period}: too few for reliable percentages. "
            "Widen the date range for trends.",
        ))
        return [asdict(i) for i in insights]

    stats = topic_stats(current)
    negative_total = current.filter(sentiment=Review.Sentiment.NEGATIVE).count()

    # 1. What negative reviews are about
    if negative_total >= MIN_NEGATIVE_REVIEWS:
        in_negative = _per_topic(current.filter(sentiment=Review.Sentiment.NEGATIVE), ReviewTopic.Polarity.NEGATIVE)
        top = sorted(in_negative.items(), key=lambda kv: -kv[1])[:3]
        for rank, (topic, n) in enumerate(top):
            share = _pct(n, negative_total)
            insights.append(Insight(
                "complaint", "high" if rank == 0 and share >= 30 else "medium",
                f"{share:.0f}% of negative reviews {period} mentioned {TOPIC_LABELS[topic].lower()} issues "
                f"({n} of {negative_total}).",
                topic,
            ))
    else:
        insights.append(Insight(
            "data", "info",
            f"{negative_total} negative review{'s' if negative_total != 1 else ''} {period} "
            "(score below 6): not enough to break down by topic.",
        ))

    # 2. What changed vs the previous period (shares, so busy and quiet weeks compare fairly)
    if previous is not None and previous.count() >= MIN_REVIEWS:
        before = {s.topic: s for s in topic_stats(previous)}
        changes = []
        for s in stats:
            delta = s.complaint_share - before[s.topic].complaint_share
            if abs(delta) >= MIN_CHANGE_POINTS and max(s.complaints, before[s.topic].complaints) >= MIN_CHANGE_COUNT:
                changes.append((delta, s, before[s.topic]))
        for delta, s, b in sorted(changes, key=lambda c: -abs(c[0]))[:2]:
            direction = "rose" if delta > 0 else "fell"
            insights.append(Insight(
                "change", "high" if delta > 0 else "info",
                f"{s.label} complaints {direction} from {b.complaint_share:.0f}% of reviews {previous_period} "
                f"to {s.complaint_share:.0f}% {period}.",
                s.topic,
            ))

    # 3. What guests value most (worth protecting)
    best = max(stats, key=lambda s: s.praise)
    if best.praise:
        insights.append(Insight(
            "strength", "info",
            f"{best.label} is the most praised topic {period}: {best.praise_share:.0f}% of reviews "
            f"({best.praise} of {total}).",
            best.topic,
        ))
    return [asdict(i) for i in insights]
