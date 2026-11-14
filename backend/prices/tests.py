from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase

from .aggregates import rebuild
from .agmarknet import clean, load_records
from .models import AggNationalDaily, AggStateDaily, FactPrice

SAMPLE = {
    "State": "Maharashtra",
    "District": "Nashik",
    "Market": "Lasalgaon",
    "Commodity": "Onion",
    "Variety": "Red",
    "Grade": "FAQ",
    "Arrival_Date": "11/11/2026",
    "Min_Price": "1200",
    "Max_Price": "2600",
    "Modal_Price": "2100",
}


class CleanTests(TestCase):
    def test_valid_row(self):
        row, reason = clean(SAMPLE)
        self.assertEqual(reason, "")
        self.assertEqual(row.modal_price, Decimal("2100"))
        self.assertEqual(row.arrival_date.isoformat(), "2026-11-11")

    def test_lowercase_field_names(self):
        row, _ = clean({k.lower(): v for k, v in SAMPLE.items()})
        self.assertIsNotNone(row)

    def test_state_alias_folded(self):
        row, _ = clean(SAMPLE | {"State": "Uttrakhand"})
        self.assertEqual(row.state, "Uttarakhand")

    def test_swapped_min_max_repaired(self):
        row, _ = clean(SAMPLE | {"Min_Price": "2600", "Max_Price": "1200"})
        self.assertEqual((row.min_price, row.max_price), (Decimal("1200"), Decimal("2600")))

    def test_modal_clamped_into_range(self):
        row, _ = clean(SAMPLE | {"Modal_Price": "9000"})
        self.assertEqual(row.modal_price, Decimal("2600"))

    def test_rejects_zero_modal(self):
        row, reason = clean(SAMPLE | {"Modal_Price": "0"})
        self.assertIsNone(row)
        self.assertIn("modal", reason)

    def test_rejects_paise_typo(self):
        # Seen in the live feed: Onion at Rs 0.01 per quintal.
        row, _ = clean(SAMPLE | {"Min_Price": "0.01", "Max_Price": "0.01", "Modal_Price": "0.01"})
        self.assertIsNone(row)

    def test_rejects_per_kg_price_in_quintal_column(self):
        # Seen in the live feed: tomato at "15" per quintal, really Rs 15 per kg.
        row, reason = clean(SAMPLE | {"Min_Price": "10", "Max_Price": "22", "Modal_Price": "15"})
        self.assertIsNone(row)
        self.assertEqual(reason, "implausibly low modal price")

    def test_keeps_glut_price_of_one_rupee_per_kg(self):
        row, _ = clean(SAMPLE | {"Min_Price": "100", "Max_Price": "150", "Modal_Price": "100"})
        self.assertEqual(row.modal_price, Decimal("100"))

    def test_rejects_absurdly_high_modal(self):
        # Seen in the live feed: tomato at Rs 27 crore per quintal.
        row, reason = clean(SAMPLE | {"Min_Price": "250000000", "Max_Price": "400000000", "Modal_Price": "270000000"})
        self.assertIsNone(row)
        self.assertIn("high", reason)

    def test_max_price_typo_is_repaired(self):
        # Seen in the live feed: max 30002800 with modal 2600.
        row, _ = clean(SAMPLE | {"Min_Price": "2400", "Max_Price": "30002800", "Modal_Price": "2600"})
        self.assertEqual(row.max_price, Decimal("2600"))

    def test_rejects_bad_date(self):
        row, reason = clean(SAMPLE | {"Arrival_Date": "2026-11-11"})
        self.assertIsNone(row)


class LoadTests(TestCase):
    def test_reload_is_idempotent(self):
        load_records([SAMPLE], {})
        load_records([SAMPLE | {"Modal_Price": "2200"}], {})
        self.assertEqual(FactPrice.objects.count(), 1)
        self.assertEqual(FactPrice.objects.get().modal_price, Decimal("2200"))

    def test_duplicate_rows_in_one_page(self):
        loaded, rejected = load_records([SAMPLE, SAMPLE, SAMPLE | {"Modal_Price": "0"}], {})
        self.assertEqual((loaded, rejected), (1, 1))
        self.assertEqual(FactPrice.objects.count(), 1)


class ApiTests(TestCase):
    def setUp(self):
        load_records(
            [
                SAMPLE,
                SAMPLE | {"Market": "Pimpalgaon", "Modal_Price": "2300", "Max_Price": "2700"},
                SAMPLE | {"State": "Gujarat", "District": "Bhavnagar", "Market": "Mahuva", "Modal_Price": "1800"},
                SAMPLE | {"Arrival_Date": "04/11/2026", "Modal_Price": "1500"},
            ],
            {},
        )
        rebuild()
        cache.clear()

    def test_map_defaults_to_latest_day(self):
        body = self.client.get("/api/map?commodity=Onion").json()
        self.assertEqual(body["date"], "2026-11-11")
        states = {s["state"]: s for s in body["states"]}
        self.assertEqual(states["Maharashtra"]["median_modal"], 2200.0)
        self.assertEqual(states["Maharashtra"]["markets"], 2)
        self.assertAlmostEqual(states["Maharashtra"]["change_7d_pct"], 46.67)
        self.assertEqual(len(body["markets"]), 3)

    def test_map_unknown_commodity_404(self):
        self.assertEqual(self.client.get("/api/map?commodity=Saffron").status_code, 404)

    def test_map_requires_commodity(self):
        self.assertEqual(self.client.get("/api/map").status_code, 400)

    def test_series_by_state(self):
        body = self.client.get("/api/series?commodity=Onion&state=Maharashtra").json()
        self.assertEqual([p["median_modal"] for p in body["points"]], [1500.0, 2200.0])

    def test_commodities_lists_onion(self):
        body = self.client.get("/api/commodities").json()
        self.assertEqual(body[0]["commodity"], "Onion")

    def test_dates_lists_days_in_order(self):
        body = self.client.get("/api/dates?commodity=Onion").json()
        self.assertEqual([d["date"] for d in body], ["2026-11-04", "2026-11-11"])


class AggregateTests(TestCase):
    def test_varieties_collapse_to_one_price_per_market(self):
        load_records(
            [
                SAMPLE | {"Variety": "Red", "Modal_Price": "2000"},
                SAMPLE | {"Variety": "White", "Modal_Price": "3000", "Max_Price": "3200"},
                SAMPLE | {"Market": "Pimpalgaon", "Modal_Price": "1000", "Min_Price": "900"},
            ],
            {},
        )
        rebuild()
        st = AggStateDaily.objects.get(state="Maharashtra")
        # Lasalgaon counts once at 2500 (median of its varieties), Pimpalgaon at 1000.
        self.assertEqual(st.markets, 2)
        self.assertEqual(st.median_modal, 1750.0)
        self.assertEqual(AggNationalDaily.objects.get().markets, 2)

    def test_rebuild_is_idempotent(self):
        load_records([SAMPLE], {})
        rebuild()
        rebuild()
        self.assertEqual(AggStateDaily.objects.count(), 1)
