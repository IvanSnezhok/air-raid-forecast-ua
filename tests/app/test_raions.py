"""Raion layer: live state per raion, raion forecast and its labels, static export. Synthetic data, offline."""
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from airraid.features import HORIZONS
from airraid.raion_data import alerts_as_of, load_official
from airraid.raion_features import build_raion_features, make_inputs
from airraid.raions import RAIONS, forecastable
from airraid_app.api import create_app
from airraid_app.engine import ForecastEngine
from airraid_app.raion_engine import raion_state

from .conftest import synthetic_alerts, synthetic_official

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_static_site  # noqa: E402

N_FORECAST = sum(forecastable(r) for r in RAIONS)  # 119


def test_raions_endpoint_shape(client):
    body = client.get("/api/raions").json()
    assert len(body["raions"]) == N_FORECAST
    assert {x["id"] for x in body["no_data"]} >= {"yaltynskyi", "sevastopol", "luhanskyi"}
    assert body["live_source"] is None and body["n_active"] == 0  # no token: no live raion state
    assert body["horizons"] == list(HORIZONS)
    for r in body["raions"]:
        assert r["live"] is None
        for t in ("active", "onset"):
            assert set(map(int, r["probs"][t])) == set(HORIZONS)
            for h, p in r["probs"][t].items():
                assert p is None or 0 <= p <= 1
                assert r["method"][t][h] in ("lightgbm", "lightgbm_state", "oblast_x_share", "oblast", None)
                assert (p is None) == (r["method"][t][h] is None)


def test_raion_forecast_is_derived_from_the_oblast(client):
    """Official history stops 10 days before the end: no raion model, oblast x share with a stale share period."""
    body = client.get("/api/raions").json()
    f = client.get("/api/forecast").json()
    oblast = {r["name"]: r["probs"] for r in f["regions"]}
    assert body["share_period"] is not None and body["share_period"][1] < body["origin"]
    for r in body["raions"]:
        if r["oblast"] not in oblast:  # no oblast forecast in this synthetic set: nothing to derive from
            assert all(p is None for p in r["probs"]["active"].values())
            continue
        for t in ("active", "onset"):
            for h in map(str, HORIZONS):
                po, p = oblast[r["oblast"]][t][h], r["probs"][t][h]
                assert r["method"][t][h] == "oblast_x_share"
                assert p <= po + 1e-9 and r["share"][f"{t}_h{h}"] <= 1
    # the first raion of each synthetic oblast is hit much more often than the last one
    by = {r["id"]: r for r in body["raions"]}
    assert by["bohodukhivskyi"]["share"]["onset_h3"] > by["chuhuivskyi"]["share"]["onset_h3"]


def test_raion_detail(client):
    d = client.get("/api/raions/kharkivskyi").json()
    assert d["name_uk"] == "Харківський район" and d["oblast_id"] == "kharkivska"
    assert d["oblast_probs"]["onset"]["3"] >= d["probs"]["onset"]["3"]
    assert d["windows"]["1"]["active"][0] == d["origin"]
    assert "oblast_x_share" in d["methods"]
    assert client.get("/api/raions/yaltynskyi").status_code == 404  # occupied, no data
    assert client.get("/api/raions/atlantis").status_code == 404


def test_forecast_endpoint_does_not_carry_raions(client):
    assert "raions" not in client.get("/api/forecast").json()


def test_raion_map_is_served(client):
    geo = client.get("/static/raions-map.json").json()
    assert len(geo["raions"]) == 138 and geo["oblast_borders"]


