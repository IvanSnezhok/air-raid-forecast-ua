"""Learning from verified forecasts (airraid.recalibration): point in time, guarded, and
compatible with store rows written before the layer existed."""
import shutil

import numpy as np
import pandas as pd
import pytest

from airraid import recalibration as rc
from airraid import store
from airraid.features import HORIZONS
from airraid.training import TARGETS

REGIONS = [f"R{i}" for i in range(24)]
T0 = pd.Timestamp("2026-10-01 00:00", tz="UTC")


def _verified(days: float, true_map=lambda p: p, seed: int = 0, step_min: int = 10, targets=TARGETS,
              horizons=HORIZONS) -> pd.DataFrame:
    """Verified rows as verify.py writes them: a run every `step_min` minutes for every region,
    target and horizon; y ~ Bernoulli(true_map(p_raw)); verified (and recorded) once the window closed."""
    rng = np.random.default_rng(seed)
    origins = T0 + pd.to_timedelta(np.arange(0, int(days * 24 * 60), step_min), unit="min")
    rows = []
    for t in targets:
        for h in horizons:
            # one latent risk per region and hour, shared by its 10-minute runs (they overlap)
            risk = rng.beta(0.6, 1.2, size=(len(REGIONS), int(days * 24) + 2))
            for o in origins:
                k = int((o - T0) / pd.Timedelta(hours=1))
                p_raw = np.clip(risk[:, k] + rng.normal(0, 0.02, len(REGIONS)), 0.005, 0.995)
                y = (rng.random(len(REGIONS)) < true_map(p_raw)).astype(float)
                done = o + pd.Timedelta(hours=h) + pd.Timedelta(minutes=10)
                rows.append(pd.DataFrame({"origin": o, "region": REGIONS, "target": t, "horizon": h, "p": p_raw.round(4),
                                          "clim": 0.3, "y": y, "issued_at": o, "verified_at": done,
                                          "label_source": "store"}))
    return pd.concat(rows, ignore_index=True)


def overconfident(p):
    """What really happens when the model is too sure of itself (and too high on average)."""
    return rc.transform(p, -0.4, 0.55)


def test_targets_match_the_models():
    assert rc.TARGETS == TARGETS


def test_too_little_data_serves_the_raw_probability():
    V = _verified(1.0, overconfident)  # one day: nothing left to test on after a full day of fitting
    cal = rc.fit(V, V["verified_at"].max())
    assert len(cal) == len(TARGETS) * len(HORIZONS)
    assert not cal["active"].any() and set(cal["reason"]) == {"collecting"}
    log = V[V["origin"] == V["origin"].max()].drop(columns=["y"])
    out = rc.apply(log, cal)
    assert (out["p"] == log["p"]).all() and (out["p_raw"] == log["p"]).all() and set(out["calibration"]) == {"none"}
    assert rc.apply(log, None)["p"].equals(log["p"])


def test_activates_only_where_it_helps():
    """Overconfident model for "active" -> the map is learned and served; already calibrated
    "onset" -> it does not lower the Brier score on the check day, identity."""
    V = pd.concat([_verified(3.5, overconfident, seed=1, targets=("active",), horizons=(1, 3)),
                   _verified(3.5, lambda p: p, seed=2, targets=("onset",), horizons=(1, 3))], ignore_index=True)
    as_of = V["verified_at"].max()
    cal = rc.fit(V, as_of).set_index(["target", "horizon"])
    for h in (1, 3):
        a = cal.loc[("active", h)]
        assert a["active"] and a["reason"] == "ok" and a["gain"] > rc.MIN_GAIN
        assert a["b"] < 0.8 and a["brier_cal"] < a["brier_raw"]  # learned that the model is too sure
        o = cal.loc[("onset", h)]
        assert not o["active"] and o["reason"] == "no_gain" and o["a"] == 0 and o["b"] == 1
    for h in (2, 6):  # no rows at all
        assert cal.loc[("active", h), "reason"] == "collecting"
    # 10-minute runs are counted as region-hours, not as independent rows
    a1 = cal.loc[("active", 1)]
    assert a1["n_rows"] > 5 * a1["n_eff"] and a1["n_eff"] == pytest.approx(len(REGIONS) * a1["n_hours"], rel=0.01)
    # served: the logged probability is corrected, the model's own is kept
    log = V[V["origin"] == V["origin"].max()].drop(columns=["y"])
    out = rc.apply(log, cal.reset_index())
    act = out["target"] == "active"
    assert (out.loc[act, "calibration"] == "platt").all() and (out.loc[~act, "calibration"] == "none").all()
    assert (out["p_raw"] == log["p"]).all() and (out.loc[~act, "p"] == log.loc[~act, "p"]).all()
    hi = act & (out["p_raw"] > 0.9)
    assert (out.loc[hi, "p"] < out.loc[hi, "p_raw"]).all()  # pulled away from overconfident values
    assert out["calibrated_at"].notna().sum() == act.sum()


