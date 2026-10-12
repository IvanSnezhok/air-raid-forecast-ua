"""The 10-minute pipeline end to end on synthetic data: poll rows in the store ->
predict_now.forecast (sub-hour origin) -> verify -> static site in store mode.

The site must serve exactly the forecast that was logged to the store (no
second model, no second forecast), its "now" must be the forecast's active_now,
and its verification must come from the store.
"""
import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import build_static_site  # noqa: E402
import predict_now  # noqa: E402
from airraid import store  # noqa: E402
from airraid.data import load_alerts  # noqa: E402
from airraid.verification import verify  # noqa: E402
from airraid_app.config import Settings  # noqa: E402,F401
from airraid_app.raion_engine import RaionForecaster  # noqa: E402

from .test_engine_text import _archive  # noqa: E402


def _poll(root, t, odesa_since=None):
    rows = []
    if odesa_since is not None:
        rows.append({"id": "1", "uid": "18", "oblast": "Odeska oblast", "oblast_uid": "18", "title": "Одеська область",
                     "level": "oblast", "raion": None, "started_at": odesa_since, "finished_at": pd.NaT,
                     "alert_type": "air_raid", "extra": "{}", "observed_at": t})
    if rows:
        store.append(root, "alerts_active", pd.DataFrame(rows))
    store.append(root, "polls", pd.DataFrame({"observed_at": [t], "status": [200], "n_records": [len(rows)]}))


@pytest.fixture()
def fake_now(monkeypatch):
    clock = {}
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: clock["now"]))
    return clock


def test_pipeline_site_serves_the_stored_forecast(settings, tmp_path, fake_now):
    alerts = load_alerts(settings.data_path)
    hist_end = alerts["start"].max()
    root, models = tmp_path / "store", tmp_path / "models"
    t_poll = hist_end.ceil("h") + pd.Timedelta(hours=2, minutes=3, seconds=40)  # a poll at hh:03:40
    since = t_poll - pd.Timedelta(minutes=25)
    t = hist_end - pd.Timedelta(minutes=20)
    while t <= t_poll:  # unbroken polls since before the dataset ended
        fake_now["now"] = t
        _poll(root, t, since if t >= since else None)
        t += pd.Timedelta(minutes=10)
    last_poll = t - pd.Timedelta(minutes=10)
    fake_now["now"] = last_poll + pd.Timedelta(seconds=50)

    log, meta = predict_now.forecast(alerts, fake_now["now"], root, None, models, threads=2,
                                     reports_dir=settings.reports_dir)
    origin = pd.Timestamp(meta["origin_utc"], tz="UTC")
    assert origin == last_poll.ceil("min") and origin.minute % 10 != 0  # sub-hour origin, after the poll
    odesa = log[(log["region"] == "Odeska oblast")]
    assert set(odesa["active_now"]) == {1}
    assert (store.load(root, "forecasts")["p"].to_numpy() == log["p"].to_numpy()).all()

    s = replace(settings, model_dir=models, store_dir=root, state_dir=tmp_path / "state", type_forecast=None,
                train_months=12, max_train_months=24, n_trees=300)
    out = tmp_path / "site"
    build_static_site.build(out, s)
    fc = json.loads((out / "api" / "forecast.json").read_text())
    assert pd.Timestamp(fc["origin"]) == origin
    want = {(r, t, int(h)): p for r, t, h, p in zip(log["region"], log["target"], log["horizon"], log["p"])}
    for reg in fc["regions"]:
        for t, by_h in reg["probs"].items():
            for h, p in by_h.items():
                assert p == pytest.approx(want[(reg["name"], t, int(h))], abs=1e-9)  # the logged numbers
    st = json.loads((out / "api" / "state.json").read_text())
    active = {r["name"]: r["active"] for r in st["regions"]}
    assert active["Odeska oblast"] and pd.Timestamp(st["as_of"]) == last_poll
    assert active == {r: bool(g["active_now"].iloc[0]) for r, g in log.groupby("region")}  # "now" = active_now
    assert json.loads((out / "api" / "status.json").read_text())["origin"] == fc["origin"]

    # 70 minutes later the first hour is over: verify from the store, rebuild the site
    t = last_poll + pd.Timedelta(minutes=10)
    while t <= last_poll + pd.Timedelta(minutes=70):
        fake_now["now"] = t
        _poll(root, t, since if t < origin + pd.Timedelta(minutes=12) else None)
        t += pd.Timedelta(minutes=10)
    fake_now["now"] = t
    v, summary = verify(store.load(root, "forecasts"), alerts, root, fake_now["now"])
    assert set(v["horizon"]) == {1} and set(v["label_source"]) == {"store"}
    store.append(root, "verification", v.assign(verified_at=fake_now["now"]))
    y = v.set_index(["region", "target"])["y"]
    assert y[("Odeska oblast", "active")] == 1  # the Odesa alert ran into the first hour
    out2 = tmp_path / "site2"
    build_static_site.build(out2, s)
    ver = json.loads((out2 / "api" / "verification.json").read_text())
    assert ver["n_verified"] == 1 and ver["n_issued"] >= 1 and ver["label_sources"] == {"store": len(v)}
    a1 = next(x for x in ver["scores_24h"] if x["target"] == "active" and x["horizon"] == 1)
    assert a1["n"] == len(log["region"].unique())
    strip = json.loads((out2 / "api" / "regions" / "odeska.json").read_text())["verification"]["active_h1"]
    assert [x["y"] for x in strip] == [1]  # the verified forecast is shown with its outcome


