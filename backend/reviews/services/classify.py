"""Apply the topic/sentiment classifier to stored reviews."""
from django.db import transaction
from django.db.models import QuerySet

from reviews.insights.topics import CLASSIFIER_VERSION, classify_review, sentiment_from_rating
from reviews.models import Review, ReviewTopic


def classify(review: Review) -> int:
    """(Re)classify one review. Idempotent: replaces its topics. Returns the number of topics."""
    mentions = classify_review(review.positive_text, review.negative_text)
    with transaction.atomic():
        review.topics.all().delete()
        ReviewTopic.objects.bulk_create([
            ReviewTopic(review=review, topic=m.topic, polarity=m.polarity, snippet=m.snippet, matched=m.matched[:64])
            for m in mentions
        ])
        review.sentiment = sentiment_from_rating(review.rating)
        review.classifier_version = CLASSIFIER_VERSION
        Review.objects.filter(pk=review.pk).update(
            sentiment=review.sentiment, classifier_version=CLASSIFIER_VERSION
        )
    return len(mentions)


def classify_pending(queryset: QuerySet | None = None, force: bool = False) -> int:
    """Classify reviews never classified, or classified by an older lexicon version."""
    qs = queryset if queryset is not None else Review.objects.all()
    if not force:
        qs = qs.filter(classifier_version__lt=CLASSIFIER_VERSION)
    count = 0
    for review in qs.iterator(chunk_size=500):
        classify(review)
        count += 1
    return count
