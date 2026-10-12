"""The history + Telegram model serves only when the archive covers the origin; otherwise
the history-only model does, and the caller is told which one served."""
import json

import numpy as np
import pandas as pd
import pytest

from airraid.data import build_grid
from airraid.features import add_horizon_features, build_features, hourly_fraction, hourly_starts
from airraid.models import GBM
from airraid.text import archive
from airraid.text import features as tf
from airraid.text.serving import TextServing

UA = {"Kyiv City": "Київ", "Kharkivska oblast": "Харків", "Lvivska oblast": "Львів", "Odeska oblast": "Одесу"}


def synthetic_alerts(days=75, seed=0):
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-07-01", tz="UTC")
    rate = {"Kyiv City": 0.08, "Kharkivska oblast": 0.15, "Lvivska oblast": 0.03, "Odeska oblast": 0.07}
    rows = []
    for hour in range(days * 24):
        t = start + pd.Timedelta(hours=hour)
        for region, p in rate.items():
            if rng.random() < p * (2 if t.hour in (22, 23, 0, 1, 2, 3) else 1):
                s = t + pd.Timedelta(minutes=int(rng.integers(0, 60)))
                rows.append((region, s, s + pd.Timedelta(minutes=int(rng.integers(10, 150))), False))
    return pd.DataFrame(rows, columns=["region", "start", "end", "naive"])


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    d = tmp_path_factory.mktemp("text")
    alerts = synthetic_alerts()
    # few trees keep the test fast (the real counts come from run.py's selection)
    sel = {f"lightgbm_text|{t}_h{h}": {"trees": 20, "val_brier": 0.1} for t in ("active", "onset") for h in (1, 2, 3, 6)}
    (d / "text_selection.json").write_text(json.dumps(sel))
    # a post naming the region 20-40 minutes before most alert starts
    rng = np.random.default_rng(0)
    rows = [{"channel": "kpszsu", "id": i + 1, "date": s - pd.Timedelta(minutes=int(rng.integers(20, 40))),
             "edited": False, "text": f"БпЛА курсом на {UA[r]}"}
            for i, (r, s) in enumerate(zip(alerts["region"], alerts["start"])) if rng.random() < 0.7]
    obs = pd.DataFrame(rows)
    obs["seen_at"] = obs["date"] + pd.Timedelta(minutes=2)
    archive.upsert(d, "telegram", "kpszsu", obs)
    end = alerts["start"].max().floor("h")
    archive.write_state(d, "telegram", "kpszsu", {"complete_from": "2026-07-01T00:00:00Z",
                                                  "complete_until": archive._iso(end - pd.Timedelta(hours=30))})
    grid = build_grid(alerts, data_end=end)
    H, S = hourly_fraction(grid), hourly_starts(grid)
    return d, grid, H, S, end


