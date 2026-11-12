"""
Flag unusual mandi prices with an Isolation Forest.

    python manage.py detect_anomalies
    python manage.py detect_anomalies --commodity Onion
"""

from django.core.management.base import BaseCommand

from prices.ml.anomalies import detect
from prices.models import AggNationalDaily


class Command(BaseCommand):
    help = "Flag unusual mandi prices"

    def add_arguments(self, parser):
        parser.add_argument("--commodity", action="append")

    def handle(self, *args, **options):
        commodities = options["commodity"] or sorted(
            AggNationalDaily.objects.values_list("commodity", flat=True).distinct()
        )
        for commodity in commodities:
            detect(commodity, log=self.stdout.write)
