"""
Group mandis by how their prices behave (K-means + PCA).

    python manage.py cluster_markets
    python manage.py cluster_markets --commodity Onion
"""

from django.core.management.base import BaseCommand

from prices.ml.clustering import cluster_commodity
from prices.models import AggNationalDaily


class Command(BaseCommand):
    help = "Group mandis that behave alike"

    def add_arguments(self, parser):
        parser.add_argument("--commodity", action="append")

    def handle(self, *args, **options):
        commodities = options["commodity"] or sorted(
            AggNationalDaily.objects.values_list("commodity", flat=True).distinct()
        )
        for commodity in commodities:
            cluster_commodity(commodity, log=self.stdout.write)
