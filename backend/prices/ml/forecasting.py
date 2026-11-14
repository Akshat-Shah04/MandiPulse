"""
Step 4 of forecasting: test every model fairly, then publish forecasts.

HOW WE TEST (a "back-test")
---------------------------
Pretend it is 8 weeks ago. Train each model only on what was known then.
Then, for each of the following 6 weeks, ask it to forecast 14 days ahead
and compare with what really happened. A model never sees the future it
is graded on.

HOW WE CHOOSE
-------------
For every "days ahead" (1 to 14) we pick whichever model had the smallest
average error in the test. In our data the LSTM is best for the first
few days and the simple baselines win further out, so the published
forecast is a mix - and it is never worse than "price stays the same".

The 80% band around each forecast comes from the test errors: 8 in 10
past forecasts landed inside a band that wide.

TWO LEVELS
----------
All of this runs twice: once on India + state median prices, and once on
single mandis that report most days. Mandi prices jump around much more,
so they get their own model choice, their own (wider) bands and their own
accuracy table, instead of borrowing the state numbers.
"""

import json
import math
import random
from datetime import timedelta

import numpy as np
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from prices.models import Forecast, ModelScore

from .examples import FORECAST_DAYS, has_all_answers, make_examples
from .models import all_models
from .series import load_mandi_series, load_series

TEST_DAYS = 42  # six weeks of forecast start-days are used for testing
RANDOM_SEED = 7

# Cap on mandi training examples (see sample()). Every test example is still scored.
MAX_MANDI_TRAIN_EXAMPLES = 40_000


def split_train_test(examples):
    """Split examples by date so training never overlaps the test period."""
    last_day = max(ex.day for ex in examples)
    # The last test start-day must still have 14 known days after it.
    test_end = last_day - timedelta(days=FORECAST_DAYS)
    test_start = test_end - timedelta(days=TEST_DAYS - 1)

    train, test = [], []
    for ex in examples:
        if test_start <= ex.day <= test_end:
            test.append(ex)
        # A training example's answers (up to 14 days later) must all be
        # before the test period, or the model would peek at test prices.
        elif ex.day + timedelta(days=FORECAST_DAYS) < test_start:
            train.append(ex)
    return train, test


def score_predictions(test, predictions):
    """
    Average error for each "days ahead", in Rs per quintal and in percent.
    Returns a list of 14 dicts: [{"mae": .., "mape": .., "n": ..}, ...]
    """
    scores = []
    for h in range(FORECAST_DAYS):
        abs_errors = []
        pct_errors = []
        for i, ex in enumerate(test):
            if ex.answers[h] is None or not ex.answer_is_real[h]:
                continue  # only grade against real mandi reports
            true_price = ex.price_today * math.exp(ex.answers[h])
            guess = ex.price_today * math.exp(predictions[i][h])
            abs_errors.append(abs(guess - true_price))
            pct_errors.append(abs(guess - true_price) / true_price * 100)
        scores.append({"mae": float(np.mean(abs_errors)), "mape": float(np.mean(pct_errors)), "n": len(abs_errors)})
    return scores


def log_errors(test, predictions, h):
    """Errors in log form for one horizon (used for the 80% band)."""
    errors = []
    for i, ex in enumerate(test):
        if ex.answers[h] is not None and ex.answer_is_real[h]:
            errors.append(ex.answers[h] - predictions[i][h])
    return errors


def sample(examples, limit, seed=RANDOM_SEED):
    """
    At most `limit` examples, picked at random (same pick every run).
    Mandi data gives ~10x more examples than state data; the random forest
    would take hours on all of them, and a sample learns nearly as well.
    """
    if limit is None or len(examples) <= limit:
        return examples
    rng = random.Random(seed)
    return rng.sample(examples, limit)


