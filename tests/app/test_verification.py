"""Live alert recording and checking issued forecasts against what happened."""
import numpy as np
import pandas as pd

from airraid.data import build_grid
from airraid.features import hourly_fraction
from airraid_app.engine import ForecastEngine
from airraid_app.journal import LiveRecorder
from airraid_app.live import LiveState, RegionState

T0 = pd.Timestamp("2026-10-11 10:00", tz="UTC")


def _state(minute, active):
    t = T0 + pd.Timedelta(minutes=minute)
    return LiveState("alerts.in.ua", t, {r: RegionState(r in active, T0 + pd.Timedelta(minutes=a) if r in active else None)
                                         for r, a in (("A", 0), ("B", 1))})


def test_recorder_turns_polls_into_intervals(tmp_path):
    rec = LiveRecorder(tmp_path / "live.csv")
    rec.observe(_state(1, {"A"}))
    rec.observe(_state(2, {"A", "B"}))
    rec.observe(_state(3, {"B"}))  # A ended
    df = rec.alerts()
    a = df[df["region"] == "A"].iloc[0]
    assert a["start"] == T0 and a["end"] == T0 + pd.Timedelta(minutes=3)
    assert (df["region"] == "B").sum() == 1  # still open, ends at last poll
    # coverage: polls started after the dataset ended -> nothing new is "known"
    assert rec.known_until(T0) == T0
    assert rec.known_until(T0 + pd.Timedelta(minutes=2)) == T0 + pd.Timedelta(minutes=3)
    # a long gap closes open alerts and restarts coverage
    rec.observe(_state(60, set()))
    assert rec.covered_since == T0 + pd.Timedelta(minutes=60)
    assert len(rec.alerts()) == 2
    # persisted and reloaded
    assert len(LiveRecorder(tmp_path / "live.csv").alerts()) == 2


def test_recorder_ignores_dataset_state(tmp_path):
    rec = LiveRecorder(tmp_path / "live.csv")
    rec.observe(LiveState("dataset", T0, {"A": RegionState(True, T0)}))
    assert rec.alerts().empty and rec.last_poll is None


def test_issued_forecasts_are_verified(settings, engine, tmp_path):
    e = ForecastEngine(type(settings)(**{**vars(settings), "state_dir": tmp_path}))
    e.load_data()
    assert e.load_models()
    grid = build_grid(e.alerts, data_end=e.hist_end.floor("h"))
    H = hourly_fraction(grid)
    origins = [e.hist_end.floor("h") - pd.Timedelta(hours=k) for k in (30, 20, 10)]
    rows = []
    for o in origins:
        for r in grid.regions:
            rows.append({"origin": o, "region": r, "target": "active", "horizon": 1, "p": 0.9, "clim": 0.5,
                         "issued_at": o, "live": False})
    e.journal.record(pd.DataFrame(rows))
    # the forecast for the current hour is journalled too, but its window is not observed yet
    e.forecast()
    v = e.verification(region="Kyiv City")
    assert v["n_verified"] == len(origins) and v["n_issued"] == len(origins) + 1
    s = next(x for x in v["scores"] if x["target"] == "active" and x["horizon"] == 1)
    ks = [grid.minute(o) // 60 for o in origins]
    y = np.array([H[grid.regions.index(r), k] > 0 for k in ks for r in grid.regions], float)
    assert s["n"] == len(y)
    assert abs(s["brier"] - np.mean((0.9 - y) ** 2)) < 1e-4
    assert abs(s["brier_clim"] - np.mean((0.5 - y) ** 2)) < 1e-4
    recent = v["recent"]["active_h1"]
    assert recent[-1]["y"] is None  # current hour: not known yet
    assert all(x["y"] in (0, 1) for x in recent[:-1])


def test_verification_endpoint(client):
    body = client.get("/api/verification").json()
    assert body["n_issued"] >= 1 and "scores" in body
    assert "verification" in client.get("/api/regions/odeska").json()
