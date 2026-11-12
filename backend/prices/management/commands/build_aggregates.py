from datetime import date

from django.core.management.base import BaseCommand

from prices.aggregates import rebuild


class Command(BaseCommand):
    help = "Rebuild daily state/national price summaries from FactPrice"

    def add_arguments(self, parser):
        parser.add_argument("--since", type=date.fromisoformat)
        parser.add_argument("--commodity")

    def handle(self, *args, **opts):
        s, n = rebuild(opts["since"], commodity=opts["commodity"])
        self.stdout.write(f"state rows={s} national rows={n}")
