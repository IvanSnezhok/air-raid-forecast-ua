"""Build the map geometry of Ukraine used by the web UI (oblasts and raions, two levels of detail).

    python scripts/build_map.py [--hdx-zip ukr_admin_boundaries.geojson.zip] [--write-neighbours]

Source: the OCHA/HDX COD-AB boundaries of Ukraine (post-2020 raions, v05,
CC BY 3.0 IGO, ~84 MB zip; downloaded unless --hdx-zip is given). Every raion
is checked against the registry in src/airraid/raions.py.

Topology first, then simplification (as TopoJSON does): the raion rings are cut
into arcs at the points where the set of neighbours changes, so every shared
border is one arc, and every arc is simplified once. Neighbouring raions
therefore keep exactly the same border (no gaps or overlaps), and the oblasts
are the raions dissolved by oblast: an oblast is the chain of the arcs its
raions do not share with each other, so oblast and raion borders coincide.

Outputs (committed; the app and the pipeline never rebuild them):
  * src/airraid_app/static/ukraine-map.json: country view, loaded with the page:
    arcs simplified at COARSE_TOL, raion and oblast rings as arc indices,
    names, bounding boxes, label points and the attribution;
  * src/airraid_app/static/ukraine-map-detail.json: zoomed view, loaded when the
    map zooms in: the same rings over arcs simplified at DETAIL_TOL;
  * --write-neighbours: src/airraid/raion_neighbours.py (raion model features!
    By default the script only checks that the committed list still matches).

Encoding: coordinates in screen units of a 1000-wide viewBox (Lambert conformal
conic projection), multiplied by Q and rounded; each arc is a flat list
[x0, y0, dx1, dy1, ...]. A ring is a list of arc indices, ~i meaning arc i
reversed. The web UI turns them into SVG paths (static/app.js, `ringsPath`).
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

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "airraid_app" / "static"
OUT = STATIC / "ukraine-map.json"
DETAIL_OUT = STATIC / "ukraine-map-detail.json"
NEIGHBOURS_OUT = ROOT / "src" / "airraid" / "raion_neighbours.py"
HDX_URL = (
    "https://data.humdata.org/dataset/d23f529f-31e4-4021-a65b-13987e5cfb42/resource/"
    "681beb86-391b-4a08-8140-ca52e80fcdce/download/ukr_admin_boundaries.geojson.zip"
)
# HDX pcode -> registry pcode when they differ: the Chornobyl exclusion zone belongs to Vyshhorodskyi raion.
PCODE_ALIAS = {"UA3200": "UA3210"}

WIDTH = 1000  # viewBox width
PAD = 6  # viewBox margin, so outlines at the edge are not clipped
Q = 10  # coordinates are stored as integers in 1/Q viewBox units
COARSE_TOL = 0.22  # Douglas-Peucker tolerance in viewBox units (~0.3 km), country view
DETAIL_TOL = 0.035  # zoomed view (an oblast fills the map: 4-8x)
COARSE_MIN_AREA = 0.6  # drop islets smaller than this (viewBox units^2; 1 unit ~ 1.3 km)
DETAIL_MIN_AREA = 0.02
# Lambert conformal conic for Ukraine (spherical): standard parallels and centre
LAT1, LAT2, LAT0, LON0 = 45.5, 51.0, 48.4, 31.2

ATTRIBUTION = {
    "uk": "Межі: OCHA/HDX COD-AB v05, ДНВП «Картографія», CC BY 3.0 IGO; спрощено",
    "en": "Boundaries: OCHA/HDX COD-AB v05, SSPE Kartographia, CC BY 3.0 IGO; simplified",
}


# ------------------------------------------------------------------ geometry helpers
def _lcc():
    p1, p2, p0 = (math.radians(v) for v in (LAT1, LAT2, LAT0))
    t = lambda p: math.tan(math.pi / 4 + p / 2)  # noqa: E731
    n = math.log(math.cos(p1) / math.cos(p2)) / math.log(t(p2) / t(p1))
    F = math.cos(p1) * t(p1) ** n / n
    rho0 = F / t(p0) ** n

    def project(lon, lat):
        rho = F / t(math.radians(lat)) ** n
        a = n * math.radians(lon - LON0)
        return rho * math.sin(a), -(rho0 - rho * math.cos(a))  # screen y grows downwards

    return project


def _dp(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker on an open polyline (n x 2), iterative; keeps both end points."""
    n = len(pts)
    if n < 3:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = pts[a + 1:b]
        d = pts[b] - pts[a]
        norm = math.hypot(*d)
        if norm == 0:
            dist = np.hypot(*(seg - pts[a]).T)
        else:
            dist = np.abs(d[1] * (seg[:, 0] - pts[a][0]) - d[0] * (seg[:, 1] - pts[a][1])) / norm
        i = int(np.argmax(dist))
        if dist[i] > tol:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return pts[keep]


