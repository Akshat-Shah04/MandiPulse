"""
Group mandis that behave alike (market segmentation).

For each mandi we describe its last 26 weeks with four numbers:

  1. price_vs_india   Is it usually cheaper or dearer than the all-India price?
  2. weekly_swing     How much does its price jump around from week to week?
  3. trend            Has its price been rising or falling lately?
  4. follows_india    When the all-India price moves, does this mandi move too?

Then K-means puts mandis with similar numbers into the same group. We try
3, 4, 5 and 6 groups and keep the number with the best "silhouette score"
(how clearly separated the groups are: 1 = perfectly, 0 = overlapping).

Finally PCA squeezes the four numbers into two (x, y) so we can draw every
mandi as a dot on a flat chart - similar mandis land close together.
"""

import math
from collections import defaultdict
from datetime import timedelta
from statistics import median

import numpy as np
from django.db import transaction
from django.db.models import Max
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from prices.models import AggNationalDaily, FactPrice, MarketGroup, MarketGroupMember

WEEKS = 26  # look at roughly the last six months
MIN_WEEKS_WITH_DATA = 18  # a mandi must report in at least 18 of those weeks
TREND_WEEKS = 12  # "trend" uses the most recent 12 weeks
GROUP_COUNTS_TO_TRY = [3, 4, 5, 6]
RANDOM_SEED = 7


def week_start(day):
    """The Monday of the week a date falls in. Used to bucket days into weeks."""
    return day - timedelta(days=day.weekday())


def weekly_prices(commodity):
    """
    Returns:
      by_market: {market_id: {week_monday: median price that week}}
      india:     {week_monday: median all-India price that week}
      weeks:     the list of week Mondays we look at, oldest first
    """
    last_day = AggNationalDaily.objects.filter(commodity=commodity).aggregate(d=Max("date"))["d"]
    first_week = week_start(last_day) - timedelta(weeks=WEEKS - 1)
    weeks = [first_week + timedelta(weeks=i) for i in range(WEEKS)]

    # all daily mandi prices in the window, grouped into weeks
    daily = defaultdict(list)
    rows = FactPrice.objects.filter(commodity__commodity=commodity, date__date__gte=first_week).values_list(
        "market_id", "date__date", "modal_price"
    )
    for market_id, day, price in rows:
        daily[(market_id, week_start(day))].append(float(price))

    by_market = defaultdict(dict)
    for (market_id, week), prices in daily.items():
        by_market[market_id][week] = median(prices)

    india_daily = defaultdict(list)
    for row in AggNationalDaily.objects.filter(commodity=commodity, date__gte=first_week):
        india_daily[week_start(row.date)].append(row.median_modal)
    india = {week: median(prices) for week, prices in india_daily.items()}

    return by_market, india, weeks


def describe_market(prices, india, weeks):
    """Turn one mandi's weekly prices into the four behaviour numbers (or None if too little data)."""
    weeks_with_data = [w for w in weeks if w in prices and w in india]
    if len(weeks_with_data) < MIN_WEEKS_WITH_DATA:
        return None

    # 1. price vs India: average of log(mandi / India) over the weeks
    price_vs_india = np.mean([math.log(prices[w] / india[w]) for w in weeks_with_data])

    # 2 & 4. week-to-week changes, for the mandi and for India, on consecutive weeks only
    my_changes = []
    india_changes = []
    for previous, current in zip(weeks, weeks[1:]):
        if previous in prices and current in prices and previous in india and current in india:
            my_changes.append(math.log(prices[current] / prices[previous]))
            india_changes.append(math.log(india[current] / india[previous]))
    if len(my_changes) < 8:
        return None
    weekly_swing = float(np.std(my_changes))

    # correlation can't be computed if one side never changed at all
    if np.std(my_changes) == 0 or np.std(india_changes) == 0:
        follows_india = 0.0
    else:
        follows_india = float(np.corrcoef(my_changes, india_changes)[0, 1])

    # 3. trend: slope of a straight line through log(price) over the recent weeks
    recent = [w for w in weeks[-TREND_WEEKS:] if w in prices]
    if len(recent) >= 6:
        x = [(w - recent[0]).days / 7 for w in recent]  # weeks since the first point
        y = [math.log(prices[w]) for w in recent]
        trend = float(np.polyfit(x, y, 1)[0])  # log change per week
    else:
        trend = 0.0

    return [price_vs_india, weekly_swing, trend, follows_india]