PAYLOAD_NOW = pd.Timestamp("2026-10-11 10:30", tz="UTC")
PAYLOAD = {"alerts": [
    {"alert_type": "air_raid", "location_title": "Харківський район", "location_type": "raion", "location_uid": "124",
     "location_oblast": "Харківська область", "started_at": "2026-10-11T09:00:00.000Z"},
    {"alert_type": "air_raid", "location_title": "Чугуївська територіальна громада", "location_type": "hromada",
     "location_raion": "Чугуївський район", "location_oblast": "Харківська область", "started_at": "2026-10-11T09:20:00Z"},
    {"alert_type": "air_raid", "location_title": "Одеська область", "location_type": "oblast",
     "location_oblast": "Одеська область", "started_at": "2026-10-11T08:00:00Z"},
    {"alert_type": "air_raid", "location_title": "Подільський район", "location_type": "raion",
     "location_oblast": "Одеська область", "started_at": "2026-10-11T07:00:00Z"},
    {"alert_type": "air_raid", "location_title": "м. Київ", "location_type": "city", "location_uid": "31",
     "started_at": "2026-10-11T10:00:00Z"},
    {"alert_type": "air_raid", "location_title": "Автономна Республіка Крим", "location_type": "oblast",
     "location_oblast": "Автономна Республіка Крим", "started_at": "2022-12-11T00:00:00Z"},
    {"alert_type": "artillery_shelling", "location_title": "Сумський район", "location_type": "raion",
     "location_oblast": "Сумська область", "started_at": "2026-10-11T09:00:00Z"},
    {"alert_type": "air_raid", "location_title": "Нова громада", "location_type": "hromada",
     "location_oblast": "Сумська область", "started_at": "2026-10-11T09:00:00Z"},
]}


def test_raion_state_levels_and_hromadas():
    st, unmatched = raion_state(PAYLOAD["alerts"])
    assert st["kharkivskyi"].active and st["kharkivskyi"].level == "raion"
    assert st["chuhuivskyi"].level == "hromada" and st["chuhuivskyi"].locations == ["Чугуївська територіальна громада"]
    odesa = [r.id for r in RAIONS if r.oblast == "Odeska oblast"]
    assert all(st[r].active and st[r].level == "oblast" for r in odesa)
    assert st["podilskyi"].since == pd.Timestamp("2026-10-11 07:00", tz="UTC")  # earliest covering alert
    assert st["kyiv-city"].active and st["kyiv-city"].level == "oblast"
    assert not st["sumskyi"].active  # other alert types do not count
    assert not st["iziumskyi"].active
    assert unmatched == ["Нова громада"]


def _shifted(payload: dict, now: pd.Timestamp) -> dict:
    """PAYLOAD with every start moved so that PAYLOAD_NOW becomes `now`."""
    out = json.loads(json.dumps(payload))
    for a in out["alerts"]:
        a["started_at"] = (pd.Timestamp(a["started_at"]) - PAYLOAD_NOW + now).isoformat()
    return out


def test_live_raion_state_through_the_api(settings, engine, monkeypatch, tmp_path):
    now = engine.hist_end.ceil("h") + pd.Timedelta(minutes=30)
    payload = _shifted(PAYLOAD, now)
    e = ForecastEngine(replace(settings, alerts_token="test", state_dir=tmp_path))
    e.load_data()
    assert e.load_models()
    monkeypatch.setattr("airraid_app.engine.fetch_active", lambda token: payload)
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: now))
    with TestClient(create_app(e.s, e)) as c:
        body = c.get("/api/raions").json()
        assert body["live_source"] == "alerts.in.ua"
        by = {r["id"]: r for r in body["raions"]}
        assert by["kharkivskyi"]["live"]["active"] and by["chuhuivskyi"]["live"]["level"] == "hromada"
        assert not by["iziumskyi"]["live"]["active"]
        assert body["n_active"] == 2 + 7 + 1 and body["unmatched_locations"] == ["Нова громада"]
        d = c.get("/api/raions/chuhuivskyi").json()
        assert d["live"]["locations"] == ["Чугуївська територіальна громада"]
        # a live poll but no recent raion history: the current-state raion model
        assert body["tier"] == "live_state"
        assert by["iziumskyi"]["method"]["active"]["1"] == "lightgbm_state"
        assert by["kyiv-city"]["method"]["active"]["1"] == "lightgbm_state"
        # under alert for 1.5 h at the forecast moment vs a quiet raion of the same oblast
        assert by["kharkivskyi"]["probs"]["active"]["1"] > by["iziumskyi"]["probs"]["active"]["1"] + 0.2