def _simplify_arc(pts: np.ndarray, tol: float) -> np.ndarray:
    if len(pts) > 3 and np.array_equal(pts[0], pts[-1]):
        # a closed ring (island, enclave): split at the point farthest from the start
        far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
        out = np.vstack([_dp(pts[:far + 1], tol)[:-1], _dp(pts[far:], tol)])
        if len(out) < 4:  # keep a recognisable shape: start, far point and two more
            q = len(pts) // 4
            out = pts[[0, q, far, min(len(pts) - 2, far + q), len(pts) - 1]]
        return out
    return _dp(pts, tol)


def _area(pts) -> float:
    x, y = np.asarray(pts, dtype=float).T
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _rings(geom):
    if geom["type"] == "Polygon":
        return geom["coordinates"]
    return [r for poly in geom["coordinates"] for r in poly]


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


def _chain(refs: list[tuple[int, bool]], arcs_pts: dict[int, list]) -> list[list[tuple[int, bool]]]:
    """Oriented arcs (index, reversed) of one unit -> closed rings of arc refs."""
    todo = defaultdict(list)
    for i, rev in refs:
        a = arcs_pts[i]
        todo[a[-1] if rev else a[0]].append((i, rev))
    rings = []
    while todo:
        start = next(iter(todo))
        ring, cur = [], start
        while cur in todo:
            i, rev = todo[cur].pop()
            if not todo[cur]:
                del todo[cur]
            ring.append((i, rev))
            a = arcs_pts[i]
            cur = a[0] if rev else a[-1]
        if cur != start:
            raise SystemExit(f"open ring while chaining arcs: {ring[:3]}...")
        rings.append(ring)
    return rings


def _label_point(rings: list[np.ndarray], avoid=None, n: int = 48) -> list[float]:
    """Interior point far from the outline (coarse pole of inaccessibility) for the value label."""
    allp = np.vstack(rings)
    (x0, y0), (x1, y1) = allp.min(0), allp.max(0)
    gx, gy = np.meshgrid(np.linspace(x0, x1, n + 1)[1:-1], np.linspace(y0, y1, n + 1)[1:-1])
    P = np.column_stack([gx.ravel(), gy.ravel()])
    A = np.vstack([r for r in rings])
    B = np.vstack([np.roll(r, -1, axis=0) for r in rings])
    inside = np.zeros(len(P), dtype=bool)
    for r in rings:
        a, b = r, np.roll(r, -1, axis=0)
        cond = (a[None, :, 1] > P[:, None, 1]) != (b[None, :, 1] > P[:, None, 1])
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (b[None, :, 0] - a[None, :, 0]) * (P[:, None, 1] - a[None, :, 1]) / (b[None, :, 1] - a[None, :, 1]) + a[None, :, 0]
        inside ^= (np.sum(cond & (P[:, None, 0] < xint), axis=1) % 2).astype(bool)
    d = B - A
    L = np.maximum((d ** 2).sum(1), 1e-12)
    best, best_xy = -1.0, allp.mean(0)
    for k in np.flatnonzero(inside):
        p = P[k]
        t = np.clip(((p - A) * d).sum(1) / L, 0, 1)
        dist = float(np.hypot(*(A + t[:, None] * d - p).T).min())
        if avoid is not None:
            dist = min(dist, math.dist(p, avoid[:2]) - avoid[2])
        if dist > best:
            best, best_xy = dist, p
    return [round(float(best_xy[0]), 1), round(float(best_xy[1]), 1)]


# ------------------------------------------------------------------ build
def load_units(zip_path: Path | None):
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
    feats, units = adm2["features"], []
    for f in feats:
        pc = f["properties"]["adm2_pcode"]
        reg = by_pcode.get(PCODE_ALIAS.get(pc, pc))
        if reg is None:
            raise SystemExit(f"HDX unit {pc} {f['properties']['adm2_name']} is not in src/airraid/raions.py")
        units.append(reg)
    missing = set(by_pcode) - {u.pcode for u in units}
    if missing:
        raise SystemExit(f"registry rows without geometry: {sorted(missing)}")
    version = {f["properties"].get("version") for f in feats}
    return RAIONS, feats, units, version


