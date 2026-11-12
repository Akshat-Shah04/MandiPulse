"""
Read-only API for the map and charts.

    GET /api/commodities                              commodities with data
    GET /api/dates?commodity=Onion                    days with data (timeline)
    GET /api/map?commodity=Onion[&date=YYYY-MM-DD]    state + market prices for one day
    GET /api/series?commodity=Onion[&state=..][&market_id=..][&days=365]
                                                      daily median modal price
    GET /api/status                                   freshness of the warehouse
    GET /api/forecast?commodity=Onion[&state=..]      next 14 days with an 80% band
    GET /api/models                                   back-test accuracy of each model
    GET /api/groups?commodity=Onion                   mandi behaviour groups (K-means) + PCA points

State and national figures come from the AggStateDaily / AggNationalDaily
summary tables, so each request reads hundreds of rows, not millions.
Prices are Rs per quintal (100 kg).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from django.db.models import Count, Max, Min, Sum
from django.views.decorators.cache import cache_page
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from .models import (
    AggNationalDaily,
    AggStateDaily,
    FactPrice,
    Forecast,
    IngestRun,
    MarketGroup,
    MarketGroupMember,
    ModelScore,
)

CACHE_SECONDS = 60


def _commodity_param(request) -> str:
    name = request.query_params.get("commodity", "").strip()
    if not name:
        raise ValidationError({"commodity": "required"})
    if not AggNationalDaily.objects.filter(commodity=name).exists():
        raise NotFound(f"No data for commodity {name!r}")
    return name


def _date_param(request, name="date") -> date | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError({name: "use YYYY-MM-DD"}) from exc


def _r(x) -> float:
    return round(float(x), 2)


def _date_key(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


@cache_page(CACHE_SECONDS)
@api_view(["GET"])
def commodities(request):
    rows = (
        AggNationalDaily.objects.values("commodity")
        .annotate(days=Count("id"), first=Min("date"), last=Max("date"), reports=Sum("markets"))
        .order_by("-reports")
    )
    # Mandis reporting on each commodity's latest day (a handful of lookups).
    latest = {
        r["commodity"]: AggNationalDaily.objects.get(commodity=r["commodity"], date=r["last"]).markets
        for r in rows
    }
    return Response(
        [
            {
                "commodity": r["commodity"],
                "days": r["days"],
                "markets": latest.get(r["commodity"], 0),
                "first_date": r["first"],
                "last_date": r["last"],
            }
            for r in rows
        ]
    )


@cache_page(CACHE_SECONDS)
@api_view(["GET"])
def dates(request):
    """Days that have prices for a commodity, for the timeline scrubber."""
    name = _commodity_param(request)
    rows = AggNationalDaily.objects.filter(commodity=name).order_by("date").values("date", "markets")
    return Response([{"date": r["date"], "markets": r["markets"]} for r in rows])


@api_view(["GET"])
def price_map(request):
    """
    Prices for one commodity on one day, ready for a choropleth.

    State value = median of market modal prices (robust to one odd mandi).
    Change = vs the same state ~7 days earlier, when that day has data.
    """
    name = _commodity_param(request)
    nat_qs = AggNationalDaily.objects.filter(commodity=name)
    day = _date_param(request) or nat_qs.aggregate(d=Max("date"))["d"]
    nat = nat_qs.filter(date=day).first()
    if nat is None:
        raise NotFound(f"No {name} prices on {day}")

    states_today = list(AggStateDaily.objects.filter(commodity=name, date=day))
    week_ago = defaultdict(list)
    for s in AggStateDaily.objects.filter(
        commodity=name, date__range=(day - timedelta(days=9), day - timedelta(days=5))
    ).values("state", "median_modal"):
        week_ago[s["state"]].append(s["median_modal"])

    states = []
    for s in states_today:
        prev = median(week_ago[s.state]) if week_ago[s.state] else None
        states.append(
            {
                "state": s.state,
                "median_modal": s.median_modal,
                "min": s.min_price,
                "max": s.max_price,
                "markets": s.markets,
                "change_7d_pct": _r((s.median_modal - prev) / prev * 100) if prev else None,
            }
        )
    states.sort(key=lambda s: s["median_modal"], reverse=True)

    # One dot per mandi: collapse several varieties to the market's median.
    per_market: dict[int, dict] = {}
    rows = FactPrice.objects.filter(date_id=_date_key(day), commodity__commodity=name).values_list(
        "market_id", "market__market", "market__district", "market__state",
        "market__latitude", "market__longitude", "min_price", "max_price", "modal_price", "is_anomaly",
        "anomaly_reason",
    )
    for mid, mk, dist, st, lat, lon, lo, hi, modal, anom, reason in rows:
        m = per_market.setdefault(
            mid,
            {"id": mid, "market": mk, "district": dist, "state": st, "lat": lat, "lon": lon,
             "_modal": [], "min": float(lo), "max": float(hi), "anomaly": False, "reason": ""},
        )
        m["_modal"].append(float(modal))
        m["min"] = min(m["min"], float(lo))
        m["max"] = max(m["max"], float(hi))
        if anom:
            m["anomaly"] = True
            m["reason"] = reason
    markets = []
    for m in per_market.values():
        m["modal"] = _r(median(m.pop("_modal")))
        markets.append(m)

    return Response(
        {
            "commodity": name,
            "date": day,
            "unit": "Rs/quintal",
            "national_median": nat.median_modal,
            "national_p25": nat.p25_modal,
            "national_p75": nat.p75_modal,
            "states": states,
            "markets": markets,
        }
    )


@api_view(["GET"])
def series(request):
    name = _commodity_param(request)
    try:
        days = min(int(request.query_params.get("days", 365)), 3650)
    except ValueError as exc:
        raise ValidationError({"days": "integer"}) from exc

    state = request.query_params.get("state")
    market_id = request.query_params.get("market_id")

    if market_id:
        facts = FactPrice.objects.filter(commodity__commodity=name, market_id=market_id)
        last = facts.aggregate(d=Max("date__date"))["d"]
        if last is None:
            raise NotFound("No data for that market")
        per_day = defaultdict(list)
        for d, p in facts.filter(date__date__gte=last - timedelta(days=days)).values_list(
            "date__date", "modal_price"
        ):
            per_day[d].append(float(p))
        points = [{"date": d, "median_modal": _r(median(v)), "markets": 1} for d, v in sorted(per_day.items())]
    else:
        qs = (
            AggStateDaily.objects.filter(commodity=name, state=state)
            if state
            else AggNationalDaily.objects.filter(commodity=name)
        )
        last = qs.aggregate(d=Max("date"))["d"]
        if last is None:
            raise NotFound("No data for that selection")
        points = [
            {"date": r["date"], "median_modal": r["median_modal"], "markets": r["markets"]}
            for r in qs.filter(date__gte=last - timedelta(days=days))
            .order_by("date")
            .values("date", "median_modal", "markets")
        ]
    return Response({"commodity": name, "unit": "Rs/quintal", "points": points})


@cache_page(CACHE_SECONDS)
@api_view(["GET"])
def status(request):
    last_run = IngestRun.objects.filter(error="").order_by("-finished_at").first()
    return Response(
        {
            "facts": AggNationalDaily.objects.aggregate(n=Sum("markets"))["n"] or 0,
            "latest_price_date": AggNationalDaily.objects.aggregate(d=Max("date"))["d"],
            "last_successful_ingest": last_run.finished_at if last_run else None,
            "source": "Agmarknet via data.gov.in (Government Open Data License - India)",
        }
    )


@api_view(["GET"])
def forecast(request):
    """Published 14-day forecast for India (no state) or one state."""
    name = _commodity_param(request)
    state = request.query_params.get("state", "")
    rows = Forecast.objects.filter(commodity=name, state=state).order_by("target")
    if not rows:
        # Small states don't have enough mandis for a reliable forecast.
        return Response({"commodity": name, "state": state, "available": False, "points": []})
    return Response(
        {
            "commodity": name,
            "state": state,
            "available": True,
            "origin": rows[0].origin,
            "points": [
                {"date": r.target, "yhat": r.yhat, "lo": r.lo, "hi": r.hi, "model": r.model} for r in rows
            ],
        }
    )


@cache_page(CACHE_SECONDS)
@api_view(["GET"])
def model_scores(request):
    """How accurate each model was in the back-test, overall and day by day."""
    scores = list(ModelScore.objects.order_by("model", "horizon"))
    if not scores:
        return Response({"trained_at": None, "models": []})

    by_model = defaultdict(list)
    for s in scores:
        by_model[s.model].append(s)

    models = []
    for model_name, rows in by_model.items():
        models.append(
            {
                "model": model_name,
                "mape": round(sum(r.mape for r in rows) / len(rows), 2),
                "mae": round(sum(r.mae for r in rows) / len(rows), 1),
                "used_for_days": [r.horizon for r in rows if r.is_live],
                "mape_by_day": [r.mape for r in rows],
            }
        )
    models.sort(key=lambda m: m["mape"])
    return Response({"trained_at": scores[0].trained_at, "models": models})


@cache_page(CACHE_SECONDS)
@api_view(["GET"])
def groups(request):
    """Behaviour groups of mandis for one commodity, and each mandi's dot on the PCA chart."""
    name = _commodity_param(request)
    group_rows = MarketGroup.objects.filter(commodity=name).order_by("number")
    members = MarketGroupMember.objects.filter(commodity=name).select_related("market")
    return Response(
        {
            "commodity": name,
            "groups": [
                {
                    "number": g.number,
                    "label": g.label,
                    "description": g.description,
                    "size": g.size,
                    "price_vs_india_pct": g.price_vs_india_pct,
                    "weekly_swing_pct": g.weekly_swing_pct,
                    "trend_pct_per_week": g.trend_pct_per_week,
                    "follows_india": g.follows_india,
                }
                for g in group_rows
            ],
            "members": [
                {
                    "market_id": m.market_id,
                    "market": m.market.market,
                    "state": m.market.state,
                    "group": m.group_number,
                    "x": m.pca_x,
                    "y": m.pca_y,
                }
                for m in members
            ],
        }
    )