def test_pipeline_site_with_text_and_raion_layers(settings, tmp_path, fake_now, monkeypatch):
    """All layers as the pipeline runs them: the train job's text and raion models, then a
    sub-hour forecast served by the history + Telegram model, and a site build that only
    loads the saved raion models (AIRRAID_RAION_TRAIN=0) and serves the logged numbers."""
    monkeypatch.setattr("airraid.text.telegram.FEATURE_CHANNELS", ["kpszsu"])
    monkeypatch.setattr("airraid.text.features.FEATURE_CHANNELS", ["kpszsu"])
    monkeypatch.setattr("airraid.text.live.fetch", lambda *a, **k: {})  # offline
    alerts = load_alerts(settings.data_path)
    hist_end = alerts["start"].max()
    root, models, reports, text = tmp_path / "store", tmp_path / "models", tmp_path / "reports", tmp_path / "text-data"
    reports.mkdir()
    sel = {f"lightgbm_text|{t}_h{h}": {"trees": 20} for t in ("active", "onset") for h in (1, 2, 3, 6)}
    (reports / "text_selection.json").write_text(json.dumps(sel))
    _archive(text, settings.data_path, "2030-01-01T00:00:00Z")
    t_poll = hist_end.ceil("h") + pd.Timedelta(hours=1, minutes=13, seconds=20)
    t = hist_end - pd.Timedelta(minutes=20)
    while t <= t_poll:
        fake_now["now"] = t
        _poll(root, t, t_poll - pd.Timedelta(minutes=30) if t >= t_poll - pd.Timedelta(minutes=30) else None)
        t += pd.Timedelta(minutes=10)
    fake_now["now"] = t - pd.Timedelta(minutes=10) + pd.Timedelta(seconds=30)

    log, meta = predict_now.forecast(alerts, fake_now["now"], root, None, models, threads=2, reports_dir=reports,
                                     text_root=text, text_fetch=False)
    origin = pd.Timestamp(meta["origin_utc"], tz="UTC")
    assert origin.minute % 10 != 0 and "lightgbm_text" in meta["model_served"] and meta["text_status"] == "ok"
    assert set(log["model"]) <= {"lightgbm", "lightgbm_text"} and "lightgbm_text" in set(log["model"])

    s = replace(settings, model_dir=models, store_dir=root, state_dir=tmp_path / "state", type_forecast=None,
                reports_dir=reports, text_root=text, train_months=12, max_train_months=24, n_trees=300)
    rf = RaionForecaster(s)  # what the train job does (scripts/train_models.py --raions)
    rf.load(False)
    rf.prepare(alerts, fake_now["now"], 2)
    rf.save(models / "raions")

    def no_raion_training(*a, **k):
        raise AssertionError("the site build must not train raion models")
    monkeypatch.setattr(RaionForecaster, "_train", no_raion_training)
    monkeypatch.setenv("AIRRAID_TEXT_TRAIN", "0")
    out = tmp_path / "site"
    build_static_site.build(out, replace(s, raion_train=False))
    fc = json.loads((out / "api" / "forecast.json").read_text())
    assert pd.Timestamp(fc["origin"]) == origin and "lightgbm_text" in fc["model"]["served"]
    assert fc["model"]["text_status"] == "ok"
    rz = json.loads((out / "api" / "raions.json").read_text())
    assert rz["models_source"] == "saved" and pd.Timestamp(rz["origin"]) == origin
    odesa = [r for r in rz["raions"] if r["oblast"] == "Odeska oblast"]
    assert odesa and all(r["live"]["active"] for r in odesa)  # the oblast-level alert of the same poll
    assert (out / "api" / "raions" / f"{odesa[0]['id']}.json").exists()
