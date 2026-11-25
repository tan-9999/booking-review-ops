"""
Save raw HTML when something looks wrong (block, unknown layout, many invalid
reviews). A snapshot turns "the scraper broke" into a reproducible test case:
copy it into tests/fixtures, fix the parser, and the regression is covered.
"""
from datetime import datetime, timezone
from pathlib import Path

KEEP_PER_PROPERTY = 50


def save_snapshot(snapshot_dir: Path, property_slug: str, label: str, html: str) -> Path:
    folder = Path(snapshot_dir) / property_slug
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"{stamp}_{label}.html"
    path.write_text(html or "", encoding="utf-8")

    old = sorted(folder.glob("*.html"))[:-KEEP_PER_PROPERTY]
    for stale in old:
        stale.unlink(missing_ok=True)
    return path
