from django.contrib import admin

from .models import Property, Review, ReviewTopic, ScrapeRun


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "country_code", "page_name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")


class ReviewTopicInline(admin.TabularInline):
    model = ReviewTopic
    extra = 0
    readonly_fields = ("topic", "polarity", "matched", "snippet")
    can_delete = False


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("property", "review_date", "rating", "sentiment", "title", "reviewer_country")
    list_filter = ("property", "sentiment", "topics__topic", "review_date")
    inlines = [ReviewTopicInline]
    search_fields = ("title", "positive_text", "negative_text")
    readonly_fields = ("fingerprint", "content_hash", "first_seen_at", "last_seen_at", "raw")
    date_hierarchy = "review_date"


@admin.register(ScrapeRun)
class ScrapeRunAdmin(admin.ModelAdmin):
    list_display = (
        "property", "mode", "status", "fetcher", "started_at", "pages_fetched",
        "reviews_created", "reviews_updated", "reviews_invalid",
    )
    list_filter = ("status", "mode", "property")
