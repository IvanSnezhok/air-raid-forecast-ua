"""Engine behaviour: live merge, model cache, map coverage."""
import json
from pathlib import Path

import pandas as pd

from airraid_app import live
from airraid_app.engine import ForecastEngine

from .conftest import REGIONS


def test_models_are_cached_and_reloaded(settings, engine):
    e2 = ForecastEngine(settings)
    e2.load_data()
    assert e2.load_models(), "cached models should load without retraining"
    assert e2.forecast()["regions"][0]["probs"] == engine.forecast()["regions"][0]["probs"]


def test_stale_cache_is_rejected(settings, engine, tmp_path):
    meta = json.loads((settings.model_dir / "meta.json").read_text())
    e2 = ForecastEngine(settings)
    e2.load_data()
    e2.hist_end = pd.Timestamp(meta["data_end"]) + pd.Timedelta(hours=settings.retrain_h + 1)
    assert not e2.load_models()


def test_live_alerts_change_the_forecast(settings, engine, monkeypatch, tmp_path):
    """An ongoing alert reported by alerts.in.ua must switch on `active_now` for that region."""
    now = engine.hist_end.ceil("h") + pd.Timedelta(minutes=30)
    started = (now - pd.Timedelta(hours=2)).isoformat()
    payload = {"alerts": [{"alert_type": "air_raid", "location_title": "Подільський район", "location_type": "raion",
                           "location_oblast": "Одеська область", "started_at": started}]}
    e = ForecastEngine(type(settings)(**{**vars(settings), "alerts_token": "test", "state_dir": tmp_path}))
    e.load_data()
    assert e.load_models()
    monkeypatch.setattr("airraid_app.engine.fetch_active", lambda token: payload)
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: now))
    f = e.forecast()
    odesa = next(r for r in f["regions"] if r["name"] == "Odeska oblast")
    assert f["live_source"] == "alerts.in.ua"
    assert f["origin"] == now.floor("h").isoformat()
    assert odesa["active_at_origin"] is True
    st = e.live_state()
    assert st.regions["Odeska oblast"].locations == ["Подільський район"]


def test_parse_active_ignores_other_alert_types_and_unknown_regions():
    now = pd.Timestamp("2026-10-11 10:00", tz="UTC")
    payload = {"alerts": [
        {"alert_type": "artillery_shelling", "location_oblast": "Харківська область", "started_at": "2026-10-11T09:00:00Z"},
        {"alert_type": "air_raid", "location_oblast": "Автономна Республіка Крим", "started_at": "2026-10-11T09:00:00Z"},
        {"alert_type": "air_raid", "location_title": "м. Київ", "location_type": "city", "started_at": "2026-10-11T09:10:00Z"},
        {"alert_type": "air_raid", "location_oblast": "м. Київ", "location_type": "oblast", "started_at": "2026-10-11T08:50:00Z"},
    ]}
    st = live.parse_active(payload, REGIONS, now)
    assert not st.regions["Kharkivska oblast"].active
    assert st.regions["Kyiv City"].active
    assert st.regions["Kyiv City"].since == pd.Timestamp("2026-10-11 08:50", tz="UTC")


def test_api_failure_falls_back_to_dataset(settings, engine, monkeypatch, tmp_path):
    def boom(token):
        raise TimeoutError("no network")

    e = ForecastEngine(type(settings)(**{**vars(settings), "alerts_token": "test", "state_dir": tmp_path}))
    e.load_data()
    monkeypatch.setattr("airraid_app.engine.fetch_active", boom)
    st = e.live_state()
    assert st.source == "dataset" and "alerts.in.ua" in st.error


def test_map_covers_every_dataset_region():
    from airraid.data import EXCLUDED_REGIONS
    from airraid_app.regions import UK_NAMES

    geo = json.loads((Path(__file__).resolve().parents[2] / "src/airraid_app/static/ukraine-map.json").read_text())
    drawn = set(geo["regions"]) | {"Kyiv City"}
    assert set(UK_NAMES) - EXCLUDED_REGIONS <= drawn
    assert set(geo["labels"]) >= set(UK_NAMES) - EXCLUDED_REGIONS
