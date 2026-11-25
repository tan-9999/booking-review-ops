from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from reviews.models import Property, Review
from reviews.services.insights import generate_insights, topic_stats, week_comparison


class Command(BaseCommand):
    help = "Print topic breakdown and insights for a week (default: this week so far vs the same days last week)."

    def add_arguments(self, parser):
        parser.add_argument("--property", help="Property slug (default: all properties)")
        parser.add_argument("--weeks-ago", type=int, default=0, help="0 = this week, 1 = last week, ...")

    def handle(self, *args, **options):
        reviews = Review.objects.all()
        scope = "All properties"
        if options["property"]:
            try:
                prop = Property.objects.get(slug=options["property"])
            except Property.DoesNotExist as exc:
                raise CommandError(f"Unknown property: {options['property']}") from exc
            reviews = reviews.filter(property=prop)
            scope = prop.name

        period, previous = week_comparison(timezone.localdate(), options["weeks_ago"])
        current = reviews.filter(review_date__range=(period.start, period.end))
        before = reviews.filter(review_date__range=(previous.start, previous.end))

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{scope}: {period.start} to {period.end} ({current.count()} reviews) "
            f"vs {previous.start} to {previous.end} ({before.count()} reviews)"
        ))
        self.stdout.write(f"{'Topic':22} {'Complaints':>11} {'Praise':>8} {'% of neg. reviews':>18}")
        for s in topic_stats(current):
            self.stdout.write(f"{s.label:22} {s.complaints:>11} {s.praise:>8} {s.share_of_negative_reviews:>17.0f}%")

        self.stdout.write(self.style.MIGRATE_HEADING("\nInsights"))
        for insight in generate_insights(current, before, period=period.label, previous_period=previous.label):
            style = self.style.WARNING if insight["severity"] == "high" else (lambda x: x)
            self.stdout.write(style(f"  - {insight['text']}"))
