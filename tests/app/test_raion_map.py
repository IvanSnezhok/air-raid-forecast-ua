"""Raion registry and map: every raion has geometry and resolves from every alert source.

Offline except for the check against the official file, which runs when
data/raw/official_data_en.csv is present (raion_run.py downloads it).
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from airraid.raion_data import OFFICIAL_PATH
from airraid.raion_neighbours import NEIGHBOURS
from airraid.raions import BY_ID, BY_UID, NO_DATA_OBLASTS, RAIONS, forecastable, from_alerts_in_ua, from_official

ROOT = Path(__file__).resolve().parents[2]
GEO = json.loads((ROOT / "src/airraid_app/static/raions-map.json").read_text(encoding="utf-8"))


def test_registry_is_the_post_2020_raion_set():
    assert len(RAIONS) == 138  # 136 raions + Kyiv City + Sevastopol
    assert len({r.id for r in RAIONS}) == len({r.pcode for r in RAIONS}) == 138
    assert sum(r.name.endswith(" raion") for r in RAIONS) == 136
    per_oblast = pd.Series([r.oblast for r in RAIONS]).value_counts()
    assert per_oblast["Kharkivska oblast"] == 7 and per_oblast["Odeska oblast"] == 7
    assert per_oblast["Luhanska oblast"] == 8 and per_oblast["Avtonomna Respublika Krym"] == 10
    assert sum(forecastable(r) for r in RAIONS) == 119  # 118 raions + Kyiv City


def test_every_unit_has_geometry_and_vice_versa():
    assert set(GEO["raions"]) == {r.id for r in RAIONS}
    for rid, g in GEO["raions"].items():
        assert g["d"].startswith("M") and g["d"].endswith("Z"), rid
        x0, y0, x1, y1 = g["bbox"]
        assert x0 < x1 and y0 < y1
        assert x0 - 1 <= g["label"][0] <= x1 + 1 and y0 - 1 <= g["label"][1] <= y1 + 1, rid
        assert g["name_uk"] == BY_ID[rid].name_uk
    assert GEO["oblast_borders"] and "CC BY-IGO" in GEO["attribution"]


def test_alerts_in_ua_uids_cover_every_raion_outside_crimea():
    missing = [r.id for r in RAIONS if r.uid is None and r.oblast != "Avtonomna Respublika Krym"]
    assert missing == []
    assert len(BY_UID) == 126 + 2  # raions + Kyiv City (31) + Sevastopol (30)
    assert BY_UID[31].id == "kyiv-city" and BY_UID[124].id == "kharkivskyi"


@pytest.mark.parametrize("kwargs, expected", [
    ({"uid": "124"}, "kharkivskyi"),
    ({"title": "Кам’янський район"}, "kamianskyi"),  # typographic apostrophe
    ({"title": "Кам'янський район"}, "kamianskyi"),
    ({"title": "Чугуївська територіальна громада", "raion": "Чугуївський район"}, "chuhuivskyi"),
    ({"uid": 1293, "title": "м. Харків"}, "kharkivskyi"),
    ({"uid": "31", "title": "м. Київ"}, "kyiv-city"),
    ({"title": "Харківська область"}, None),
    ({"uid": "99999", "title": "Невідома громада"}, None),
])
def test_alerts_in_ua_resolution(kwargs, expected):
    r = from_alerts_in_ua(**kwargs)
    assert (r.id if r else None) == expected


def test_renamed_raions_in_the_official_file():
    assert from_official("Kharkivska oblast", "Krasnohradskyi raion").id == "berestynskyi"
    assert from_official("Lvivska oblast", "Chervonohradskyi raion").id == "sheptytskyi"
    assert from_official("Volynska oblast", "Volodymyr-Volynskyi raion").id == "volodymyrskyi"
    assert from_official("Kyivska oblast", "Krasnohradskyi raion") is None  # alias must stay in its oblast
    assert from_official("Kyiv City", None).id == "kyiv-city"


@pytest.mark.skipif(not OFFICIAL_PATH.exists(), reason="official file not downloaded")
def test_every_raion_of_the_official_file_maps():
    df = pd.read_csv(OFFICIAL_PATH, usecols=["oblast", "raion"]).dropna().drop_duplicates()
    unmatched = [(o, r) for o, r in df.itertuples(index=False) if from_official(o, r) is None]
    assert unmatched == []
    covered = {from_official(o, r).id for o, r in df.itertuples(index=False)}
    assert {r.id for r in RAIONS if forecastable(r)} - covered == {"kyiv-city"}  # the city has oblast-level rows only


def test_alerts_in_ua_location_list_resolves():
    """The full alerts.in.ua location list (shipped with the live collector) maps onto the registry."""
    path = ROOT / "src/airraid/resources/alerts_in_ua_locations.csv"
    if not path.exists():
        pytest.skip("alerts.in.ua location list not in this checkout")
    loc = pd.read_csv(path, dtype=str, keep_default_na=False)
    raions = loc[loc["level"] == "raion"]
    bad = [t for u, t in zip(raions["uid"], raions["title"]) if from_alerts_in_ua(u, t) is None]
    assert bad == [], f"unmatched alerts.in.ua raions: {bad}"
    titles = dict(zip(loc["uid"], loc["title"]))
    hrom = loc[(loc["level"] == "hromada") & (loc["raion_uid"] != "")]
    bad = [t for u, t, ru in zip(hrom["uid"], hrom["title"], hrom["raion_uid"])
           if from_alerts_in_ua(u, t, titles.get(ru)) is None]
    assert bad == [], f"{len(bad)} hromadas without a raion, e.g. {bad[:5]}"


def test_neighbours_are_symmetric_and_connected():
    for a, nbs in NEIGHBOURS.items():
        assert a not in nbs
        for b in nbs:
            assert a in NEIGHBOURS[b], (a, b)
    lonely = [r.id for r in RAIONS if forecastable(r) and not NEIGHBOURS.get(r.id)]
    assert lonely == []
    assert "kyiv-city" in NEIGHBOURS["buchanskyi"] and "chuhuivskyi" in NEIGHBOURS["kharkivskyi"]
    assert not any(BY_ID[b].oblast in NO_DATA_OBLASTS for b in NEIGHBOURS["kharkivskyi"])
