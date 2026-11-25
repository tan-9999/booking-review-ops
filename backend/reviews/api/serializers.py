from rest_framework import serializers

from reviews.insights.topics import TOPIC_LABELS
from reviews.models import Review, ReviewTopic


class ReviewTopicSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()

    class Meta:
        model = ReviewTopic
        fields = ["topic", "label", "polarity", "snippet"]

    def get_label(self, obj) -> str:
        return TOPIC_LABELS.get(obj.topic, obj.topic)


class ReviewSerializer(serializers.ModelSerializer):
    property = serializers.CharField(source="property.slug")
    property_name = serializers.CharField(source="property.name")
    rating = serializers.FloatField()
    topics = ReviewTopicSerializer(many=True, read_only=True)

    class Meta:
        model = Review
        fields = [
            "id", "property", "property_name", "review_date", "rating", "sentiment",
            "title", "positive_text", "negative_text", "hotel_response",
            "reviewer_name", "reviewer_country", "traveller_type", "room_type",
            "nights", "stay_date", "topics",
        ]