def test_point_in_time():
    """At T only rows verified by T whose outcome window had closed by T count: rows verified
    later (whatever their outcome) change nothing."""
    V = _verified(4, overconfident, seed=3, targets=("active",), horizons=(1, 6))
    T = T0 + pd.Timedelta(days=3, hours=5)
    base = rc.fit(V, T)
    assert base["active"].any()
    later = V["verified_at"] > T
    assert later.any()
    flipped = V.assign(y=np.where(later, 1 - V["y"], V["y"]))
    pd.testing.assert_frame_equal(rc.fit(flipped, T), base)
    pd.testing.assert_frame_equal(rc.fit(V[~later], T), base)
    # a (wrong) early verified_at never lets an unfinished window in
    early = V.assign(verified_at=V["issued_at"])
    unfinished = early["origin"] + pd.to_timedelta(early["horizon"], unit="h") > T
    flipped = early.assign(y=np.where(unfinished, 1 - early["y"], early["y"]))
    pd.testing.assert_frame_equal(rc.fit(flipped, T), rc.fit(early, T))


def test_store_truncated_at_the_origin_gives_the_same_calibrator(tmp_path):
    """The forecast at origin T uses the newest map fitted by T (store as_of=T); a store cut at
    T (every file and row known later removed) gives exactly the same one."""
    V = _verified(4.5, overconfident, seed=4, targets=("active",), horizons=(1,))
    root = tmp_path / "store"
    fits = [T0 + pd.Timedelta(days=3, hours=k) for k in (0, 6, 12, 18)]
    for t in fits:
        store.append(root, "verification", V[V["verified_at"] <= t].assign(verified_at=t))
        store.append(root, "calibration", rc.fit(store.load(root, "verification", as_of=t), t))
    T = fits[2] + pd.Timedelta(minutes=13)
    used = rc.latest(root, T)
    assert used is not None and (used["fitted_at"] == fits[2]).all() and used["active"].any()
    # refitting from the store as known at that fit gives the same map
    refit = rc.fit(store.load(root, "verification", as_of=fits[2]), fits[2])
    np.testing.assert_allclose(used.set_index(["target", "horizon"]).loc[refit.set_index(["target", "horizon"]).index, "b"],
                               refit["b"].to_numpy())
    cut = tmp_path / "cut"
    shutil.copytree(root, cut)
    for kind in ("verification", "calibration"):
        for f in (cut / kind).rglob("*.parquet"):
            df = pd.read_parquet(f)
            col = store.TIME_COL[kind]
            keep = pd.to_datetime(df[col], utc=True) <= T
            if not keep.any():
                f.unlink()
            elif not keep.all():
                df[keep].to_parquet(f, index=False)
    pd.testing.assert_frame_equal(rc.latest(cut, T), used)
    # before the first fit, or once the newest fit is too old: the raw probability
    assert rc.latest(root, fits[0] - pd.Timedelta(minutes=1)) is None
    assert rc.latest(root, fits[-1] + rc.MAX_AGE + pd.Timedelta(minutes=1)) is None


def test_rows_from_before_the_layer_and_label_sources():
    """Store rows written before this layer have no p_raw / calibration (the raw probability was
    served: p is p_raw); verify.py rows may lack verified_at; dataset labels win over store labels."""
    V = _verified(3.5, overconfident, seed=5, targets=("active",), horizons=(1,))
    old = V.drop(columns=["verified_at", "issued_at", "label_source"])
    as_of = V["verified_at"].max()
    a, b = rc.fit(old, as_of), rc.fit(V, as_of)
    pd.testing.assert_frame_equal(a, b)
    rows = rc.training_rows(V.assign(p_raw=np.nan), as_of)
    assert (rows["p_raw"] == rows["p"]).all()
    # the same forecast verified twice: store label first, dataset label later -> the dataset one
    one = V.iloc[[0]]
    two = pd.concat([one.assign(y=1.0, label_source="store"),
                     one.assign(y=0.0, label_source="dataset", verified_at=one["verified_at"] + pd.Timedelta(days=1))])
    got = rc.training_rows(two, as_of)
    assert len(got) == 1 and got["y"].iloc[0] == 0.0 and got["label_source"].iloc[0] == "dataset"
    # empty or label-less input
    assert not rc.fit(pd.DataFrame(), as_of)["active"].any()
    assert rc.describe(None)["n_active"] == 0 and rc.describe(rc.fit(V, as_of))["n_total"] == 8
