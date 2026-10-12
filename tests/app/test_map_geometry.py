"""Map geometry built by scripts/build_map.py: complete, topologically clean, oblasts = dissolved raions.

Both levels of detail (ukraine-map.json with the page, ukraine-map-detail.json when the map
zooms in) describe the same units over shared arcs, so oblast and raion borders coincide and
neighbours have no gaps or overlaps. Offline: reads the committed files only.
"""
import gzip
import json
from collections import Counter
from pathlib import Path

import pytest

from airraid.raions import RAIONS
from airraid_app.regions import UK_NAMES

STATIC = Path(__file__).resolve().parents[2] / "src/airraid_app/static"
COARSE = json.loads((STATIC / "ukraine-map.json").read_text(encoding="utf-8"))
DETAIL = json.loads((STATIC / COARSE["detail"]["file"]).read_text(encoding="utf-8"))
LODS = {"coarse": COARSE, "detail": DETAIL}


def _arcs(g):
    out = []
    for a in g["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in zip(a[::2], a[1::2]):
            x += dx
            y += dy
            pts.append((x, y))
        out.append(pts)
    return out


def _ends(ref, arcs):
    a = arcs[ref if ref >= 0 else ~ref]
    return (a[0], a[-1]) if ref >= 0 else (a[-1], a[0])


def _uses(rings):
    return Counter(r if r >= 0 else ~r for ring in rings for r in ring)


@pytest.mark.parametrize("lod", LODS)
def test_every_raion_and_oblast_is_drawn(lod):
    g = LODS[lod]
    assert g["viewBox"] == COARSE["viewBox"] and g["q"] == COARSE["q"]
    assert set(g["raions"]) == {r.id for r in RAIONS}
    assert set(g["oblasts"]) == {r.oblast for r in RAIONS}
    assert set(UK_NAMES) <= set(g["oblasts"])  # every region of the alert dataset, Kyiv City included
    for unit, rings in [*g["raions"].items(), *g["oblasts"].items()]:
        assert rings and all(len(ring) >= 1 for ring in rings), unit


@pytest.mark.parametrize("lod", LODS)
def test_rings_are_closed_chains_of_shared_arcs(lod):
    g = LODS[lod]
    arcs = _arcs(g)
    for unit, rings in [*g["raions"].items(), *g["oblasts"].items()]:
        for ring in rings:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                assert _ends(a, arcs)[1] == _ends(b, arcs)[0], (lod, unit)
    # a border belongs to at most two raions (no overlaps), and arcs on one side only are on the outline
    uses = _uses([ring for rings in g["raions"].values() for ring in rings])
    assert max(uses.values()) <= 2
    oblast_of = {r.id: r.oblast for r in RAIONS}
    owners = {}
    for rid, rings in g["raions"].items():
        for a in _uses(rings):
            owners.setdefault(a, set()).add(oblast_of[rid])
    borders = set(g["borders"])
    assert {a for a, n in uses.items() if n == 1} <= borders  # the country outline
    assert {a for a, o in owners.items() if len(o) == 2} <= borders  # borders between oblasts
    assert not {a for a, n in uses.items() if n == 2 and len(owners[a]) == 1} & borders  # no raion border inside an oblast


@pytest.mark.parametrize("lod", LODS)
def test_oblasts_are_the_dissolved_raions(lod):
    g = LODS[lod]
    for oblast, orings in g["oblasts"].items():
        own = _uses([ring for r in RAIONS if r.oblast == oblast for ring in g["raions"][r.id]])
        outer = {a for a, n in own.items() if n % 2}  # arcs shared by two raions of the oblast cancel out
        drawn = _uses(orings)
        assert max(drawn.values()) == 1, oblast
        assert set(drawn) <= outer, oblast  # same arcs as the raions: borders coincide exactly
        if lod == "detail":
            assert set(drawn) == outer, oblast


def test_kyiv_city_is_cut_out_of_kyiv_oblast():
    for g in LODS.values():
        city = _uses(g["oblasts"]["Kyiv City"])
        assert set(city) <= set(_uses(g["oblasts"]["Kyivska oblast"]))
        assert len(g["oblasts"]["Kyivska oblast"]) >= 2  # outer ring + the hole of the city


def test_metadata_labels_and_attribution():
    meta = COARSE["meta"]
    assert set(meta["oblasts"]) == set(COARSE["oblasts"])
    for name, m in [*meta["oblasts"].items(), *meta["raions"].items()]:
        x0, y0, x1, y1 = m["bbox"]
        assert x0 - 1 <= m["label"][0] <= x1 + 1 and y0 - 1 <= m["label"][1] <= y1 + 1, name
    assert set(COARSE["attribution"]) == {"en", "uk"}
    assert all("COD-AB" in a and "CC BY 3.0 IGO" in a for a in COARSE["attribution"].values())
    licence = (STATIC / "ukraine-map.LICENSE.txt").read_text(encoding="utf-8")
    assert "CC BY 3.0 IGO" in licence and "Kartographia" in licence


def test_payload_stays_small():
    """The country level loads with the page; the detailed one only when the map zooms in."""
    coarse = len(gzip.compress((STATIC / "ukraine-map.json").read_bytes(), 9))
    detail = len(gzip.compress((STATIC / COARSE["detail"]["file"]).read_bytes(), 9))
    assert coarse < 100_000 and detail < 200_000, (coarse, detail)
    assert COARSE["tolerance"] > DETAIL["tolerance"]
    assert sum(map(len, DETAIL["arcs"])) > 2 * sum(map(len, COARSE["arcs"]))
