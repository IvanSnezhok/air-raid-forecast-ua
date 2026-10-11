"""Build the simplified SVG map of Ukraine used by the web UI.

    python scripts/build_map.py

Downloads oblast boundaries (OpenStreetMap, ODbL, packaged by
github.com/EugeneBorshch/ukraine_geojson), projects them, simplifies the
outlines and writes src/airraid_app/static/ukraine-map.json:
{"viewBox": "...", "regions": {"<dataset region name>": "<svg path>"}, ...}.
Only needs the standard library; the output is committed, so the app never
downloads anything at runtime.
"""
from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/EugeneBorshch/ukraine_geojson/master/UA_FULL_Ukraine.geojson"
OUT = Path(__file__).resolve().parents[1] / "src" / "airraid_app" / "static" / "ukraine-map.json"

# ISO 3166-2 code -> region name used in the alert dataset (None: not in the dataset).
ISO = {
    "UA-71": "Cherkaska oblast", "UA-74": "Chernihivska oblast", "UA-77": "Chernivetska oblast",
    "UA-43": None, "UA-12": "Dnipropetrovska oblast", "UA-14": "Donetska oblast",
    "UA-26": "Ivano-Frankivska oblast", "UA-63": "Kharkivska oblast", "UA-65": "Khersonska oblast",
    "UA-68": "Khmelnytska oblast", "UA-32": "Kyivska oblast", "UA-35": "Kirovohradska oblast",
    "UA-09": "Luhanska oblast", "UA-46": "Lvivska oblast", "UA-48": "Mykolaivska oblast",
    "UA-51": "Odeska oblast", "UA-53": "Poltavska oblast", "UA-56": "Rivnenska oblast",
    "UA-59": "Sumska oblast", "UA-61": "Ternopilska oblast", "UA-05": "Vinnytska oblast",
    "UA-07": "Volynska oblast", "UA-21": "Zakarpatska oblast", "UA-23": "Zaporizka oblast",
    "UA-18": "Zhytomyrska oblast",
}
KYIV = (30.5234, 50.4501)  # Kyiv City is not a separate polygon in the source: drawn as a circle
WIDTH = 1000
LAT0 = 48.5
TOLERANCE = 1.2  # Douglas-Peucker tolerance in output pixels


def _project(lon, lat, bbox):
    x0, y1 = bbox
    k = math.cos(math.radians(LAT0))
    return (lon - x0) * k, (y1 - lat)


def _dp(pts, tol):
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-12
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        x, y = pts[i]
        d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [pts[0], pts[-1]]
    return _dp(pts[: idx + 1], tol)[:-1] + _dp(pts[idx:], tol)


def _simplify_ring(pts, tol):
    # A closed ring has equal end points, which breaks the line-based DP:
    # split it at the point farthest from the start and simplify both halves.
    far = max(range(len(pts)), key=lambda i: math.dist(pts[0], pts[i]))
    return _dp(pts[: far + 1], tol)[:-1] + _dp(pts[far:], tol)


def _rings(geom):
    if geom["type"] == "Polygon":
        return geom["coordinates"]
    return [r for poly in geom["coordinates"] for r in poly]


def _inside(x, y, rings):
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def _seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def _label_point(rings, avoid=None):
    """Interior point far from the outline (coarse pole of inaccessibility) for the value label."""
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    edges = [(a, b) for r in rings for a, b in zip(r, r[1:] + r[:1])]
    best, best_xy = -1.0, (sum(xs) / len(xs), sum(ys) / len(ys))
    n = 36
    for i in range(1, n):
        for j in range(1, n):
            x = min(xs) + (max(xs) - min(xs)) * i / n
            y = min(ys) + (max(ys) - min(ys)) * j / n
            if not _inside(x, y, rings):
                continue
            d = min(_seg_dist(x, y, *a, *b) for a, b in edges)
            if avoid is not None:
                d = min(d, math.dist((x, y), avoid[:2]) - avoid[2])
            if d > best:
                best, best_xy = d, (x, y)
    return [round(best_xy[0], 1), round(best_xy[1], 1)]


def main():
    with urllib.request.urlopen(URL, timeout=60) as r:
        data = json.load(r)
    lons = [p[0] for f in data["features"] for ring in _rings(f["geometry"]) for p in ring]
    lats = [p[1] for f in data["features"] for ring in _rings(f["geometry"]) for p in ring]
    bbox = (min(lons), max(lats))
    k = math.cos(math.radians(LAT0))
    scale = WIDTH / ((max(lons) - min(lons)) * k)
    height = (max(lats) - min(lats)) * scale

    def xy(lon, lat):
        x, y = _project(lon, lat, bbox)
        return x * scale, y * scale

    cx, cy = xy(*KYIV)
    regions, other, label_at = {}, {}, {}
    for f in data["features"]:
        name = ISO[f["properties"]["iso3166-2"]]
        parts, simple = [], []
        for ring in _rings(f["geometry"]):
            pts = _simplify_ring([xy(*p[:2]) for p in ring], TOLERANCE)
            if len(pts) < 4:
                continue
            simple.append(pts)
            parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
        if name:
            label_at[name] = _label_point(simple, avoid=(cx, cy, 40) if name == "Kyivska oblast" else None)
        path = "".join(parts)
        if name:
            regions[name] = path
        else:
            other[f["properties"]["name"]] = path
    label_at["Kyiv City"] = [round(cx, 1), round(cy, 1)]
    out = {
        "viewBox": f"0 0 {WIDTH} {height:.0f}",
        "regions": regions,
        "other": other,  # drawn greyed out (no data in the alert dataset)
        "labels": label_at,
        "kyiv_city": {"cx": round(cx, 1), "cy": round(cy, 1), "r": 11},
        "attribution": "Кордони: © учасники OpenStreetMap (ODbL), github.com/EugeneBorshch/ukraine_geojson",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
