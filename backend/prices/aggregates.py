"""
Roll FactPrice up to daily state and national summaries.

Medians (not means) because one mandi reporting a typo price should not move
a state's figure. Rebuilding a date range is idempotent.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from django.db import transaction

from .models import AggNationalDaily, AggStateDaily, FactPrice


def _facts(since: date | None, until: date | None, commodity: str | None) -> pd.DataFrame:
    qs = FactPrice.objects.all()
    if since:
        qs = qs.filter(date__date__gte=since)
    if until:
        qs = qs.filter(date__date__lte=until)
    if commodity:
        qs = qs.filter(commodity__commodity=commodity)
    rows = qs.values_list(
        "date__date", "commodity__commodity", "market__state", "market_id",
        "modal_price", "min_price", "max_price",
    )
    df = pd.DataFrame.from_records(
        rows, columns=["date", "commodity", "state", "market", "modal", "lo", "hi"]
    )
    for c in ("modal", "lo", "hi"):
        df[c] = df[c].astype(float)
    return df


def rebuild(
    since: date | None = None, until: date | None = None, commodity: str | None = None
) -> tuple[int, int]:
    df = _facts(since, until, commodity)
    if df.empty:
        return 0, 0

    # A market can report several varieties of one commodity on a day;
    # collapse to one price per market first so big mandis don't count twice.
    per_market = df.groupby(["date", "commodity", "state", "market"], as_index=False).agg(
        modal=("modal", "median"), lo=("lo", "min"), hi=("hi", "max")
    )

    g = per_market.groupby(["date", "commodity", "state"])
    st = g["modal"].quantile([0.25, 0.5, 0.75]).unstack()
    st.columns = ["p25", "median", "p75"]
    st["lo"] = g["lo"].min()
    st["hi"] = g["hi"].max()
    st["markets"] = g.size()
    st = st.reset_index()

    gn = per_market.groupby(["date", "commodity"])
    nat = gn["modal"].quantile([0.25, 0.5, 0.75]).unstack()
    nat.columns = ["p25", "median", "p75"]
    nat["markets"] = gn.size()
    nat["states"] = gn["state"].nunique()
    nat = nat.reset_index()

    keys = {(r.date, r.commodity) for r in nat.itertuples()}
    with transaction.atomic():
        for d, c in keys:
            AggStateDaily.objects.filter(date=d, commodity=c).delete()
            AggNationalDaily.objects.filter(date=d, commodity=c).delete()
        AggStateDaily.objects.bulk_create(
            [
                AggStateDaily(
                    date=r.date, commodity=r.commodity, state=r.state,
                    median_modal=round(r.median, 2), p25_modal=round(r.p25, 2),
                    p75_modal=round(r.p75, 2), min_price=r.lo, max_price=r.hi,
                    markets=int(r.markets),
                )
                for r in st.itertuples()
            ],
            batch_size=2000,
        )
        AggNationalDaily.objects.bulk_create(
            [
                AggNationalDaily(
                    date=r.date, commodity=r.commodity,
                    median_modal=round(r.median, 2), p25_modal=round(r.p25, 2),
                    p75_modal=round(r.p75, 2), markets=int(r.markets), states=int(r.states),
                )
                for r in nat.itertuples()
            ],
            batch_size=2000,
        )
    return len(st), len(nat)
