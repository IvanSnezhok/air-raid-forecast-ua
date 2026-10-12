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
