"""
Find mandi prices that look unusual.

For every mandi on every day we ask two questions:

  1. Is it far from what other mandis in the same state charged that day?
  2. Is it far from what THIS mandi charged over its last 14 reports?

Both answers become numbers (features). An Isolation Forest then looks at
all of them together and picks out the rare, odd-looking ones.

How an Isolation Forest works, in one line: it splits the data randomly
again and again; normal points need many splits to be separated, odd
points get isolated after only a few. Few splits = high anomaly score.

To keep flags meaningful we only flag a price if the forest finds it odd
AND it is at least 50% away from the state price or the mandi's own
recent price. Each flag gets a plain-English reason for the map.
"""

import math
from collections import defaultdict
from statistics import median

import numpy as np
from django.db import transaction
from sklearn.ensemble import IsolationForest

from prices.models import AggStateDaily, FactPrice

EXPECTED_ANOMALY_SHARE = 0.01  # tell the forest roughly 1 in 100 prices is odd
MIN_DIFFERENCE = math.log(1.5)  # 50% higher, or 33% lower (1 / 1.5)
OWN_HISTORY_REPORTS = 14
RANDOM_SEED = 7


def load_market_days(commodity):
    """
    One entry per (mandi, day). A mandi may report several varieties of the
    same commodity on one day; we use the middle (median) price of those.
    """
    rows = (
        FactPrice.objects.filter(commodity__commodity=commodity)
        .values_list("id", "market_id", "market__state", "date__date", "modal_price", "min_price", "max_price")
        .order_by("market_id", "date__date")
    )
    grouped = defaultdict(lambda: {"fact_ids": [], "modal": [], "min": [], "max": []})
    for fact_id, market_id, state, day, modal, low, high in rows:
        entry = grouped[(market_id, day)]
        entry["state"] = state
        entry["fact_ids"].append(fact_id)
        entry["modal"].append(float(modal))
        entry["min"].append(float(low))
        entry["max"].append(float(high))

    market_days = []
    for (market_id, day), e in grouped.items():
        market_days.append(
            {
                "market_id": market_id,
                "day": day,
                "state": e["state"],
                "fact_ids": e["fact_ids"],
                "price": median(e["modal"]),
                "low": min(e["min"]),
                "high": max(e["max"]),
            }
        )
    return market_days


def add_features(market_days, commodity):
    """Work out 'vs state' and 'vs own history' for every mandi-day. Returns the usable ones."""
    state_price = {
        (a.state, a.date): a.median_modal for a in AggStateDaily.objects.filter(commodity=commodity)
    }

    # market_days is sorted by mandi then day, so we can walk each mandi's history in order
    recent_prices = defaultdict(list)
    usable = []
    for md in market_days:
        typical = state_price.get((md["state"], md["day"]))
        if typical is None:
            continue  # summary not built for this day yet; skip rather than guess
        md["vs_state"] = math.log(md["price"] / typical)

        history = recent_prices[md["market_id"]]
        if len(history) >= 5:  # need a few past reports for this to mean anything
            md["vs_own"] = math.log(md["price"] / median(history))
        else:
            md["vs_own"] = 0.0
        history.append(md["price"])
        if len(history) > OWN_HISTORY_REPORTS:
            history.pop(0)

        # How wide was the day's trading range? Very wide ranges are suspicious too.
        md["spread"] = math.log(md["high"] / md["low"]) if md["low"] > 0 else 0.0
        usable.append(md)
    return usable


def describe(md):
    """Plain-English reason shown on the map, e.g. '62% above state price; 2.1x its usual'."""
    parts = []
    if abs(md["vs_state"]) >= MIN_DIFFERENCE:
        pct = (math.exp(md["vs_state"]) - 1) * 100
        parts.append(f"{abs(pct):.0f}% {'above' if pct > 0 else 'below'} the state's typical price")
    if abs(md["vs_own"]) >= MIN_DIFFERENCE:
        ratio = math.exp(md["vs_own"])
        if ratio > 1:
            parts.append(f"{ratio:.1f}x this mandi's recent price")
        else:
            parts.append(f"{(1 - ratio) * 100:.0f}% below this mandi's recent price")
    return "; ".join(parts)[:160]


def detect(commodity, log=print):
    market_days = load_market_days(commodity)
    if len(market_days) < 500:
        log(f"{commodity}: not enough data ({len(market_days)} mandi-days)")
        return 0
    market_days = add_features(market_days, commodity)

    features = np.array([[md["vs_state"], md["vs_own"], md["spread"]] for md in market_days])
    forest = IsolationForest(
        n_estimators=200, contamination=EXPECTED_ANOMALY_SHARE, random_state=RANDOM_SEED, n_jobs=-1
    )
    forest.fit(features)
    is_odd = forest.predict(features) == -1  # scikit-learn uses -1 for "anomaly"
    scores = -forest.score_samples(features)  # flip the sign so higher = more unusual

    flagged = []
    for md, odd, score in zip(market_days, is_odd, scores):
        big_enough = abs(md["vs_state"]) >= MIN_DIFFERENCE or abs(md["vs_own"]) >= MIN_DIFFERENCE
        if odd and big_enough:
            flagged.append((md, float(score)))

    # Save: clear old flags for this commodity, then set the new ones.
    with transaction.atomic():
        FactPrice.objects.filter(commodity__commodity=commodity, is_anomaly=True).update(
            is_anomaly=False, anomaly_score=None, anomaly_reason=""
        )
        for md, score in flagged:
            FactPrice.objects.filter(id__in=md["fact_ids"]).update(
                is_anomaly=True, anomaly_score=round(score, 3), anomaly_reason=describe(md)
            )

    log(f"{commodity}: {len(flagged)} unusual prices out of {len(market_days)} mandi-days "
        f"({len(flagged) / len(market_days) * 100:.2f}%)")
    return len(flagged)
