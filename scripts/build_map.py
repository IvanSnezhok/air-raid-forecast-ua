"""Build the simplified SVG maps of Ukraine used by the web UI.

    python scripts/build_map.py                 # oblasts and raions
    python scripts/build_map.py --only oblasts
    python scripts/build_map.py --only raions [--hdx-zip ukr_admin_boundaries.geojson.zip]

Oblasts: downloads boundaries (OpenStreetMap, ODbL, packaged by
github.com/EugeneBorshch/ukraine_geojson), projects them, simplifies the
outlines and writes src/airraid_app/static/ukraine-map.json:
{"viewBox": "...", "regions": {"<dataset region name>": "<svg path>"}, ...}.

Raions: downloads the OCHA/HDX COD-AB boundaries of Ukraine (post-2020 raions,
CC BY-IGO, ~84 MB zip), simplifies them without opening gaps between
neighbours (shared borders are split into arcs and every arc is simplified
once, as TopoJSON does) and writes src/airraid_app/static/raions-map.json plus
the neighbour list src/airraid/raion_neighbours.py. Every unit is checked
against the registry in src/airraid/raions.py.

Only needs the standard library; the outputs are committed, so the app never
downloads anything at runtime.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

URL = "https://raw.githubusercontent.com/EugeneBorshch/ukraine_geojson/master/UA_FULL_Ukraine.geojson"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "airraid_app" / "static" / "ukraine-map.json"
HDX_URL = (
    "https://data.humdata.org/dataset/d23f529f-31e4-4021-a65b-13987e5cfb42/resource/"
    "681beb86-391b-4a08-8140-ca52e80fcdce/download/ukr_admin_boundaries.geojson.zip"
)
RAION_OUT = ROOT / "src" / "airraid_app" / "static" / "raions-map.json"
NEIGHBOURS_OUT = ROOT / "src" / "airraid" / "raion_neighbours.py"
RAION_TOLERANCE = 0.4  # output pixels; the raion layer is zoomed in, so finer than the oblast map
# HDX pcode -> registry id when they differ: the Chornobyl exclusion zone belongs to Vyshhorodskyi raion.
PCODE_ALIAS = {"UA3200": "UA3210"}

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


def build_oblasts():
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


# ------------------------------------------------------------------ raions
def _split_arcs(rings):
    """Cut closed rings (lists of hashable points, no repeated end point) into arcs at junctions.

    A junction is a point with more than two distinct neighbours over all rings, i.e.
    where the set of polygons sharing the border changes. Rings without a junction
    (islands, enclaves) are cut at their smallest point so both sides of a shared
    ring get the same arc. Returns, per ring, a list of arcs (point lists, end points
    included).
    """
    nb = defaultdict(set)
    for ring in rings:
        n = len(ring)
        for i, p in enumerate(ring):
            nb[p].add(ring[i - 1])
            nb[p].add(ring[(i + 1) % n])
    out = []
    for ring in rings:
        cuts = [i for i, p in enumerate(ring) if len(nb[p]) > 2]
        if not cuts:
            cuts = [min(range(len(ring)), key=lambda i: ring[i])]
        r = ring[cuts[0]:] + ring[:cuts[0]]
        idx = [c - cuts[0] for c in cuts] + [len(ring)]
        r = r + [r[0]]
        out.append([r[a:b + 1] for a, b in zip(idx, idx[1:])])
    return out


def _arc_key(arc):
    rev = arc[::-1]
    return (tuple(arc), False) if tuple(arc) <= tuple(rev) else (tuple(rev), True)


def _area(pts):
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))


def build_raions(zip_path: Path | None = None):
    sys.path.insert(0, str(ROOT / "src"))
    from airraid.raions import RAIONS

    if zip_path is None:
        print(f"downloading {HDX_URL}")
        with urllib.request.urlopen(HDX_URL, timeout=600) as r:
            blob = r.read()
    else:
        blob = Path(zip_path).read_bytes()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        adm2 = json.loads(z.read("ukr_admin2.geojson"))
    by_pcode = {r.pcode: r for r in RAIONS}
    feats = adm2["features"]
    units = []
    for f in feats:
        pc = f["properties"]["adm2_pcode"]
        reg = by_pcode.get(PCODE_ALIAS.get(pc, pc))
        if reg is None:
            raise SystemExit(f"HDX unit {pc} {f['properties']['adm2_name']} is not in src/airraid/raions.py")
        units.append(reg)
    missing = set(by_pcode) - {u.pcode for u in units}
    if missing:
        raise SystemExit(f"registry rows without geometry: {sorted(missing)}")

    lons = [p[0] for f in feats for ring in _rings(f["geometry"]) for p in ring]
    lats = [p[1] for f in feats for ring in _rings(f["geometry"]) for p in ring]
    bbox = (min(lons), max(lats))
    k = math.cos(math.radians(LAT0))
    scale = WIDTH / ((max(lons) - min(lons)) * k)
    height = (max(lats) - min(lats)) * scale

    def xy(p):
        x, y = _project(p[0], p[1], bbox)
        return x * scale, y * scale

    rings, owner = [], []
    for f, u in zip(feats, units):
        for ring in _rings(f["geometry"]):
            pts = [(p[0], p[1]) for p in ring]
            if pts[0] == pts[-1]:
                pts = pts[:-1]
            dedup = [p for i, p in enumerate(pts) if p != pts[i - 1]]
            if len(dedup) >= 3:
                rings.append(dedup)
                owner.append(u)
    arcs = _split_arcs(rings)

    simple, arc_owners, arc_uses = {}, defaultdict(set), defaultdict(int)
    for ring_arcs, u in zip(arcs, owner):
        for arc in ring_arcs:
            key, _ = _arc_key(arc)
            arc_owners[key].add(u.id)
            arc_uses[key] += 1
            if key not in simple:
                pts = [xy(p) for p in key]
                simple[key] = _simplify_ring(pts[:-1], RAION_TOLERANCE) + [pts[0]] if key[0] == key[-1] \
                    else _dp(pts, RAION_TOLERANCE)

    # Per unit: drop arcs it uses twice (the border between the Chornobyl zone and
    # the rest of Vyshhorodskyi raion), then chain the remaining oriented arcs into rings.
    unit_arcs = defaultdict(list)
    for ring_arcs, u in zip(arcs, owner):
        unit_arcs[u.id].extend(_arc_key(a) for a in ring_arcs)
    paths, boxes, shapes = defaultdict(list), {}, defaultdict(list)
    for uid, keys in unit_arcs.items():
        count = defaultdict(int)
        for key, _ in keys:
            count[key] += 1
        todo = {}
        for key, rev in keys:
            if count[key] == 1:
                start = key[-1] if rev else key[0]
                todo.setdefault(start, []).append((key, rev))
        while todo:
            start = next(iter(todo))
            pts, cur = [], start
            while cur in todo:
                key, rev = todo[cur].pop()
                if not todo[cur]:
                    del todo[cur]
                s_arc = simple[key][::-1] if rev else simple[key]
                pts.extend(s_arc[:-1])
                cur = key[0] if rev else key[-1]
            if len(pts) < 3 or abs(_area(pts)) < 0.05:
                continue  # tiny islet, invisible at this scale
            shapes[uid].append(pts)
            paths[uid].append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
    oblast_of = {u.id: u.oblast for u in RAIONS}
    borders, neighbours = [], defaultdict(set)
    for key, ids in arc_owners.items():
        if len(ids) > 1:
            for a in ids:
                neighbours[a] |= ids - {a}
        internal = len(ids) == 1 and arc_uses[key] > 1
        if not internal and (len(ids) == 1 or len({oblast_of[i] for i in ids}) > 1):
            pts = simple[key]
            borders.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts))
    for uid, rs in shapes.items():
        xs = [p[0] for r in rs for p in r]
        ys = [p[1] for r in rs for p in r]
        boxes[uid] = [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]
    # label at the interior point of the largest part (islands and exclaves ignored)
    labels = {uid: _label_point([max(rs, key=lambda r: abs(_area(r)))]) for uid, rs in shapes.items()}
    out = {
        "viewBox": f"0 0 {WIDTH} {height:.0f}",
        "raions": {
            r.id: {"name_uk": r.name_uk, "oblast": r.oblast, "d": "".join(paths[r.id]),
                   "bbox": boxes[r.id], "label": labels[r.id]}
            for r in RAIONS
        },
        "oblast_borders": "".join(borders),
        "attribution": "Кордони районів: OCHA/HDX COD-AB, ДНВП «Картографія» (CC BY-IGO)",
    }
    RAION_OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {RAION_OUT} ({RAION_OUT.stat().st_size / 1024:.0f} KB, {len(out['raions'])} units)")
    lines = ",\n".join(f"    {r.id!r}: {sorted(neighbours[r.id])!r}" for r in RAIONS)
    NEIGHBOURS_OUT.write_text(
        '"""Raions sharing a border, generated by scripts/build_map.py from the HDX boundaries. Do not edit."""\n\n'
        f"NEIGHBOURS: dict[str, list[str]] = {{\n{lines},\n}}\n", encoding="utf-8")
    print(f"wrote {NEIGHBOURS_OUT}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", choices=["oblasts", "raions"])
    ap.add_argument("--hdx-zip", type=Path, help="local copy of ukr_admin_boundaries.geojson.zip (skips the download)")
    args = ap.parse_args()
    if args.only != "raions":
        build_oblasts()
    if args.only != "oblasts":
        build_raions(args.hdx_zip)


if __name__ == "__main__":
    main()
