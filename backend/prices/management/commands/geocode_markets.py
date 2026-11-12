from collections import Counter

from django.core.management.base import BaseCommand

from prices.geocode import locate
from prices.models import DimMarket


class Command(BaseCommand):
    help = "Fill latitude/longitude for markets from district centroids"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Redo markets that already have a location")

    def handle(self, *args, **opts):
        qs = DimMarket.objects.all() if opts["all"] else DimMarket.objects.filter(latitude__isnull=True)
        how = Counter()
        unplaced = []
        batch = []
        for m in qs:
            hit = locate(m.state, m.district, m.market)
            if hit is None:
                how["none"] += 1
                unplaced.append(str(m))
                continue
            m.latitude, m.longitude, method = hit
            how[method] += 1
            batch.append(m)
        DimMarket.objects.bulk_update(batch, ["latitude", "longitude"], batch_size=500)
        self.stdout.write(f"placed {len(batch)} markets: {dict(how)}")
        for u in unplaced[:20]:
            self.stdout.write(f"  unplaced: {u}")