@pytest.fixture(scope="module")
def lgb_settings(settings, tmp_path_factory):
    """Raion history that reaches the forecast moment and a selection that chose the raion model for onset."""
    d = tmp_path_factory.mktemp("raion_lgb")
    official = d / "official.csv"
    synthetic_official(synthetic_alerts()).to_csv(official, index=False)
    plan = {"best_months": 1, "best_trees": 20}
    sel = {f"{t}_h{h}": {"chosen": {"history": "lightgbm" if t == "onset" else "oblast_x_share",
                                    "live_state": "lightgbm_state", "none": "oblast_x_share_stale30"},
                         "lightgbm": plan, "lightgbm_state": plan}
           for t in ("active", "onset") for h in HORIZONS}
    (d / "reports" / "raions").mkdir(parents=True)
    (d / "reports" / "raions" / "summary.json").write_text(json.dumps({"selection": sel, "split": {"test": ["a", "b"]}}))
    pd.DataFrame([{"target": "onset", "horizon_h": 3, "model": "lightgbm", "brier": 0.1, "bss_vs_clim": 0.05,
                   "roc_auc": 0.7, "base_rate": 0.2}]).to_csv(d / "reports" / "raions" / "metrics_test.csv", index=False)
    return replace(settings, official_path=official, reports_dir=d / "reports", model_dir=d / "models", state_dir=d / "state")


def test_raion_model_is_used_when_history_reaches_now(lgb_settings):
    e = ForecastEngine(lgb_settings)
    e.bootstrap()
    assert e.status == "ready", e.message
    assert e.raions.models and {k for k, t, _ in e.raions.models if t == "active"} == {"lightgbm_state"}
    assert {(k, t) for k, t, _ in e.raions.models} >= {("lightgbm", "onset"), ("lightgbm_state", "onset")}
    with TestClient(create_app(lgb_settings, e)) as c:
        body = c.get("/api/raions").json()
        assert body["models"]["lightgbm"]["data_end"] is not None and body["tier"] == "history"
        by = {r["id"]: r for r in body["raions"]}
        r = by["iziumskyi"]
        assert {r["method"]["onset"][str(h)] for h in HORIZONS} == {"lightgbm"}
        assert {r["method"]["active"][str(h)] for h in HORIZONS} == {"oblast_x_share"}
        assert all(0 <= r["probs"]["onset"][str(h)] <= 1 for h in HORIZONS)
        d = c.get("/api/raions/iziumskyi").json()
        assert d["evaluation"]["scores"][0]["model"] == "lightgbm"


