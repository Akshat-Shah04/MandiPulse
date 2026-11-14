"""Tests for the forecasting and anomaly code."""

import math
from datetime import date, timedelta

from django.test import TestCase

from prices.ml.anomalies import describe
from prices.ml.examples import FORECAST_DAYS, HISTORY_DAYS, make_examples
from prices.ml.forecasting import sample, split_train_test
from prices.ml.models import NaiveModel, SameWeekdayModel
from prices.ml.series import MIN_MANDI_REPORTING_DAYS, PriceSeries, load_mandi_series


def make_series(prices, start=date(2026, 1, 1), real=None):
    days = [start + timedelta(days=i) for i in range(len(prices))]
    return PriceSeries("Onion", "Maharashtra", days, prices, real or [p is not None for p in prices])


class ExampleTests(TestCase):
    def test_answers_are_relative_to_today(self):
        prices = [1000.0] * HISTORY_DAYS + [1100.0] * FORECAST_DAYS
        first = make_examples([make_series(prices)])[0]
        self.assertAlmostEqual(first.history[-1], 0.0)
        self.assertAlmostEqual(first.answers[0], math.log(1.1))

    def test_copied_days_are_not_used_for_grading(self):
        prices = [1000.0] * (HISTORY_DAYS + 3)
        real = [True] * len(prices)
        real[HISTORY_DAYS] = False  # day after the first origin was a copied price
        first = make_examples([make_series(prices, real=real)])[0]
        self.assertFalse(first.answer_is_real[0])
        self.assertTrue(first.answer_is_real[1])

    def test_future_unknown_at_the_end(self):
        prices = [1000.0] * (HISTORY_DAYS + 2)
        last = make_examples([make_series(prices)])[-1]
        self.assertIsNone(last.answers[0])


class SplitTests(TestCase):
    def test_training_answers_never_reach_the_test_period(self):
        examples = make_examples([make_series([1000.0 + i for i in range(200)])])
        train, test = split_train_test(examples)
        first_test_day = min(ex.day for ex in test)
        for ex in train:
            self.assertLess(ex.day + timedelta(days=FORECAST_DAYS), first_test_day)
        self.assertEqual(len({ex.day for ex in test}), 42)


class BaselineTests(TestCase):
    def test_naive_predicts_no_change(self):
        ex = make_examples([make_series([1000.0] * 40)])[0]
        self.assertEqual(NaiveModel().predict([ex]).tolist(), [[0.0] * FORECAST_DAYS])

    def test_same_weekday_looks_one_week_back(self):
        # a price pattern that repeats every 7 days
        week = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0]
        ex = make_examples([make_series(week * 8)])[0]
        predicted = SameWeekdayModel().predict([ex])[0]
        today = ex.history[-1]
        # 7 days ahead should equal today's price (same weekday): log ratio 0
        self.assertAlmostEqual(predicted[6], today)
        # 1 day ahead should equal the price 6 days ago
        self.assertAlmostEqual(predicted[0], ex.history[-7])


class AnomalyTextTests(TestCase):
    def test_reason_mentions_both_checks(self):
        text = describe({"vs_state": math.log(1.62), "vs_own": math.log(2.1)})
        self.assertIn("62% above the state's typical price", text)
        self.assertIn("2.1x this mandi's recent price", text)

    def test_small_differences_are_not_mentioned(self):
        self.assertEqual(describe({"vs_state": math.log(1.2), "vs_own": 0.0}), "")


