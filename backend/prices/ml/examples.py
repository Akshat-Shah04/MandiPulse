"""
Step 2 of forecasting: cut each price series into training examples.

One example answers the question:
    "Standing on day T, knowing the last 28 days of prices,
     what will the price be on day T+1, T+2, ... T+14?"

We describe prices RELATIVE to day T, as log(price / price on day T).
So a value of +0.10 means "about 10% higher than on day T".
This lets one model learn from cheap and expensive commodities together.
"""

import math

import numpy as np

HISTORY_DAYS = 28  # how many past days a model can look at
FORECAST_DAYS = 14  # how many days ahead we predict


class Example:
    """One forecasting question and (if known) its answer."""

    def __init__(self, series_number, day_index, day, history, answers, answer_is_real, commodity):
        self.series_number = series_number  # which series in the list this came from
        self.day_index = day_index  # position of day T inside that series
        self.day = day  # the date of day T
        self.history = history  # 28 numbers: log(price[T-27..T] / price[T])
        self.answers = answers  # 14 numbers: log(price[T+1..T+14] / price[T]), None if unknown
        self.answer_is_real = answer_is_real  # 14 bools: only real reports are used for grading
        self.commodity = commodity
        self.price_today = None  # filled in below


def make_examples(series_list):
    """Build every possible example from every series."""
    examples = []
    for series_number, series in enumerate(series_list):
        prices = series.prices

        for t in range(HISTORY_DAYS - 1, len(prices)):
            # Day T itself must be a real report, not a copied price.
            if not series.is_real[t]:
                continue

            # We need all 28 history days to be known.
            window = prices[t - HISTORY_DAYS + 1: t + 1]
            if any(p is None for p in window):
                continue

            price_today = prices[t]
            history = [math.log(p / price_today) for p in window]

            answers = []
            answer_is_real = []
            for h in range(1, FORECAST_DAYS + 1):
                future = t + h
                if future < len(prices) and prices[future] is not None:
                    answers.append(math.log(prices[future] / price_today))
                    answer_is_real.append(series.is_real[future])
                else:
                    answers.append(None)  # the future has not happened yet
                    answer_is_real.append(False)

            ex = Example(series_number, t, series.dates[t], history, answers, answer_is_real, series.commodity)
            ex.price_today = price_today
            examples.append(ex)
    return examples


def has_all_answers(example):
    return all(a is not None for a in example.answers)


# ---------------------------------------------------------------- features
# The tree and linear models can't read a raw list of 28 numbers well, so we
# summarise the history into a few meaningful features.
#
# Note: we deliberately DON'T use the month or the price level as features.
# Our history doesn't cover every month yet, and the models would be asked
# about months they have never seen - that made them much worse in testing.


def one_hot(index, size):
    """one_hot(2, 5) -> [0, 0, 1, 0, 0]"""
    row = [0.0] * size
    row[index] = 1.0
    return row


def features_for(example, commodity_names):
    h = example.history  # h[-1] is day T (always 0.0), h[-2] is yesterday, ...

    # How different was the price 1, 2, 3 ... days ago? (as % change, in log form)
    lags = [h[-2], h[-3], h[-4], h[-5], h[-6], h[-7], h[-8], h[-15], h[-22], h[-28]]

    # Average over the last week, two weeks and four weeks.
    averages = [np.mean(h[-7:]), np.mean(h[-14:]), np.mean(h)]

    # How jumpy have prices been? (standard deviation of day-to-day changes)
    daily_changes = np.diff(h)
    jumpiness = [np.std(daily_changes[-6:]), np.std(daily_changes)]

    weekday = one_hot(example.day.weekday(), 7)
    commodity = one_hot(commodity_names.index(example.commodity), len(commodity_names))

    return lags + averages + jumpiness + weekday + commodity


def feature_rows(examples, commodity_names):
    """
    Tree/linear models predict one number at a time, so we make one row per
    (example, days-ahead) pair and add "days ahead" as an extra feature.
    14 rows per example: row 0 asks "1 day ahead?", row 13 asks "14 days ahead?".
    """
    rows = []
    for ex in examples:
        base = features_for(ex, commodity_names)
        for h in range(FORECAST_DAYS):
            rows.append(base + one_hot(h, FORECAST_DAYS))
    return np.array(rows)


def answer_column(examples):
    """The true answers, in the same order as feature_rows()."""
    answers = []
    for ex in examples:
        answers.extend(ex.answers)
    return np.array(answers, dtype=float)