def test_text_model_serves_when_covered_and_falls_back_otherwise(setup, monkeypatch):
    d, grid, H, S, end = setup
    monkeypatch.setattr("airraid.text.telegram.FEATURE_CHANNELS", ["kpszsu"])
    monkeypatch.setattr("airraid.text.features.FEATURE_CHANNELS", ["kpszsu"])
    hist_end = end - pd.Timedelta(hours=30)
    text = TextServing(d, d / "models", reports_dir=d, fetch=False)
    assert text.ensure_models(grid, H, S, hist_end)
    k_cov = grid.minute(hist_end) // 60 - 2
    fallback = GBM(n_estimators=5).fit(*_xy(grid, H, S, k_cov))
    rows, status = text.rows_now(grid, k_cov)
    assert status == "ok"
    x = add_horizon_features(build_features(grid, np.array([k_cov]), H), grid, H, 1, S)
    p, served = text.predict(x, "onset", 1, rows, fallback)
    assert served == "lightgbm_text" and p.shape == (len(grid.regions),) and np.all((p > 0) & (p < 1))
    # same probabilities as the frame built the way training builds it (column order matters)
    as_trained = add_horizon_features(tf.attach(build_features(grid, np.array([k_cov]), H), rows), grid, H, 1, S)
    np.testing.assert_allclose(p, text.models[("onset", 1)].predict(as_trained))

    # the archive stops 30 h before the end of the history: no text rows, history model serves
    k_late = grid.n_minutes // 60 - 1
    rows, status = text.rows_now(grid, k_late)
    assert rows is None and "does not cover" in status
    x = add_horizon_features(build_features(grid, np.array([k_late]), H), grid, H, 1, S)
    p, served = text.predict(x, "onset", 1, rows, fallback)
    assert served == "lightgbm"
    np.testing.assert_allclose(p, fallback.predict(x))

    # where the text model lost on the validation month, the history model serves
    sel = json.loads((d / "text_selection.json").read_text())
    sel["lightgbm_text|onset_h1"]["val_brier"], sel["lightgbm_cov|onset_h1"] = 0.2, {"trees": 5, "val_brier": 0.1}
    (d / "text_selection.json").write_text(json.dumps(sel))
    rows, _ = text.rows_now(grid, k_cov)
    x = add_horizon_features(build_features(grid, np.array([k_cov]), H), grid, H, 1, S)
    assert text.predict(x, "onset", 1, rows, fallback)[1] == "lightgbm"
    assert text.predict(add_horizon_features(build_features(grid, np.array([k_cov]), H), grid, H, 2, S),
                        "onset", 2, rows, fallback)[1] == "lightgbm_text"

    # boosters are cached and reloaded
    again = TextServing(d, d / "models", reports_dir=d, fetch=False)
    assert again.load(hist_end, 24) and set(again.models) == set(text.models)


def _xy(grid, H, S, k):
    K = np.arange(k - 24 * 7, k)
    x = add_horizon_features(build_features(grid, K, H), grid, H, 1, S)
    y = np.resize([0.0, 1.0], len(x))
    return x, y


def test_text_training_off_serves_saved_models_or_the_history_model(setup, monkeypatch, tmp_path):
    """AIRRAID_TEXT_TRAIN=0 (the 10-minute pipeline): never train; serve boosters a separate job
    saved, whatever their age, else say why and let the history-only model serve."""
    d, grid, H, S, end = setup
    monkeypatch.setattr("airraid.text.telegram.FEATURE_CHANNELS", ["kpszsu"])
    monkeypatch.setattr("airraid.text.features.FEATURE_CHANNELS", ["kpszsu"])
    hist_end = end - pd.Timedelta(hours=30)
    saved = tmp_path / "text"
    TextServing(d, saved, reports_dir=d, fetch=False).train(grid, H, S, hist_end - pd.Timedelta(days=5))

    def no_training(*a, **k):
        raise AssertionError("text models must not be trained with AIRRAID_TEXT_TRAIN=0")
    monkeypatch.setattr(TextServing, "train", no_training)
    monkeypatch.setenv("AIRRAID_TEXT_TRAIN", "0")
    off = TextServing(d, saved, reports_dir=d, fetch=False)
    assert off.allow_train is False
    assert off.ensure_models(grid, H, S, hist_end, retrain_hours=24)  # 5 days old: still served
    assert off.status.startswith("loaded") and off.models

    none = TextServing(d, tmp_path / "nothing", reports_dir=d, fetch=False)
    assert not none.ensure_models(grid, H, S, hist_end) and "AIRRAID_TEXT_TRAIN=0" in none.status
    rows, status = none.rows_now(grid, grid.minute(hist_end) // 60 - 2)
    assert rows is None and "AIRRAID_TEXT_TRAIN=0" in status
    k = grid.minute(hist_end) // 60 - 2
    fallback = GBM(n_estimators=5).fit(*_xy(grid, H, S, k))
    x = add_horizon_features(build_features(grid, np.array([k]), H), grid, H, 1, S)
    assert none.predict(x, "onset", 1, rows, fallback)[1] == "lightgbm"
