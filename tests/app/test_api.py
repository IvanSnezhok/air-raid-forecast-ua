"""HTTP API contract of the web app."""
from airraid.features import HORIZONS

from .conftest import REGIONS


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["live_api_configured"] is False


def test_regions(client):
    ids = {r["id"] for r in client.get("/api/regions").json()}
    assert ids == {"kyiv-city", "kharkivska", "lvivska", "odeska"}


def test_state_falls_back_to_dataset(client):
    body = client.get("/api/state").json()
    assert body["source"] == "dataset"
    assert {r["name"] for r in body["regions"]} == set(REGIONS)
    assert body["n_active"] == sum(r["active"] for r in body["regions"])


def test_forecast_shape_and_range(client):
    body = client.get("/api/forecast").json()
    assert body["horizons"] == list(HORIZONS)
    assert body["live_source"] == "dataset" and body["warnings"]
    # the same warnings as language-neutral codes, translated by the UI
    assert len(body["warning_codes"]) == len(body["warnings"]) and body["warning_codes"][0]["code"] == "no_live"
    assert len(body["regions"]) == len(REGIONS)
    for r in body["regions"]:
        for t in ("active", "onset"):
            probs = r["probs"][t]
            assert set(map(int, probs)) == set(HORIZONS)
            assert all(0 <= p <= 1 for p in probs.values())
    # onset within N hours can only grow with N (cumulative event); allow model noise
    for r in body["regions"]:
        o = r["probs"]["onset"]
        assert o[str(max(HORIZONS))] >= o["1"] - 0.15


def test_forecast_windows_follow_origin(client):
    body = client.get("/api/forecast").json()
    w = body["windows"]
    assert w["1"]["active"][0] == body["origin"]
    assert w["1"]["onset"][0] == body["origin"]


def test_region_detail(client):
    r = client.get("/api/regions/kharkivska")
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Kharkivska oblast" and d["name_uk"] == "Харківська"
    assert len(d["history"]["hourly"]) == 72
    assert len(d["hour_profile_28d"]) == 24
    assert "active_h1" in d["holdout"]
    assert len(d["holdout"]["active_h1"]["p"]) == len(d["holdout"]["active_h1"]["y"])


def test_unknown_region_is_404(client):
    assert client.get("/api/regions/atlantis").status_code == 404


def test_metrics(client):
    m = client.get("/api/metrics").json()
    scores = m["holdout"]["scores"]
    models = {s["model"] for s in scores}
    assert models == {"lightgbm", "climatology_28d", "persistence"}
    assert {(s["target"], s["horizon"]) for s in scores} == {(t, h) for t in ("active", "onset") for h in HORIZONS}
    assert m["walk_forward"] is None  # no reports/metrics_test.csv in the temp dir


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Повітряні тривоги" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/ukraine-map.json").json()["regions"]


def test_not_ready_returns_503(settings):
    from fastapi.testclient import TestClient

    from airraid_app.api import create_app
    from airraid_app.engine import ForecastEngine

    with TestClient(create_app(settings, ForecastEngine(settings))) as c:
        r = c.get("/api/forecast")
        assert r.status_code == 503 and r.json()["detail"]["code"] == {"code": "starting"}  # the UI translates the code
        assert c.get("/api/health").json()["status"] == "starting"


def test_threat_types(client):
    f = client.get("/api/forecast").json()
    assert f["threats"]["horizons"] == [1, 3]
    assert {t["id"] for t in f["threats"]["types"]} == {"drone", "massed_drone", "kab", "missile", "ballistic"}
    by = {r["name"]: r["types"] for r in f["regions"]}
    assert by["Kyiv City"]["1"]["most_likely"] == "drone"
    assert by["Kyiv City"]["3"]["p_type"]["missile"] == 0.15
    assert by["Odeska oblast"] is None
    t = client.get("/api/threats").json()
    assert t["origin"] == "2026-09-13 10:00" and len(t["regions"]) == 3
    assert client.get("/api/regions/kyiv-city").json()["types"]["1"]["level"] == "yellow"


def test_threat_types_missing_is_optional(settings, engine):
    from fastapi.testclient import TestClient

    from airraid_app.api import create_app
    from airraid_app.config import Settings

    s = Settings(**{**vars(settings), "type_forecast": str(settings.data_path.parent / "nope.csv")})
    from airraid_app.engine import ForecastEngine

    e = ForecastEngine(s)
    e.bootstrap()
    with TestClient(create_app(s, e)) as c:
        assert c.get("/api/forecast").json()["threats"] is None
        assert c.get("/api/threats").status_code == 404