class ClusteringTests(TestCase):
    def setUp(self):
        from prices.ml.clustering import WEEKS

        self.weeks = [date(2026, 5, 4) + timedelta(weeks=i) for i in range(WEEKS)]

    def test_mandi_with_too_few_weeks_is_skipped(self):
        from prices.ml.clustering import describe_market

        india = {w: 1000.0 for w in self.weeks}
        prices = {w: 900.0 for w in self.weeks[:5]}
        self.assertIsNone(describe_market(prices, india, self.weeks))

    def test_cheap_mandi_that_copies_india(self):
        from prices.ml.clustering import describe_market

        india = {w: 1000.0 + 50 * (i % 3) for i, w in enumerate(self.weeks)}
        prices = {w: india[w] * 0.8 for w in self.weeks}  # always 20% cheaper, same moves
        price_vs_india, swing, trend, follows = describe_market(prices, india, self.weeks)
        self.assertAlmostEqual(math.exp(price_vs_india), 0.8, places=3)
        self.assertAlmostEqual(follows, 1.0, places=3)

    def test_group_label_lists_standout_traits(self):
        from prices.ml.clustering import name_group

        centre = [math.log(0.7), 0.30, 0.0, 0.05]  # 30% cheaper, very jumpy, independent
        label, description, _ = name_group(centre, typical_swing=0.10)
        self.assertEqual(label, "Cheap · volatile · own path")
        self.assertIn("30% below India", description)


# ---------------------------------------------------------------- mandi forecasts


def mandi_record(market, day, price):
    """One raw API row for a Nashik onion mandi on `day`."""
    return {
        "State": "Maharashtra", "District": "Nashik", "Market": market, "Commodity": "Onion",
        "Variety": "Red", "Grade": "FAQ", "Arrival_Date": day.strftime("%d/%m/%Y"),
        "Min_Price": str(price - 200), "Max_Price": str(price + 200), "Modal_Price": str(price),
    }


class MandiSeriesTests(TestCase):
    def setUp(self):
        from prices.agmarknet import load_records

        last = date(2026, 11, 11)
        records = []
        # Lasalgaon reports every day for 150 days; Yeola only every third day.
        for i in range(150):
            day = last - timedelta(days=i)
            records.append(mandi_record("Lasalgaon", day, 2000 + i))
            if i % 3 == 0:
                records.append(mandi_record("Yeola", day, 2100))
        load_records(records, {})

    def test_only_mandis_that_report_often_get_a_series(self):
        series = load_mandi_series()
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].state, "Maharashtra")
        self.assertIsNotNone(series[0].market_id)
        self.assertGreaterEqual(sum(series[0].is_real), MIN_MANDI_REPORTING_DAYS)


class SampleTests(TestCase):
    def test_sample_caps_and_is_repeatable(self):
        items = list(range(1000))
        self.assertEqual(sample(items, 100), sample(items, 100))
        self.assertEqual(len(sample(items, 100)), 100)
        self.assertIs(sample(items, None), items)


class ForecastApiTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        from prices.agmarknet import load_records
        from prices.aggregates import rebuild
        from prices.models import DimMarket, Forecast

        load_records([mandi_record("Lasalgaon", date(2026, 11, 11), 2000)], {})
        rebuild()
        cache.clear()
        self.market = DimMarket.objects.get(market="Lasalgaon")
        common = dict(commodity="Onion", model="naive", origin=date(2026, 11, 11), target=date(2026, 11, 12))
        Forecast.objects.create(state="Maharashtra", yhat=2000, lo=1900, hi=2100, **common)
        Forecast.objects.create(state="Maharashtra", market=self.market, yhat=2500, lo=2000, hi=3000, **common)

    def test_state_forecast_ignores_mandi_rows(self):
        body = self.client.get("/api/forecast?commodity=Onion&state=Maharashtra").json()
        self.assertEqual([p["yhat"] for p in body["points"]], [2000])

    def test_mandi_forecast(self):
        body = self.client.get(f"/api/forecast?commodity=Onion&market_id={self.market.id}").json()
        self.assertTrue(body["available"])
        self.assertEqual(body["market_id"], self.market.id)
        self.assertEqual([p["yhat"] for p in body["points"]], [2500])

    def test_mandi_without_forecast_is_not_available(self):
        body = self.client.get("/api/forecast?commodity=Onion&market_id=999999").json()
        self.assertFalse(body["available"])
