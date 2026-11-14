"""
Step 1 of forecasting: turn the database into simple daily price lists.

A "series" is the typical (median) price of ONE commodity in ONE state, for
every day in order. Example: Onion in Maharashtra, 1 Apr -> 11 Nov.
We also make one all-India series per commodity (state = "").

Mandi series (one commodity at ONE mandi) are built separately, and only for
mandis that report most days. A mandi that reports twice a week leaves too
many gaps to forecast honestly.

Mandis are closed on Sundays and holidays, so some days have no price.
If the gap is short (7 days or less) we copy the last known price forward,
so the models always get a complete list. We remember which days were
copied, because we must never "grade" a forecast against a copied price.
"""

import math
from datetime import timedelta

import pandas as pd
from django.db.models import Max

from prices.models import AggNationalDaily, AggStateDaily, FactPrice

# A series needs at least this many real days of data to be useful.
MIN_DAYS_WITH_DATA = 90

# Skip states where usually only one mandi reports - one mandi is too noisy.
MIN_MANDIS_PER_DAY = 2

# Fill gaps of up to this many days by copying the last price forward.
MAX_GAP_TO_FILL = 7

# Mandi forecasts look at the last 180 days, and a mandi must have reported on
# at least 120 of them (about 5 days a week) to get a forecast.
MANDI_RECENT_DAYS = 180
MIN_MANDI_REPORTING_DAYS = 120


class PriceSeries:
    """Daily prices for one commodity in one state (or at one mandi)."""

    def __init__(self, commodity, state, dates, prices, is_real, market_id=None):
        self.commodity = commodity  # e.g. "Onion"
        self.state = state  # e.g. "Maharashtra", or "" for all India
        self.market_id = market_id  # set only for a mandi series
        self.dates = dates  # list of datetime.date, one per day, no gaps
        self.prices = prices  # list of float (Rs per quintal), or None if unknown
        self.is_real = is_real  # list of bool: True if a mandi really reported that day

    def name(self):
        return f"{self.commodity} / {self.state or 'All India'}"


def read_daily_medians(commodities=None):
    """Read state-level and all-India daily medians into one table."""
    state_rows = AggStateDaily.objects.all()
    india_rows = AggNationalDaily.objects.all()
    if commodities:
        state_rows = state_rows.filter(commodity__in=commodities)
        india_rows = india_rows.filter(commodity__in=commodities)

    states = pd.DataFrame(list(state_rows.values("commodity", "state", "date", "median_modal", "markets")))
    india = pd.DataFrame(list(india_rows.values("commodity", "date", "median_modal", "markets")))
    india["state"] = ""  # empty state name means "all India"
    return pd.concat([states, india], ignore_index=True)


def build_one_series(commodity, state, rows):
    """Turn the rows for one commodity+state into a PriceSeries with no missing days."""
    rows = rows.sort_values("date")
    price_on = dict(zip(rows["date"], rows["median_modal"]))

    first_day = rows["date"].min()
    last_day = rows["date"].max()
    all_days = [d.date() for d in pd.date_range(first_day, last_day, freq="D")]

    prices = []
    is_real = []
    last_price = None
    days_since_real = 0
    for day in all_days:
        if day in price_on:
            last_price = float(price_on[day])
            days_since_real = 0
            prices.append(last_price)
            is_real.append(True)
        else:
            days_since_real += 1
            if last_price is not None and days_since_real <= MAX_GAP_TO_FILL:
                prices.append(last_price)  # copy the last known price forward
            else:
                prices.append(None)  # gap too long, leave it unknown
            is_real.append(False)

    return PriceSeries(commodity, state, all_days, prices, is_real)


def load_series(commodities=None):
    """Return a list of PriceSeries that have enough data to forecast."""
    table = read_daily_medians(commodities)
    if table.empty:
        return []

    result = []
    for (commodity, state), rows in table.groupby(["commodity", "state"]):
        if len(rows) < MIN_DAYS_WITH_DATA:
            continue
        if rows["markets"].median() < MIN_MANDIS_PER_DAY:
            continue
        result.append(build_one_series(commodity, state, rows))
    return result


def read_mandi_prices(commodities=None):
    """Each mandi's daily price over the last MANDI_RECENT_DAYS days, as a table."""
    facts = FactPrice.objects.all()
    if commodities:
        facts = facts.filter(commodity__commodity__in=commodities)
    last_day = facts.aggregate(d=Max("date__date"))["d"]
    if last_day is None:
        return pd.DataFrame()

    first_day = last_day - timedelta(days=MANDI_RECENT_DAYS - 1)
    rows = facts.filter(date__date__gte=first_day).values_list(
        "commodity__commodity", "market_id", "market__state", "date__date", "modal_price"
    )
    table = pd.DataFrame(list(rows), columns=["commodity", "market_id", "state", "date", "median_modal"])
    table["median_modal"] = table["median_modal"].astype(float)
    # Some mandis report several varieties on one day; use their median, as the price chart does.
    return table.groupby(["commodity", "market_id", "state", "date"], as_index=False)["median_modal"].median()


def load_mandi_series(commodities=None):
    """Return one PriceSeries per (commodity, mandi) that reports often enough to forecast."""
    table = read_mandi_prices(commodities)
    if table.empty:
        return []

    result = []
    for (commodity, market_id, state), rows in table.groupby(["commodity", "market_id", "state"]):
        if len(rows) < MIN_MANDI_REPORTING_DAYS:
            continue  # too many missing days for an honest forecast
        series = build_one_series(commodity, state, rows)
        series.market_id = int(market_id)
        result.append(series)
    return result


def log_price(price):
    """We work with log(price) so a Rs 100 rise means more for a Rs 500 item than a Rs 5000 one."""
    return math.log(price)
