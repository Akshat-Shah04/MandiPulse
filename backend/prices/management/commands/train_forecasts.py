"""
Test all forecasting models and publish 14-day forecasts.

    python manage.py train_forecasts
"""

from django.core.management.base import BaseCommand

from prices.ml.forecasting import run


class Command(BaseCommand):
    help = "Back-test forecasting models and publish 14-day forecasts"

    def add_arguments(self, parser):
        parser.add_argument("--commodity", action="append", help="Only these commodities (repeatable)")

    def handle(self, *args, **options):
        report = run(options["commodity"], log=self.stdout.write)

        self.stdout.write("")
        self.stdout.write("Average error over days 1-14 (lower is better):")
        self.stdout.write(f"  {'model':<16}{'Rs/quintal':>12}{'percent':>10}")
        for name, err in sorted(report["average_error"].items(), key=lambda kv: kv[1]["mae"]):
            self.stdout.write(f"  {name:<16}{err['mae']:>12.1f}{err['mape']:>9.2f}%")
