from django.urls import path

from . import views

urlpatterns = [
    path("meta/", views.meta, name="api-meta"),
    path("properties/", views.properties, name="api-properties"),
    path("summary/", views.summary, name="api-summary"),
    path("trends/", views.trends, name="api-trends"),
    path("topics/", views.topics, name="api-topics"),
    path("insights/", views.insights, name="api-insights"),
    path("reviews/", views.ReviewFeed.as_view(), name="api-reviews"),
]
