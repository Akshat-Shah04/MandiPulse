"""
Place each mandi at its district's centre (Census-2011 polygons, DataMeet).

Agmarknet district names drift from census spellings ("Palakad" vs
"Palakkad") and some districts changed state after 2011 (Telangana, Ladakh),
so matching is: exact -> fuzzy within state -> fuzzy across India -> state centre.
A small deterministic offset keeps several markets in one district from
stacking on a single pixel.
"""

import csv
import hashlib
import json
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path

from django.conf import settings

GEO = Path(settings.BASE_DIR) / "data" / "geo"


def _key(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


@lru_cache(maxsize=1)
def _tables():
    by_state: dict[str, dict[str, tuple[float, float]]] = {}
    everywhere: dict[str, tuple[float, float]] = {}
    with (GEO / "district_centroids.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pt = (float(row["lat"]), float(row["lon"]))
            by_state.setdefault(_key(row["state"]), {})[_key(row["district"])] = pt
            everywhere.setdefault(_key(row["district"]), pt)
    states = {}
    for f in json.loads((GEO / "india_states.geojson").read_text())["features"]:
        p = f["properties"]
        states[_key(p["name"])] = (p["cy"], p["cx"])
    return by_state, everywhere, states


def _jitter(name: str, scale=0.06) -> tuple[float, float]:
    h = hashlib.md5(name.encode()).digest()
    return ((h[0] / 255 - 0.5) * scale, (h[1] / 255 - 0.5) * scale)


def locate(state: str, district: str, market: str) -> tuple[float, float, str] | None:
    """Return (lat, lon, how) or None when even the state is unknown."""
    by_state, everywhere, states = _tables()
    s, d = _key(state), _key(district)
    dj, mj = _jitter(market)

    in_state = by_state.get(s, {})
    if d in in_state:
        lat, lon = in_state[d]
        return lat + dj, lon + mj, "exact"
    if m := get_close_matches(d, in_state.keys(), n=1, cutoff=0.8):
        lat, lon = in_state[m[0]]
        return lat + dj, lon + mj, "fuzzy"
    if m := get_close_matches(d, everywhere.keys(), n=1, cutoff=0.85):
        lat, lon = everywhere[m[0]]
        return lat + dj, lon + mj, "fuzzy-india"
    if s in states:
        lat, lon = states[s]
        big_dj, big_mj = _jitter(district + market, scale=0.8)
        return lat + big_dj, lon + big_mj, "state"
    return None
