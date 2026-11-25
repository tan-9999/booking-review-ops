from django.core.management.base import BaseCommand

from reviews.insights.topics import CLASSIFIER_VERSION
from reviews.services.classify import classify_pending


class Command(BaseCommand):
    help = (
        "Tag reviews with operational topics and sentiment. New and changed reviews are "
        "tagged automatically during scraping; run this after changing the lexicon "
        "(bump CLASSIFIER_VERSION) or for data stored before tagging existed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Re-tag every review, not just outdated ones.")

    def handle(self, *args, **options):
        count = classify_pending(force=options["all"])
        self.stdout.write(self.style.SUCCESS(f"Classified {count} review(s) with classifier v{CLASSIFIER_VERSION}."))