def build(zip_path: Path | None = None, write_neighbours: bool = False) -> None:
    RAIONS, feats, units, version = load_units(zip_path)
    oblast_of = {u.id: u.oblast for u in RAIONS}
    project = _lcc()

    rings, owner = [], []
    for f, u in zip(feats, units):
        for ring in _rings(f["geometry"]):
            pts = [(p[0], p[1]) for p in ring]
            if pts[0] == pts[-1]:
                pts = pts[:-1]
            dedup = [p for i, p in enumerate(pts) if p != pts[i - 1]]
            if len(dedup) >= 3:
                rings.append(dedup)
                owner.append(u.id)
    ring_arcs = _split_arcs(rings)

    # unique arcs (canonical direction) and, per raion, the oriented arcs it uses
    index, arc_lonlat = {}, []
    arc_owners, arc_uses = defaultdict(set), defaultdict(int)
    unit_refs = defaultdict(list)
    for arcs, uid in zip(ring_arcs, owner):
        for arc in arcs:
            key, rev = _arc_key(arc)
            if key not in index:
                index[key] = len(arc_lonlat)
                arc_lonlat.append(key)
            i = index[key]
            arc_owners[i].add(uid)
            arc_uses[i] += 1
            unit_refs[uid].append((i, rev))
    print(f"{len(rings)} rings, {len(arc_lonlat)} arcs, {sum(map(len, arc_lonlat))} points")

    # projection to the viewBox
    proj = [np.array([project(*p) for p in a]) for a in arc_lonlat]
    allp = np.vstack(proj)
    (mx0, my0), (mx1, my1) = allp.min(0), allp.max(0)
    scale = (WIDTH - 2 * PAD) / (mx1 - mx0)
    height = (my1 - my0) * scale + 2 * PAD
    proj = [(a - [mx0, my0]) * scale + PAD for a in proj]

    # Per raion: drop arcs it uses twice (the border between the Chornobyl zone and the rest of
    # Vyshhorodskyi raion), then chain into rings. Per oblast: the same with the arcs of all its
    # raions, which dissolves the raions into the oblast.
    def unit_rings(refs):
        count = defaultdict(int)
        for i, _ in refs:
            count[i] += 1
        keep = [(i, rev) for i, rev in refs if count[i] == 1]
        return _chain(keep, arc_lonlat)

    raion_rings = {uid: unit_rings(refs) for uid, refs in unit_refs.items()}
    oblasts = sorted({u.oblast for u in RAIONS})
    oblast_rings = {o: unit_rings([ref for uid, refs in unit_refs.items() if oblast_of[uid] == o for ref in refs])
                    for o in oblasts}
    # oblast borders and the country outline: arcs not internal to one oblast
    border_arcs = sorted(i for i, ids in arc_owners.items()
                         if not (len(ids) == 1 and arc_uses[i] > 1)
                         and (len(ids) == 1 or len({oblast_of[u] for u in ids}) > 1))

    neighbours = defaultdict(set)
    for ids in arc_owners.values():
        if len(ids) > 1:
            for a in ids:
                neighbours[a] |= ids - {a}

    def lod(tol: float, min_area: float):
        simple = [_simplify_arc(a, tol) for a in proj]
        quant = []
        for a in simple:
            qa = np.rint(a * Q).astype(np.int64)
            keep = np.ones(len(qa), dtype=bool)
            keep[1:-1] = np.any(qa[1:-1] != qa[:-2], axis=1)  # drop repeated points, keep the ends
            quant.append(qa[keep])

        def ring_pts(ring):
            parts = [(quant[i][::-1] if rev else quant[i])[:-1] for i, rev in ring]
            return np.vstack(parts) / Q

        def encode(rs):
            out = []
            for ring in rs:
                pts = ring_pts(ring)
                # an islet invisible at this scale; only isolated rings (no shared border), so that
                # raions and their oblast always drop the same rings and the borders stay identical
                isolated = all(arc_uses[i] == 1 for i, _ in ring)
                if isolated and (len(pts) < 3 or abs(_area(pts)) < min_area):
                    continue
                out.append([~i if rev else i for i, rev in ring])
            return out

        units_out = {uid: encode(rs) for uid, rs in raion_rings.items()}
        obl_out = {o: encode(rs) for o, rs in oblast_rings.items()}
        used = sorted({(r if r >= 0 else ~r) for rs in [*units_out.values(), *obl_out.values()] for ring in rs for r in ring}
                      | set(border_arcs))
        # renumber the arcs actually used
        new = {old: k for k, old in enumerate(used)}
        ren = lambda rs: [[new[r] if r >= 0 else ~new[~r] for r in ring] for ring in rs]  # noqa: E731
        arcs_out = []
        for old in used:
            a = quant[old]
            d = np.vstack([a[:1], np.diff(a, axis=0)])
            arcs_out.append(d.ravel().tolist())
        geo = {
            "arcs": arcs_out,
            "raions": {uid: ren(rs) for uid, rs in units_out.items()},
            "oblasts": {o: ren(rs) for o, rs in obl_out.items()},
            "borders": [new[i] for i in border_arcs if i in new],
        }
        shapes = {uid: [ring_pts(r) for r in raion_rings[uid] if len(ring_pts(r)) >= 3] for uid in raion_rings}
        oshapes = {o: [ring_pts(r) for r in oblast_rings[o] if len(ring_pts(r)) >= 3] for o in oblasts}
        npts = sum(len(a) // 2 for a in arcs_out)
        return geo, shapes, oshapes, npts

    coarse, shapes, oshapes, n_c = lod(COARSE_TOL, COARSE_MIN_AREA)
    detail, _, _, n_d = lod(DETAIL_TOL, DETAIL_MIN_AREA)

    def bbox(rs):
        p = np.vstack(rs)
        return [round(float(v), 1) for v in (*p.min(0), *p.max(0))]

    kyiv = shapes["kyiv-city"]
    kyiv_c = _label_point([max(kyiv, key=lambda r: abs(_area(r)))])
    raion_meta = {}
    for r in RAIONS:
        main = max(shapes[r.id], key=lambda x: abs(_area(x)))
        raion_meta[r.id] = {"name_uk": r.name_uk, "name": r.name, "oblast": r.oblast,
                            "bbox": bbox(shapes[r.id]), "label": _label_point([main])}
    oblast_meta = {}
    for o in oblasts:
        rs = oshapes[o]
        main = max(rs, key=lambda x: abs(_area(x)))
        # label on the main part; holes (Kyiv City inside Kyivska) are kept so the label avoids them
        holes = [x for x in rs if x is not main and _area(x) * _area(main) < 0]
        oblast_meta[o] = {"bbox": bbox(rs), "label": kyiv_c if o == "Kyiv City" else _label_point([main, *holes])}

    view = f"0 0 {WIDTH} {height:.0f}"
    out = {
        "format": 2, "viewBox": view, "q": Q, "lod": "coarse", "tolerance": COARSE_TOL,
        "source": f"OCHA/HDX COD-AB {'/'.join(sorted(v for v in version if v))}",
        "attribution": ATTRIBUTION,
        **coarse,
        "meta": {"raions": raion_meta, "oblasts": oblast_meta},
        "detail": {"file": DETAIL_OUT.name, "tolerance": DETAIL_TOL},
    }
    out_d = {"format": 2, "viewBox": view, "q": Q, "lod": "detail", "tolerance": DETAIL_TOL, **detail}
    for path, obj, n in ((OUT, out, n_c), (DETAIL_OUT, out_d, n_d)):
        path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB, {n} points)")

    lines = ",\n".join(f"    {r.id!r}: {sorted(neighbours[r.id])!r}" for r in RAIONS)
    text = ('"""Raions sharing a border, generated by scripts/build_map.py from the HDX boundaries. Do not edit."""\n\n'
            f"NEIGHBOURS: dict[str, list[str]] = {{\n{lines},\n}}\n")
    if write_neighbours:
        NEIGHBOURS_OUT.write_text(text, encoding="utf-8")
        print(f"wrote {NEIGHBOURS_OUT}")
    elif NEIGHBOURS_OUT.read_text(encoding="utf-8") != text:
        print(f"WARNING: {NEIGHBOURS_OUT} differs from the source; the raion models use it as features, "
              "rerun with --write-neighbours only together with retraining")
    else:
        print(f"{NEIGHBOURS_OUT.name} unchanged")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hdx-zip", type=Path, help="local copy of ukr_admin_boundaries.geojson.zip (skips the download)")
    ap.add_argument("--write-neighbours", action="store_true", help="also rewrite src/airraid/raion_neighbours.py")
    args = ap.parse_args()
    build(args.hdx_zip, args.write_neighbours)


if __name__ == "__main__":
    main()
