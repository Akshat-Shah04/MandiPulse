"""
Save the API's answers as JSON files so the dashboard can run without Django.

Used for the hosted demo (Vercel): the frontend is built with
NEXT_PUBLIC_STATIC_DATA=1 and reads these files instead of calling /api.

    python manage.py export_static                      # default: last 60 days of maps
    python manage.py export_static --days 30

We don't copy any logic here. Each file is simply what the real API view
returns for one URL, fetched with Django's test client. So the demo shows
exactly the same numbers as the full app.

Layout of the output folder (frontend/public/data by default):

    status.json  commodities.json  models.json
    onion/dates.json  onion/groups.json
    onion/map/2026-11-11.json            one file per day
    onion/series/india.json  onion/series/state/maharashtra.json
    onion/series/market/123.json         one file per mandi
    onion/forecast/india.json  onion/forecast/state/maharashtra.json
"""

import json
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import Client

from prices.models import AggNationalDaily, AggStateDaily, Forecast

DEFAULT_OUT = Path(settings.BASE_DIR).parent / "frontend" / "public" / "data"


def slug(text):
    """'Tamil Nadu' -> 'tamil-nadu'. The frontend uses the same rule (lib/api.ts)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Command(BaseCommand):
    help = "Export API responses as static JSON for the hosted demo"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60, help="How many recent days of maps to include")
        parser.add_argument("--history-days", type=int, default=365, help="Length of price-history charts")
        parser.add_argument("--out", type=Path, default=DEFAULT_OUT)

    def handle(self, *args, **options):
        self.client = Client(HTTP_HOST="localhost")
        self.out = options["out"]
        self.files = 0
        self.bytes = 0

        # Start clean so files from an older snapshot don't linger.
        if self.out.exists():
            shutil.rmtree(self.out)

        history = options["history_days"]
        self.save("/api/status", "status.json")
        self.save("/api/models", "models.json")
        commodities = self.save("/api/commodities", "commodities.json")

        for c in commodities:
            name = c["commodity"]
            folder = slug(name)
            self.stdout.write(f"{name} ...")

            # Only the most recent days get a map file; the timeline shows just those.
            all_days = self.fetch(f"/api/dates?commodity={name}")
            recent_days = all_days[-options["days"]:]
            self.write(f"{folder}/dates.json", recent_days)

            self.save(f"/api/groups?commodity={name}", f"{folder}/groups.json")
            self.save(f"/api/series?commodity={name}&days={history}", f"{folder}/series/india.json")
            self.save(f"/api/forecast?commodity={name}", f"{folder}/forecast/india.json")

            # Every state that has data for this commodity.
            states = AggStateDaily.objects.filter(commodity=name).values_list("state", flat=True).distinct()
            forecast_states = set(Forecast.objects.filter(commodity=name).values_list("state", flat=True))
            for state in states:
                self.save(
                    f"/api/series?commodity={name}&state={state}&days={history}",
                    f"{folder}/series/state/{slug(state)}.json",
                )
                if state in forecast_states:
                    self.save(f"/api/forecast?commodity={name}&state={state}", f"{folder}/forecast/state/{slug(state)}.json")

            # One map per day, and remember which mandis appear so we can export their history.
            mandis = set()
            for day in recent_days:
                snapshot = self.save(f"/api/map?commodity={name}&date={day['date']}", f"{folder}/map/{day['date']}.json")
                mandis.update(m["id"] for m in snapshot["markets"])

            for market_id in sorted(mandis):
                self.save(
                    f"/api/series?commodity={name}&market_id={market_id}&days={history}",
                    f"{folder}/series/market/{market_id}.json",
                )

        latest = AggNationalDaily.objects.order_by("-date").values_list("date", flat=True).first()
        self.write("snapshot.json", {"latest_price_date": latest, "map_days": options["days"]})
        self.stdout.write(
            self.style.SUCCESS(f"Wrote {self.files} files, {self.bytes / 1_048_576:.1f} MB, to {self.out}")
        )

    # ---------------------------------------------------------------- helpers

    def fetch(self, url):
        response = self.client.get(url)
        if response.status_code != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status_code}")
        return response.json()

    def save(self, url, filename):
        """Fetch one API URL and write its JSON to a file. Returns the data."""
        data = self.fetch(url)
        self.write(filename, data)
        return data

    def write(self, filename, data):
        path = self.out / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        # Compact JSON (no spaces) keeps the snapshot small.
        text = json.dumps(data, separators=(",", ":"), default=str)
        path.write_text(text, encoding="utf-8")
        self.files += 1
        self.bytes += len(text.encode("utf-8"))
