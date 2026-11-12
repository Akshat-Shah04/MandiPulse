"""Tests for the forecasting and anomaly code."""

import math
from datetime import date, timedelta

from django.test import TestCase

from prices.ml.anomalies import describe
from prices.ml.examples import FORECAST_DAYS, HISTORY_DAYS, make_examples
from prices.ml.forecasting import split_train_test
from prices.ml.models import NaiveModel, SameWeekdayModel
from prices.ml.series import PriceSeries


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
