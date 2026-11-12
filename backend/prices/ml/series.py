"""
Step 1 of forecasting: turn the database into simple daily price lists.

A "series" is the typical (median) price of ONE commodity in ONE state, for
every day in order. Example: Onion in Maharashtra, 1 Apr -> 11 Nov.
We also make one all-India series per commodity (state = "").

Mandis are closed on Sundays and holidays, so some days have no price.
If the gap is short (7 days or less) we copy the last known price forward,
so the models always get a complete list. We remember which days were
copied, because we must never "grade" a forecast against a copied price.
"""

import math

import pandas as pd

from prices.models import AggNationalDaily, AggStateDaily

# A series needs at least this many real days of data to be useful.
MIN_DAYS_WITH_DATA = 90

# Skip states where usually only one mandi reports - one mandi is too noisy.
MIN_MANDIS_PER_DAY = 2

# Fill gaps of up to this many days by copying the last price forward.
MAX_GAP_TO_FILL = 7


class PriceSeries:
    """Daily prices for one commodity in one state."""

    def __init__(self, commodity, state, dates, prices, is_real):
        self.commodity = commodity  # e.g. "Onion"
        self.state = state  # e.g. "Maharashtra", or "" for all India
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


def log_price(price):
    """We work with log(price) so a Rs 100 rise means more for a Rs 500 item than a Rs 5000 one."""
    return math.log(price)