def test_raion_features_leak_free_on_synthetic_data():
    """Offline twin of tests/test_raion_leakage.py (runs in CI): truncating at T changes nothing, a lag-0 leak does."""
    vol = synthetic_alerts().rename(columns={"started_at": "start", "finished_at": "end"})
    raw = synthetic_official(synthetic_alerts())
    ra = load_official(raw=raw)
    end = vol["start"].max().floor("h")
    full = make_inputs(alerts_as_of(ra, end), alerts_as_of(vol, end), end)
    rng = np.random.default_rng(0)
    ks = sorted(rng.integers(full.grid.n_minutes // 60 - 24 * 30, full.grid.n_minutes // 60 - 12, size=4))
    for k in ks:
        T = full.grid.t0 + pd.Timedelta(hours=int(k))
        tr = make_inputs(alerts_as_of(ra, T), alerts_as_of(vol, T), T)
        a, b = build_raion_features(full, np.array([k])), build_raion_features(tr, np.array([k]))
        pd.testing.assert_frame_equal(a, b, check_dtype=False)
    k = next(int(k) for k in range(full.grid.n_minutes // 60 - 200, full.grid.n_minutes // 60 - 1) if full.H[:, k].sum() > 0)
    T = full.grid.t0 + pd.Timedelta(hours=k)
    tr = make_inputs(alerts_as_of(ra, T), alerts_as_of(vol, T), T)
    leak = lambda inp: np.pad(inp.H, ((0, 0), (0, 1)))[:, k]  # noqa: E731 - the hour starting at T
    assert not np.array_equal(leak(full), leak(tr))


def test_static_site_has_raions(settings, tmp_path):
    out = tmp_path / "site"
    build_static_site.build(out, replace(settings, type_forecast=None))
    raions = json.loads((out / "api" / "raions.json").read_text(encoding="utf-8"))
    assert len(raions["raions"]) == N_FORECAST
    for r in raions["raions"]:
        assert (out / "api" / "raions" / f"{r['id']}.json").exists()
    assert (out / "static" / "raions-map.json").exists() and (out / "static" / "raions-map.LICENSE.txt").exists()
    assert 'href="api/raions.json"' in (out / "index.html").read_text(encoding="utf-8")


def test_raion_history_from_the_live_store(tmp_path):
    """alerts.in.ua history pulls and polls in the store -> raion alerts, point in time, with the covered span."""
    from airraid import store
    from airraid.live_alerts import OBLAST_UID
    from airraid.raion_data import load_history, load_store

    root = tmp_path / "live-data"
    pull = pd.Timestamp("2026-10-11 20:00", tz="UTC")
    rec = lambda uid, title, level, oblast, start, end: {  # noqa: E731
        "uid": uid, "oblast": oblast, "oblast_uid": "22", "title": title, "level": level, "raion": None,
        "started_at": pd.Timestamp(start, tz="UTC"), "alert_type": "air_raid",
        "finished_at": pd.Timestamp(end, tz="UTC") if end else pd.NaT, "id": f"{uid}-{start}", "extra": "{}"}
    rows = [
        rec("124", "Харківський район", "raion", "Kharkivska oblast", "2026-10-01 10:00", "2026-10-01 11:00"),
        rec("1324", "Берестинська територіальна громада", "hromada", "Kharkivska oblast", "2026-10-02 10:00", "2026-10-02 10:30"),
        rec("6293", "м. Харків", "city", "Kharkivska oblast", "2026-10-03 10:00", "2026-10-03 10:20"),
        rec("22", "Харківська область", "oblast", "Kharkivska oblast", "2026-10-04 10:00", "2026-10-04 10:40"),
        rec("122", "Чугуївський район", "raion", "Kharkivska oblast", "2026-10-11 19:30", None),  # still running
    ]
    store.append(root, "alerts_history", pd.DataFrame(rows).assign(fetched_at=pull, requested_uid="22"))
    store.append(root, "history_pulls", pd.DataFrame(
        [{"fetched_at": pull, "uid": u, "status": 200, "n_records": 1} for u, r in OBLAST_UID.items() if r != "Luhanska oblast"]))
    a, spans = load_store(root, as_of=pull + pd.Timedelta(minutes=5))
    assert spans == [(pull - pd.Timedelta(days=29), pull)]
    got = {(r.region, str(r.start)[:16]) for r in a.itertuples()}
    assert ("kharkivskyi", "2026-10-01 10:00") in got
    assert ("berestynskyi", "2026-10-02 10:00") in got  # hromada -> its raion
    assert ("kharkivskyi", "2026-10-03 10:00") in got  # city -> its raion
    assert {r for r, s in got if s == "2026-10-04 10:00"} == {"bohodukhivskyi", "iziumskyi", "berestynskyi",
                                                              "kupianskyi", "lozivskyi", "kharkivskyi", "chuhuivskyi"}
    running = a[(a["region"] == "chuhuivskyi") & (a["start"] == pd.Timestamp("2026-10-11 19:30", tz="UTC"))]
    assert running["end"].iloc[0] == pull  # open: known to run until the pull, never beyond
    # point in time: before the pull the store knows nothing
    assert load_store(root, as_of=pull - pd.Timedelta(minutes=1))[0].empty
    h = load_history(tmp_path / "missing.csv", root, as_of=pull + pd.Timedelta(minutes=5), download=False)
    assert h.covered(pull - pd.Timedelta(days=28), pull) and not h.covered(pull - pd.Timedelta(days=30), pull)
