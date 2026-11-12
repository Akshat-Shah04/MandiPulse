"""
Pull Agmarknet prices from data.gov.in into the warehouse.

    python manage.py ingest_agmarknet                       # today, all commodities
    python manage.py ingest_agmarknet --commodity Onion --commodity Tomato \
        --start 2024-11-01 --end 2026-11-12

Resumable: a (day, commodity) pair that already finished cleanly is skipped,
so a long backfill can be stopped and restarted. When data.gov.in is down
(it returns 502s for minutes at a time) the command backs off and retries
instead of giving up.
"""

import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from prices.agmarknet import AgmarknetError, ingest
from prices.aggregates import rebuild
from prices.geocode import locate
from prices.models import DimMarket
from prices.models import IngestRun


class Command(BaseCommand):
    help = "Ingest Agmarknet mandi prices for a date range"

    def add_arguments(self, parser):
        parser.add_argument("--start", type=date.fromisoformat, help="YYYY-MM-DD (default: today)")
        parser.add_argument("--end", type=date.fromisoformat, help="YYYY-MM-DD (default: start)")
        parser.add_argument("--commodity", action="append", help="Repeatable, e.g. --commodity Onion")
        parser.add_argument("--state")
        parser.add_argument("--newest-first", action="store_true",
                            help="Walk backwards from --end (recent data lands first)")
        parser.add_argument("--force", action="store_true", help="Re-fetch days already loaded")
        parser.add_argument("--outage-wait", type=int, default=600,
                            help="Seconds to wait after repeated failures (default 600)")

    def _done(self, day, commodity, state):
        return IngestRun.objects.filter(
            params__arrival_date=day.isoformat(),
            params__commodity=commodity,
            params__state=state,
            error="",
            finished_at__isnull=False,
        ).exists()

    def handle(self, *args, **opts):
        start = opts["start"] or date.today()
        end = opts["end"] or start
        if end < start:
            raise CommandError("--end is before --start")
        commodities = opts["commodity"] or [None]
        state = opts["state"]

        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if opts["newest_first"]:
            days.reverse()

        failures_in_a_row = 0
        for day in days:
            touched = False
            for commodity in commodities:
                if not opts["force"] and self._done(day, commodity, state):
                    continue
                while True:
                    try:
                        run = ingest(arrival_date=day, commodity=commodity, state=state)
                        touched = touched or run.rows_loaded > 0
                        failures_in_a_row = 0
                        break
                    except AgmarknetError as exc:
                        failures_in_a_row += 1
                        self.stderr.write(f"{day} {commodity}: {exc}")
                        if failures_in_a_row >= 3:
                            self.stderr.write(
                                f"data.gov.in looks down; waiting {opts['outage_wait']}s"
                            )
                            time.sleep(opts["outage_wait"])
                self.stdout.write(
                    f"{day} {commodity or 'ALL':<12} fetched={run.rows_fetched:>6} "
                    f"loaded={run.rows_loaded:>6} rejected={run.rows_rejected:>4}",
                )
                self.stdout.flush()
            if touched:
                rebuild(since=day, until=day)  # keep the summary tables in step
                self.place_new_markets()

    def place_new_markets(self):
        """Give any mandi seen for the first time a map position (its district centre)."""
        for market in DimMarket.objects.filter(latitude__isnull=True):
            found = locate(market.state, market.district, market.market)
            if found:
                market.latitude, market.longitude, _ = found
                market.save(update_fields=["latitude", "longitude"])
