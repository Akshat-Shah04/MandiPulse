"""
Client and loader for Agmarknet prices published on data.gov.in.

API:   GET https://api.data.gov.in/resource/35985678-0d79-46b4-9ed6-6f13308a1d24
Query: api-key, format=json, offset, limit,
       filters[State], filters[District], filters[Commodity], filters[Arrival_Date]
Licence: Government Open Data License - India
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterator

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DimCommodity, DimDate, DimMarket, FactPrice, IngestRun, RawRecord

log = logging.getLogger(__name__)

BASE_URL = "https://api.data.gov.in/resource/{resource_id}"
PAGE_SIZE = 1000


class AgmarknetError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "mandipulse-research-client/1.0"
    return s


def fetch_pages(
    *,
    arrival_date: date | None = None,
    commodity: str | None = None,
    state: str | None = None,
    api_key: str | None = None,
    page_size: int = PAGE_SIZE,
    max_retries: int = 4,
) -> Iterator[list[dict]]:
    """Yield pages of raw records, following offset pagination until exhausted."""
    key = api_key or settings.DATA_GOV_IN_API_KEY
    if not key:
        raise AgmarknetError("DATA_GOV_IN_API_KEY is not set (see backend/.env.example)")

    url = BASE_URL.format(resource_id=settings.AGMARKNET_RESOURCE_ID)
    params = {"api-key": key, "format": "json", "limit": page_size, "offset": 0}
    if arrival_date:
        # The portal stores dates as DD/MM/YYYY strings.
        params["filters[Arrival_Date]"] = arrival_date.strftime("%d/%m/%Y")
    if commodity:
        params["filters[Commodity]"] = commodity
    if state:
        params["filters[State]"] = state

    session = _session()
    while True:
        for attempt in range(max_retries):
            try:
                resp = session.get(url, params=params, timeout=60)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                body = resp.json()
                break
            except (requests.RequestException, ValueError) as exc:
                wait = 2 ** attempt
                log.warning("Agmarknet request failed (%s); retry in %ss", exc, wait)
                time.sleep(wait)
        else:
            raise AgmarknetError(f"Giving up after {max_retries} attempts: {params | {'api-key': '***'}}")

        records = body.get("records", [])
        if not records:
            return
        yield records
        if len(records) < page_size:
            return
        params["offset"] += page_size


# --------------------------------------------------------------------------- cleaning


@dataclass
class CleanRow:
    arrival_date: date
    state: str
    district: str
    market: str
    commodity: str
    variety: str
    grade: str
    min_price: Decimal
    max_price: Decimal
    modal_price: Decimal


def _pick(rec: dict, *names: str) -> str:
    """Field names vary in case between resources (State vs state)."""
    for n in names:
        for k in (n, n.lower(), n.replace("_", " ")):
            if k in rec and rec[k] not in (None, ""):
                return str(rec[k]).strip()
    return ""


def _norm_text(s: str) -> str:
    return " ".join(s.split())


# Rs 100 per quintal = Rs 1 per kg. Even in a glut, onion, tomato and potato
# rarely sell below that. Lower figures in the feed are typos (Rs 0.01/quintal)
# or Rs/kg prices typed into the Rs/quintal column (tomato "15" = Rs 15/kg).
MIN_PLAUSIBLE_PRICE = Decimal("100")
# Rs 1 crore per quintal = Rs 1 lakh per kg. Seen in the feed: tomato at
# Rs 27 crore/quintal. Nothing we track trades anywhere near this.
MAX_PLAUSIBLE_PRICE = Decimal("10000000")
# A min or max more than 10x away from the modal price is a typo
# (e.g. "30002800" where "3000" and "2800" got run together).
MAX_RATIO_TO_MODAL = Decimal("10")

# Agmarknet spells some states two ways; fold them so the map joins cleanly.
STATE_ALIASES = {
    "Uttrakhand": "Uttarakhand",
    "Chattisgarh": "Chhattisgarh",
    "Pondicherry": "Puducherry",
    "Keralam": "Kerala",
    "NCT of Delhi": "Delhi",
    "Andaman and Nicobar": "Andaman and Nicobar Islands",
}


def clean(rec: dict) -> tuple[CleanRow | None, str]:
    """Validate one API record. Returns (row, "") or (None, reason)."""
    raw_date = _pick(rec, "Arrival_Date")
    try:
        d = datetime.strptime(raw_date, "%d/%m/%Y").date()
    except ValueError:
        return None, f"bad date {raw_date!r}"

    try:
        lo = Decimal(_pick(rec, "Min_Price") or "nan")
        hi = Decimal(_pick(rec, "Max_Price") or "nan")
        modal = Decimal(_pick(rec, "Modal_Price") or "nan")
    except InvalidOperation:
        return None, "non-numeric price"
    if any(p.is_nan() for p in (lo, hi, modal)):
        return None, "missing price"
    if modal < MIN_PLAUSIBLE_PRICE:
        # e.g. Rs 0.01/quintal appears in the source; no mandi trades that low.
        return None, "implausibly low modal price"
    if modal > MAX_PLAUSIBLE_PRICE:
        return None, "implausibly high modal price"
    if hi > modal * MAX_RATIO_TO_MODAL:
        hi = modal  # typo in max price: fall back to the modal price
    if lo > 0 and lo < modal / MAX_RATIO_TO_MODAL:
        lo = modal  # typo in min price
    if lo > hi:
        lo, hi = hi, lo  # swapped columns happen in the source; keep the row
    # Modal outside [min, max] is a data-entry slip; clamp rather than drop.
    modal = min(max(modal, lo), hi) if lo > 0 else modal

    state = _norm_text(_pick(rec, "State"))
    state = STATE_ALIASES.get(state, state)
    row = CleanRow(
        arrival_date=d,
        state=state,
        district=_norm_text(_pick(rec, "District")),
        market=_norm_text(_pick(rec, "Market")),
        commodity=_norm_text(_pick(rec, "Commodity")),
        variety=_norm_text(_pick(rec, "Variety")) or "Other",
        grade=_norm_text(_pick(rec, "Grade")) or "FAQ",
        min_price=lo,
        max_price=hi,
        modal_price=modal,
    )
    if not (row.state and row.district and row.market and row.commodity):
        return None, "missing location or commodity"
    return row, ""


# --------------------------------------------------------------------------- loading


def _dimension_map(model, fields: tuple[str, ...], keys: set[tuple]) -> dict[tuple, int]:
    """Make sure every key exists in a dimension table; return key -> id."""
    if not keys:
        return {}
    model.objects.bulk_create(
        [model(**dict(zip(fields, k))) for k in keys], ignore_conflicts=True, batch_size=500
    )
    # Fetch ids back; filter on the first field to keep the query small.
    first_values = {k[0] for k in keys}
    rows = model.objects.filter(**{f"{fields[0]}__in": first_values}).values_list("id", *fields)
    return {tuple(r[1:]): r[0] for r in rows}


def load_records(records: list[dict], query: dict) -> tuple[int, int]:
    """Store raw rows, then bulk-upsert cleaned rows into the star schema."""
    cleaned: list[CleanRow] = []
    raws: list[RawRecord] = []
    for rec in records:
        row, reason = clean(rec)
        raws.append(RawRecord(payload=rec, query=query, loaded=row is not None, reject_reason=reason))
        if row is not None:
            cleaned.append(row)

    # The source occasionally repeats a row within a page; last one wins.
    unique: dict[tuple, CleanRow] = {}
    for r in cleaned:
        unique[(r.arrival_date, r.state, r.district, r.market, r.commodity, r.variety, r.grade)] = r

    with transaction.atomic():
        RawRecord.objects.bulk_create(raws, batch_size=1000)

        dates = {r.arrival_date: DimDate.for_date(r.arrival_date) for r in unique.values()}
        markets = _dimension_map(
            DimMarket, ("state", "district", "market"),
            {(r.state, r.district, r.market) for r in unique.values()},
        )
        commodities = _dimension_map(
            DimCommodity, ("commodity", "variety", "grade"),
            {(r.commodity, r.variety, r.grade) for r in unique.values()},
        )
        facts = [
            FactPrice(
                date=dates[r.arrival_date],
                market_id=markets[(r.state, r.district, r.market)],
                commodity_id=commodities[(r.commodity, r.variety, r.grade)],
                min_price=r.min_price,
                max_price=r.max_price,
                modal_price=r.modal_price,
            )
            for r in unique.values()
        ]
        FactPrice.objects.bulk_create(
            facts,
            batch_size=1000,
            update_conflicts=True,
            unique_fields=["date", "market", "commodity"],
            update_fields=["min_price", "max_price", "modal_price"],
        )
    return len(facts), len(records) - len(cleaned)


def ingest(*, arrival_date=None, commodity=None, state=None) -> IngestRun:
    params = {
        "arrival_date": arrival_date.isoformat() if arrival_date else None,
        "commodity": commodity,
        "state": state,
    }
    run = IngestRun.objects.create(params=params)
    try:
        for page in fetch_pages(arrival_date=arrival_date, commodity=commodity, state=state):
            run.rows_fetched += len(page)
            ok, bad = load_records(page, params)
            run.rows_loaded += ok
            run.rows_rejected += bad
    except Exception as exc:  # recorded on the run, then re-raised
        run.error = str(exc)
        raise
    finally:
        run.finished_at = timezone.now()
        run.save()
    return run