def name_group(centre, typical_swing):
    """
    Make a human label from a group's centre, e.g. "Dear and volatile".
    centre = [price_vs_india, weekly_swing, trend, follows_india] in log units.
    """
    price_pct = (math.exp(centre[0]) - 1) * 100
    if price_pct < -10:
        price_word = "Cheap"
    elif price_pct > 10:
        price_word = "Dear"
    else:
        price_word = "Average price"

    if centre[1] < typical_swing * 0.8:
        swing_word = "steady"
    elif centre[1] > typical_swing * 1.25:
        swing_word = "volatile"
    else:
        swing_word = ""  # normal swings: not worth putting in the label

    trend_pct = (math.exp(centre[2]) - 1) * 100
    if trend_pct > 1.5:
        trend_text = f"rising about {trend_pct:.1f}% a week"
        trend_word = "rising"
    elif trend_pct < -1.5:
        trend_text = f"falling about {abs(trend_pct):.1f}% a week"
        trend_word = "falling"
    else:
        trend_text = "flat lately"
        trend_word = ""

    if centre[3] > 0.5:
        follow_text = "moves with the national price"
        follow_word = "follows India"
    elif centre[3] < 0.2:
        follow_text = "moves on its own"
        follow_word = "own path"
    else:
        follow_text = "partly follows the national price"
        follow_word = ""

    # Label = only the traits that stand out, e.g. "Cheap · volatile · own path"
    words = [price_word] + [w for w in (swing_word, follow_word) if w]
    label = " · ".join(words)
    description = (
        f"{abs(price_pct):.0f}% {'below' if price_pct < 0 else 'above'} India on average; "
        f"typical weekly swing {centre[1] * 100:.0f}%; {trend_text}; {follow_text}."
    )
    return label, description, trend_word


def cluster_commodity(commodity, log=print):
    by_market, india, weeks = weekly_prices(commodity)

    market_ids = []
    features = []
    for market_id, prices in by_market.items():
        numbers = describe_market(prices, india, weeks)
        if numbers is not None:
            market_ids.append(market_id)
            features.append(numbers)
    if len(features) < 30:
        log(f"{commodity}: only {len(features)} mandis with enough weeks - skipped")
        return
    features = np.array(features)

    # Put all four numbers on the same scale, otherwise "swing" (tiny numbers)
    # would count for less than "follows_india" (numbers near 1) in K-means.
    scaled = StandardScaler().fit_transform(features)

    # Try several group counts and keep the clearest one.
    best_k, best_score, best_labels, best_model = None, -1.0, None, None
    for k in GROUP_COUNTS_TO_TRY:
        model = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_SEED)
        labels = model.fit_predict(scaled)
        score = silhouette_score(scaled, labels)
        log(f"{commodity}: {k} groups -> silhouette {score:.3f}")
        if score > best_score:
            best_k, best_score, best_labels, best_model = k, score, labels, model

    # 2-D picture of the mandis for the scatter chart
    xy = PCA(n_components=2, random_state=RANDOM_SEED).fit_transform(scaled)

    typical_swing = float(np.median(features[:, 1]))
    groups = []
    used_labels = set()
    for number in range(best_k):
        members = features[best_labels == number]
        centre = members.mean(axis=0)  # average in the original (readable) units
        label, description, trend_word = name_group(centre, typical_swing)
        # Two groups can still get the same label; add the trend, then a number.
        if label in used_labels and trend_word:
            label = f"{label} · {trend_word}"
        if label in used_labels:
            label = f"{label} ({number + 1})"
        used_labels.add(label)
        groups.append(
            MarketGroup(
                commodity=commodity, number=number, label=label[:64], description=description[:200],
                size=len(members),
                price_vs_india_pct=round((math.exp(centre[0]) - 1) * 100, 1),
                weekly_swing_pct=round(centre[1] * 100, 1),
                trend_pct_per_week=round((math.exp(centre[2]) - 1) * 100, 2),
                follows_india=round(centre[3], 2),
            )
        )

    with transaction.atomic():
        MarketGroup.objects.filter(commodity=commodity).delete()
        MarketGroupMember.objects.filter(commodity=commodity).delete()
        MarketGroup.objects.bulk_create(groups)
        MarketGroupMember.objects.bulk_create(
            [
                MarketGroupMember(
                    commodity=commodity, market_id=market_id, group_number=int(group),
                    pca_x=round(float(point[0]), 3), pca_y=round(float(point[1]), 3),
                )
                for market_id, group, point in zip(market_ids, best_labels, xy)
            ],
            batch_size=1000,
        )

    log(f"{commodity}: {len(market_ids)} mandis in {best_k} groups (silhouette {best_score:.2f})")
    for g in groups:
        log(f"   [{g.number}] {g.label:<40} {g.size:>4} mandis  {g.description}")
