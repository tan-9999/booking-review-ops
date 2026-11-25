import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from reviews.models import Property, parse_booking_url


class Command(BaseCommand):
    help = "Create or update tracked properties from a JSON file."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(settings.BASE_DIR / "properties.json"))

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {path}: {exc}") from exc

        for entry in entries:
            try:
                slug, name, url = entry["slug"], entry["name"], entry["url"]
                parse_booking_url(url)
            except KeyError as exc:
                raise CommandError(f"Entry missing field {exc}: {entry}") from exc
            except ValueError as exc:
                raise CommandError(str(exc)) from exc

            obj, created = Property.objects.update_or_create(
                slug=slug, defaults={"name": name, "booking_url": url, "is_active": True}
            )
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(
                f"{verb} {obj.name} (cc={obj.country_code}, page={obj.page_name})"
            ))
