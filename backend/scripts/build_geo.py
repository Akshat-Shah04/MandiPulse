"""
Build the map layers from DataMeet's open boundaries (MIT licence).

  data/geo/Admin2.*            DataMeet States/Admin2 shapefile (36 states/UTs, incl. Ladakh)
  data/geo/dists11.geojson     DataMeet Census-2011 district polygons
  -> data/geo/india_states.geojson   simplified, names aligned with Agmarknet
  -> data/geo/district_centroids.csv state,district,lat,lon

The two raw inputs are not in git (about 45 MB). To rebuild, download them
from https://github.com/datameet/maps into backend/data/geo/:
  States/Admin2.shp, .shx, .dbf, .prj   and   docs/data/geojson/dists11.geojson
"""

import csv
import json
from pathlib import Path

import shapefile
from shapely.geometry import mapping, shape

GEO = Path(__file__).resolve().parent.parent / "data" / "geo"

# DataMeet spelling -> spelling used after prices.agmarknet.STATE_ALIASES
STATE_NAMES = {
    "Andaman & Nicobar": "Andaman and Nicobar Islands",
    "Jammu & Kashmir": "Jammu and Kashmir",
}


def build_states():
    reader = shapefile.Reader(str(GEO / "Admin2"))
    features = []
    for sr in reader.shapeRecords():
        name = STATE_NAMES.get(sr.record["ST_NM"], sr.record["ST_NM"])
        geom = shape(sr.shape.__geo_interface__).simplify(0.02, preserve_topology=True)
        c = geom.representative_point()
        features.append(
            {
                "type": "Feature",
                "properties": {"name": name, "cx": round(c.x, 3), "cy": round(c.y, 3)},
                "geometry": _round(mapping(geom)),
            }
        )
    out = GEO / "india_states.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")))
    print(out.name, round(out.stat().st_size / 1024), "KB,", len(features), "features")


def _round(g, nd=3):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], nd), round(c[1], nd)]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def build_district_centroids():
    src = GEO / "dists11.geojson"
    data = json.loads(src.read_text(encoding="utf-8"))
    rows = []
    for f in data["features"]:
        p = f["properties"]
        state = STATE_NAMES.get(p.get("ST_NM", ""), p.get("ST_NM", ""))
        district = p.get("DISTRICT", "")
        if not (state and district and f.get("geometry")):
            continue
        c = shape(f["geometry"]).representative_point()
        rows.append((state, district, round(c.y, 4), round(c.x, 4)))
    out = GEO / "district_centroids.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["state", "district", "lat", "lon"])
        w.writerows(sorted(rows))
    print(out.name, len(rows), "districts")


if __name__ == "__main__":
    build_states()
    build_district_centroids()
