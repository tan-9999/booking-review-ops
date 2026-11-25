"""
Query-parameter validation shared by every endpoint.

Bad input returns a 400 with a field-level message, never a 500 and never a
silently empty result (an unknown property slug is an error, not "0 reviews").
"""
from django.db.models import Q, QuerySet
from rest_framework import serializers

from reviews.insights.topics import TOPIC_LABELS
from reviews.models import Property, Review, ReviewTopic

ORDERINGS = {
    "newest": ("-review_date", "-id"),
    "oldest": ("review_date", "id"),
    "lowest": ("rating", "-review_date"),
    "highest": ("-rating", "-review_date"),
}


class ReviewFilters(serializers.Serializer):
    property = serializers.CharField(required=False, help_text="Slug, or comma-separated slugs")
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    sentiment = serializers.ChoiceField(choices=Review.Sentiment.values, required=False)
    topic = serializers.ChoiceField(choices=list(TOPIC_LABELS), required=False)
    polarity = serializers.ChoiceField(choices=ReviewTopic.Polarity.values, required=False)
    min_rating = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=1, max_value=10, required=False)
    max_rating = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=1, max_value=10, required=False)
    search = serializers.CharField(required=False, max_length=200, trim_whitespace=True)
    ordering = serializers.ChoiceField(choices=list(ORDERINGS), required=False, default="newest")

    def validate_property(self, value: str) -> list[Property]:
        slugs = [s.strip() for s in value.split(",") if s.strip()]
        found = list(Property.objects.filter(slug__in=slugs))
        missing = sorted(set(slugs) - {p.slug for p in found})
        if missing:
            raise serializers.ValidationError(f"Unknown property: {', '.join(missing)}")
        return found

    def validate(self, attrs):
        if attrs.get("start") and attrs.get("end") and attrs["start"] > attrs["end"]:
            raise serializers.ValidationError({"end": "End date must be on or after the start date."})
        if attrs.get("min_rating") and attrs.get("max_rating") and attrs["min_rating"] > attrs["max_rating"]:
            raise serializers.ValidationError({"max_rating": "Maximum rating must be at least the minimum."})
        if attrs.get("polarity") and not attrs.get("topic"):
            raise serializers.ValidationError({"polarity": "Choose a topic to filter praise/complaints."})
        return attrs


def parse_filters(query_params) -> dict:
    serializer = ReviewFilters(data=query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def scope(filters: dict, include_dates: bool = True) -> QuerySet:
    """Property (+ optional date) scope: what summaries, trends and insights use."""
    qs = Review.objects.all()
    if filters.get("property"):
        qs = qs.filter(property__in=filters["property"])
    if include_dates:
        if filters.get("start"):
            qs = qs.filter(review_date__gte=filters["start"])
        if filters.get("end"):
            qs = qs.filter(review_date__lte=filters["end"])
    return qs


def feed(filters: dict) -> QuerySet:
    """Everything the review feed can filter on."""
    qs = scope(filters)
    if filters.get("sentiment"):
        qs = qs.filter(sentiment=filters["sentiment"])
    if filters.get("min_rating") is not None:
        qs = qs.filter(rating__gte=filters["min_rating"])
    if filters.get("max_rating") is not None:
        qs = qs.filter(rating__lte=filters["max_rating"])
    if filters.get("topic"):
        topic_q = Q(topics__topic=filters["topic"])
        if filters.get("polarity"):
            topic_q &= Q(topics__polarity=filters["polarity"])
        qs = qs.filter(topic_q).distinct()
    if filters.get("search"):
        term = filters["search"]
        qs = qs.filter(
            Q(title__icontains=term) | Q(positive_text__icontains=term)
            | Q(negative_text__icontains=term) | Q(hotel_response__icontains=term)
        )
    return qs.order_by(*ORDERINGS[filters.get("ordering", "newest")])
