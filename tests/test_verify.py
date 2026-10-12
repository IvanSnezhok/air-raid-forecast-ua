"""verify.py must score forecasts with exactly the labels the models were trained on."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airraid.data import build_grid, load_alerts
from airraid.features import HORIZONS, hourly_fraction, hourly_starts, labels
from verify import outcomes


def test_outcomes_match_training_labels():
    g = build_grid(load_alerts())
    H, S = hourly_fraction(g), hourly_starts(g)
    rng = np.random.default_rng(0)
    ks = rng.integers(g.n_minutes // 60 - 24 * 60, g.n_minutes // 60 - 6, size=5)
    rows = []
    for k in ks:
        for h in HORIZONS:
            for t in ("active", "onset"):
                y = labels(g, np.array([k]), h, H, t, S)
                rows += [{"origin": g.t0 + pd.Timedelta(hours=int(k)), "region": r, "target": t,
                          "horizon": h, "expected": y[i]} for i, r in enumerate(g.regions)]
    log = pd.DataFrame(rows)
    np.testing.assert_array_equal(outcomes(log, g), log["expected"].to_numpy())


def test_store_labels_after_the_dataset(tmp_path):
    """With a store, forecasts after the end of the dataset are verified from live records."""
    from airraid import store
    from verify import verify

    T = pd.Timestamp("2026-10-11 04:36", tz="UTC")  # dataset ends here (last start)
    rng = np.random.default_rng(1)
    starts = T - pd.to_timedelta(np.sort(rng.integers(0, 40 * 24 * 60, 600)), unit="min")
    alerts = pd.DataFrame({"region": rng.choice(["Kharkivska oblast", "Lvivska oblast"], 600), "start": starts,
                           "end": starts + pd.Timedelta(minutes=20), "naive": False})
    alerts.loc[alerts["start"].idxmax(), "start"] = T
    alerts = alerts.sort_values("start").reset_index(drop=True)
    m = lambda x: T + pd.Timedelta(minutes=x)  # noqa: E731
    rec = {"uid": "122", "oblast": "Kharkivska oblast", "level": "raion", "started_at": m(55), "finished_at": pd.NaT,
           "alert_type": "air_raid"}
    for t in range(-10, 301, 10):  # polls every 10 min from before the dataset end
        store.append(tmp_path, "polls", pd.DataFrame({"observed_at": [m(t)], "n_records": [int(60 <= t <= 90)]}))
        if 60 <= t <= 90:
            store.append(tmp_path, "alerts_active", pd.DataFrame([{**rec, "observed_at": m(t)}]))
    o = lambda h: T.floor("h") + pd.Timedelta(hours=h)  # noqa: E731
    log = pd.DataFrame([{"origin": o(k), "region": "Kharkivska oblast", "target": t, "horizon": 1, "p": 0.5,
                         "clim": 0.2, "issued_at": o(k)} for k in (-1, 1, 5) for t in ("active", "onset")])
    v, summary = verify(log, alerts, tmp_path, now=m(301))
    got = {(r.origin, r.target): (r.y, r.label_source) for r in v.itertuples()}
    # 05:00-06:00: the live alert that started 05:31 -> active and onset, from the store
    assert got[(o(1), "active")] == (1, "store") and got[(o(1), "onset")] == (1, "store")
    assert got[(o(-1), "active")][1] == "dataset"
    assert (o(5), "active") not in got and summary["pending"] == 2  # 09:00-10:00 is not covered yet
    v0, _ = verify(log, alerts, None, now=m(301))
    assert set(v0["label_source"]) == {"dataset"} and len(v0) == 2  # without the store: dataset only
    # appended once; a second run adds nothing
    assert store.append(tmp_path, "verification", v.assign(verified_at=m(301)))
    assert store.append(tmp_path, "verification", v.assign(verified_at=m(400))) == []


def test_subhour_origins_are_scored_with_the_shifted_labels():
    """A forecast from 14:20 is scored on 14:20-15:20 (minutes from the origin), exactly the
    label the features/labels code gives for a grid whose blocks end at :20."""
    alerts = load_alerts()
    plain = build_grid(alerts)  # verify's label grid: blocks on the hour
    shifted = build_grid(alerts, data_end=alerts["start"].max().floor("h") - pd.Timedelta(minutes=40))
    H, S = hourly_fraction(shifted), hourly_starts(shifted)
    rng = np.random.default_rng(2)
    rows = []
    for k in rng.integers(shifted.n_minutes // 60 - 24 * 60, shifted.n_minutes // 60 - 6, size=5):
        origin = shifted.t0 + pd.Timedelta(hours=int(k))
        assert origin.minute == 20
        for h in HORIZONS:
            for t in ("active", "onset"):
                y = labels(shifted, np.array([k]), h, H, t, S)
                rows += [{"origin": origin, "region": r, "target": t, "horizon": h, "expected": y[i]}
                         for i, r in enumerate(shifted.regions)]
    log = pd.DataFrame(rows)
    np.testing.assert_array_equal(outcomes(log, plain), log["expected"].to_numpy())


def test_poll_runs_after_an_outage_verify_the_windows_they_cover(tmp_path):
    """Polls that resume after a gap (no history pull since) verify windows that start at least
    LIVE_EXPIRY_HOURS into the new run; windows touching the gap stay pending."""
    from airraid import store
    from verify import verify

    T = pd.Timestamp("2026-10-11 04:36", tz="UTC")
    alerts = pd.DataFrame({"region": ["Kharkivska oblast", "Lvivska oblast"] * 50,
                           "start": [T - pd.Timedelta(hours=k) for k in range(100)], "naive": False})
    alerts["end"] = alerts["start"] + pd.Timedelta(minutes=20)
    m = lambda x: T + pd.Timedelta(minutes=x)  # noqa: E731
    rec = {"uid": "122", "oblast": "Kharkivska oblast", "level": "raion", "started_at": m(320), "finished_at": pd.NaT,
           "alert_type": "air_raid"}
    for t in list(range(-10, 61, 10)) + list(range(180, 401, 10)):  # outage between +60 and +180 min
        store.append(tmp_path, "polls", pd.DataFrame({"observed_at": [m(t)], "n_records": [int(320 <= t <= 340)]}))
        if 320 <= t <= 340:
            store.append(tmp_path, "alerts_active", pd.DataFrame([{**rec, "observed_at": m(t)}]))
    log = pd.DataFrame([{"origin": m(o), "region": "Kharkivska oblast", "target": "active", "horizon": 1, "p": 0.5,
                         "clim": 0.2, "issued_at": m(o)} for o in (30, 120, 190, 250, 345)])
    v, summary = verify(log, alerts, tmp_path, now=m(401))
    got = {int((r.origin - T) / pd.Timedelta(minutes=1)): r.y for r in v.itertuples()}
    # +250: window 250-310 inside the new run (lead-in ends at 240); the alert at +320 is after it
    assert got == {250: 0}
    assert summary["pending"] == 4  # +30/+120 touch the outage, +190 is in the lead-in, +345 not over yet
    v2, _ = verify(log.assign(horizon=2), alerts, tmp_path, now=m(401))
    assert {int((r.origin - T) / pd.Timedelta(minutes=1)): r.y for r in v2.itertuples()} == {250: 1}  # 310-370 has it


def test_site_strip_prefers_the_verified_forecast_of_each_hour():
    from airraid.verification import site_summary

    o = pd.Timestamp("2026-10-11 23:00", tz="UTC")
    J = pd.DataFrame([{"origin": o + pd.Timedelta(minutes=m), "region": "Lvivska oblast", "target": "active",
                       "horizon": 1, "p": 0.1, "clim": 0.2, "y": y} for m, y in ((7, np.nan), (37, 0.0), (57, np.nan))])
    out = site_summary(J, o + pd.Timedelta(hours=2), region="Lvivska oblast")
    assert [(x["origin"][11:16], x["y"]) for x in out["recent"]["active_h1"]] == [("23:37", 0)]
    assert out["n_verified"] == 1 and out["n_issued"] == 3
