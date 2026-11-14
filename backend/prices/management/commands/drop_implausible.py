"""
Remove stored prices that today's cleaning rules would reject.

Why: the cleaning rules in prices/agmarknet.py got stricter over time, but rows
loaded before a rule existed stay in FactPrice. Example: Patti APMC onion at
Rs 0.01/quintal was loaded before the lower price limit was added.

    python manage.py drop_implausible            # delete, then rebuild summaries
    python manage.py drop_implausible --dry-run  # only count

The raw API rows (RawRecord) are kept, so nothing is lost for good.
Afterwards re-run detect_anomalies and cluster_markets so they use clean data.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from prices.aggregates import rebuild
from prices.agmarknet import MAX_PLAUSIBLE_PRICE, MIN_PLAUSIBLE_PRICE
from prices.models import FactPrice


class Command(BaseCommand):
    help = "Delete stored prices outside the plausible range and rebuild summaries"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only show what would be deleted")

    def handle(self, *args, **options):
        # Step 1: find rows outside the same limits clean() uses.
        too_low = Q(modal_price__lt=MIN_PLAUSIBLE_PRICE)
        too_high = Q(modal_price__gt=MAX_PLAUSIBLE_PRICE)
        bad = FactPrice.objects.filter(too_low | too_high)

        # Step 2: report per commodity, so the size of the clean-up is visible.
        per_commodity = bad.values("commodity__commodity").annotate(rows=Count("id")).order_by("commodity__commodity")
        for item in per_commodity:
            self.stdout.write(f"  {item['commodity__commodity']}: {item['rows']} rows")
        total = bad.count()
        self.stdout.write(f"{total} rows outside Rs {MIN_PLAUSIBLE_PRICE}-{MAX_PLAUSIBLE_PRICE}/quintal")

        if options["dry_run"] or total == 0:
            return

        # Step 3: the earliest affected day is where the summaries must be rebuilt from.
        earliest = bad.order_by("date__date").values_list("date__date", flat=True).first()
        bad.delete()
        state_rows, national_rows = rebuild(earliest)
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {total} rows; rebuilt summaries from {earliest} "
                               f"(state rows={state_rows}, national rows={national_rows})")
        )
