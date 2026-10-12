"""The History tab's data (/api/history, /api/history/timeline) and the recalibration layer in the
10-minute pipeline: predict_now applies the newest map known at its origin and logs both numbers,
verify scores both, and the static site serves the History files (small, lazy-loaded)."""
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import build_static_site  # noqa: E402
import predict_now  # noqa: E402
from airraid import recalibration, store  # noqa: E402
from airraid.data import load_alerts  # noqa: E402
from airraid.features import HORIZONS  # noqa: E402
from airraid.verification import verify  # noqa: E402

from .test_pipeline_site import _poll, fake_now  # noqa: E402,F401  (fixture)


def test_history_endpoints(client):
    h = client.get("/api/history")
    assert h.status_code == 200
    body = h.json()
    for k in ("n_verified", "n_runs", "windows", "blocks", "reliability", "calibration"):
        assert k in body
    assert body["calibration"]["n_active"] == 0 and body["calibration"]["n_total"] == 8  # server mode: raw
    tl = client.get("/api/history/timeline")
    assert tl.status_code == 200 and {"start", "n_hours", "regions", "series"} <= set(tl.json())


def _cal(active_key, fitted_at, a, b):
    rows = []
    for t in ("active", "onset"):
        for h in HORIZONS:
            on = (t, h) == active_key
            rows.append({"target": t, "horizon": h, "active": on, "reason": "ok" if on else "collecting",
                         "a": a if on else 0.0, "b": b if on else 1.0, "n_rows": 0, "n_hours": 0, "n_eff": 0.0,
                         "n_fit_hours": 0, "n_check_hours": 0, "brier_raw": 0.2 if on else np.nan,
                         "brier_cal": 0.18 if on else np.nan, "gain": 0.1 if on else np.nan,
                         "data_from": pd.NaT, "data_to": pd.NaT, "fitted_at": fitted_at})
    df = pd.DataFrame(rows)
    for c in ("data_from", "data_to"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def test_pipeline_learns_from_verified_forecasts(settings, tmp_path, fake_now):
    alerts = load_alerts(settings.data_path)
    hist_end = alerts["start"].max()
    root, models = tmp_path / "store", tmp_path / "models"
    t = hist_end - pd.Timedelta(minutes=20)
    t_end = hist_end.ceil("h") + pd.Timedelta(hours=1, minutes=3)
    while t <= t_end:  # unbroken polls since before the dataset ended
        fake_now["now"] = t
        _poll(root, t)
        t += pd.Timedelta(minutes=10)
    fake_now["now"] = t - pd.Timedelta(minutes=10) + pd.Timedelta(seconds=40)

    # first run: no calibration in the store -> the model's own probability, tagged "none"
    log1, meta1 = predict_now.forecast(alerts, fake_now["now"], root, None, models, threads=2,
                                       reports_dir=settings.reports_dir)
    assert set(log1["calibration"]) == {"none"} and (log1["p_raw"] == log1["p"]).all()
    assert meta1["calibration_active"] == 0
    o1 = pd.Timestamp(meta1["origin_utc"], tz="UTC")

    # verify.py fitted a map (active for "alert in hour 1"); one fitted after the next origin must not be used
    store.append(root, "calibration", _cal(("active", 1), o1 + pd.Timedelta(minutes=5), -0.5, 0.5))
    fake_now["now"] = o1 + pd.Timedelta(minutes=9, seconds=20)
    _poll(root, fake_now["now"])
    o2 = fake_now["now"].ceil("min")  # the next origin: the first whole minute after the poll
    fake_now["now"] += pd.Timedelta(seconds=30)
    store.append(root, "calibration", _cal(("onset", 3), o2 + pd.Timedelta(minutes=1), 1.0, 1.0))
    log2, meta2 = predict_now.forecast(alerts, fake_now["now"], root, None, models, threads=2,
                                       reports_dir=settings.reports_dir)
    assert pd.Timestamp(meta2["origin_utc"], tz="UTC") == o2 and meta2["calibration_active"] == 1
    a1 = (log2["target"] == "active") & (log2["horizon"] == 1)
    assert (log2.loc[a1, "calibration"] == "platt").all() and (log2.loc[~a1, "calibration"] == "none").all()
    np.testing.assert_allclose(log2.loc[a1, "p"], np.round(recalibration.transform(log2.loc[a1, "p_raw"], -0.5, 0.5), 4))
    assert (log2.loc[~a1, "p"] == log2.loc[~a1, "p_raw"]).all()  # incl. onset 3 h: its map came after the origin
    stored = store.load(root, "forecasts")
    assert {"p_raw", "calibration"} <= set(stored) and len(stored) == len(log1) + len(log2)
    s = replace(settings, model_dir=models, store_dir=root, state_dir=tmp_path / "state", type_forecast=None,
                train_months=12, max_train_months=24, n_trees=300)
    build_static_site.build(tmp_path / "site", s)
    fc = json.loads((tmp_path / "site" / "api" / "forecast.json").read_text())
    assert pd.Timestamp(fc["origin"]) == o2
    served = {r: p for r, tg, h, p in zip(log2["region"], log2["target"], log2["horizon"], log2["p"]) if (tg, h) == ("active", 1)}
    for reg in fc["regions"]:
        assert reg["probs"]["active"]["1"] == served[reg["name"]]  # the site serves the corrected number

    # an hour later both runs' first hours are over: verify scores served and raw
    t = fake_now["now"]
    while t <= o2 + pd.Timedelta(minutes=75):
        fake_now["now"] = t
        _poll(root, t)
        t += pd.Timedelta(minutes=10)
    fake_now["now"] = t
    v, summary = verify(store.load(root, "forecasts"), alerts, root, fake_now["now"])
    assert set(v["horizon"]) == {1} and v["origin"].nunique() == 2
    w = v[(v["origin"] == o2) & (v["target"] == "active")]
    np.testing.assert_allclose(w["brier_raw"], (w["p_raw"] - w["y"]) ** 2)
    assert not np.allclose(w["brier_raw"], w["brier_model"])
    store.append(root, "verification", v.assign(verified_at=fake_now["now"]))
    store.append(root, "calibration", recalibration.fit(v.assign(verified_at=fake_now["now"]), fake_now["now"]))

    out = tmp_path / "site2"
    build_static_site.build(out, s)
    hist = json.loads((out / "api" / "history.json").read_text())
    assert hist["n_verified"] == len(v) and hist["n_runs_verified"] == 2
    a1h = next(r for r in hist["windows"]["24h"] if r["target"] == "active" and r["horizon"] == 1)
    assert a1h["n_calibrated"] == len(w) and a1h["brier_raw"] != a1h["brier"]
    assert a1h["brier_pers"] is not None
    on1 = next(r for r in hist["windows"]["24h"] if r["target"] == "onset" and r["horizon"] == 1)
    assert on1["brier_pers"] is not None  # onset persistence from the alert record at each origin
    assert hist["calibration"]["n_total"] == 8 and hist["calibration"]["fitted_at"] is not None
    assert "raw" in hist["reliability"]["active_h1"] or not hist["reliability"]["active_h1"]["served"]
    tl = json.loads((out / "api" / "history" / "timeline.json").read_text())
    s1 = tl["series"]["active_h1"]
    assert len(tl["regions"]) == len(s1["p"]) and all(len(row) == tl["n_hours"] for row in s1["p"])
    assert sum(y is not None for row in s1["y"] for y in row) == len(tl["regions"]) * 2
    for f in ("history.json", "history/timeline.json"):
        assert (out / "api" / f).stat().st_size < 1_000_000
    html = (out / "index.html").read_text()
    assert 'href="#history"' in html and 'id="view-history"' in html