def forecast_level(series_list, level, log=print, max_train=None):
    """
    Back-test every model on one set of series, pick the best per day ahead,
    and forecast the next 14 days for each series.

    `level` is "state" (all-India and state medians) or "mandi" (single mandis).
    Returns (score rows, forecast rows, report section). Nothing is saved here.
    """
    examples = make_examples(series_list)
    train, test = split_train_test(examples)
    train = sample(train, max_train)
    log(f"[{level}] {len(series_list)} series, {len(train)} training examples, {len(test)} test examples")

    # ---- 1. back-test every model
    models = all_models()
    results = {}  # model name -> {"scores": [...], "predictions": array}
    for model in models:
        log(f"[{level}] testing {model.name} ...")
        model.train(train)
        predictions = model.predict(test)
        results[model.name] = {"scores": score_predictions(test, predictions), "predictions": predictions}

    # ---- 2. choose the best model for each "days ahead"
    best_for_day = []
    for h in range(FORECAST_DAYS):
        best = min(results, key=lambda name: results[name]["scores"][h]["mae"])
        best_for_day.append(best)
    log(f"[{level}] best model per day ahead: " + ", ".join(f"{h + 1}:{m}" for h, m in enumerate(best_for_day)))

    # ---- 3. size of the 80% band for each day ahead, from the chosen model's test errors
    band_low, band_high = [], []
    for h in range(FORECAST_DAYS):
        errors = log_errors(test, results[best_for_day[h]]["predictions"], h)
        band_low.append(float(np.quantile(errors, 0.10)))
        band_high.append(float(np.quantile(errors, 0.90)))

    # ---- 4. retrain the chosen models on ALL data and forecast from today
    with_answers = sample([ex for ex in examples if has_all_answers(ex)], max_train)
    needed = set(best_for_day)
    for model in models:
        if model.name in needed:
            log(f"[{level}] retraining {model.name} on all data ...")
            model.train(with_answers)

    # The newest example of each series is "today" for that series.
    newest = {}
    for ex in examples:
        if ex.series_number not in newest or ex.day > newest[ex.series_number].day:
            newest[ex.series_number] = ex
    latest_examples = list(newest.values())

    predictions_by_model = {}
    for model in models:
        if model.name in needed:
            predictions_by_model[model.name] = model.predict(latest_examples)

    forecasts = []
    for i, ex in enumerate(latest_examples):
        series = series_list[ex.series_number]
        for h in range(FORECAST_DAYS):
            model_name = best_for_day[h]
            change = predictions_by_model[model_name][i][h]
            forecasts.append(
                Forecast(
                    commodity=series.commodity,
                    state=series.state,
                    market_id=series.market_id,  # None for India / state series
                    model=model_name,
                    origin=ex.day,
                    target=ex.day + timedelta(days=h + 1),
                    yhat=round(ex.price_today * math.exp(change), 2),
                    lo=round(ex.price_today * math.exp(change + band_low[h]), 2),
                    hi=round(ex.price_today * math.exp(change + band_high[h]), 2),
                )
            )

    # ---- 5. accuracy rows (saved by run()) and a readable summary
    scores = []
    for name, result in results.items():
        for h, s in enumerate(result["scores"]):
            scores.append(
                dict(level=level, model=name, horizon=h + 1, mae=round(s["mae"], 2),
                     mape=round(s["mape"], 3), n=s["n"], is_live=(best_for_day[h] == name))
            )
    report = {
        "series": len(series_list),
        "forecasts_published": len(latest_examples),
        "train_examples": len(train),
        "test_examples": len(test),
        "best_model_per_day": best_for_day,
        "average_error": {
            name: {
                "mae": round(float(np.mean([s["mae"] for s in r["scores"]])), 1),
                "mape": round(float(np.mean([s["mape"] for s in r["scores"]])), 2),
            }
            for name, r in results.items()
        },
        "by_day": {name: [round(s["mape"], 2) for s in r["scores"]] for name, r in results.items()},
    }
    return scores, forecasts, report


def run(commodities=None, log=print, mandis=True):
    """Forecast India + states, then (optionally) single mandis, and save everything together."""
    state_series = load_series(commodities)
    if not state_series:
        raise ValueError("No series with enough history yet")
    scores, forecasts, state_report = forecast_level(state_series, ModelScore.STATE, log)
    report = {"state": state_report}

    if mandis:
        mandi_series = load_mandi_series(commodities)
        if mandi_series:
            m_scores, m_forecasts, report["mandi"] = forecast_level(
                mandi_series, ModelScore.MANDI, log, max_train=MAX_MANDI_TRAIN_EXAMPLES
            )
            scores += m_scores
            forecasts += m_forecasts

    # ---- save scores and forecasts (replace the old ones in one go)
    now = timezone.now()
    with transaction.atomic():
        ModelScore.objects.all().delete()
        ModelScore.objects.bulk_create([ModelScore(trained_at=now, **row) for row in scores])
        Forecast.objects.all().delete()
        Forecast.objects.bulk_create(forecasts, batch_size=2000)

    report["trained_at"] = now.isoformat()
    settings.ARTIFACTS_DIR.mkdir(exist_ok=True)
    (settings.ARTIFACTS_DIR / "forecast_report.json").write_text(json.dumps(report, indent=2))
    return report
