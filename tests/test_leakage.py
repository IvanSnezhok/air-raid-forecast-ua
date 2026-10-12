"""Mechanical no-leakage check.

For several forecast origins T we rebuild the whole pipeline from data that
ends exactly at T (as if we were running live at T) and compare the features
with the ones computed from the full dataset. Any difference means a feature
used information from after T.
"""
import numpy as np
import pandas as pd
import pytest

from airraid.data import build_grid, load_alerts
from airraid.features import HORIZONS, add_horizon_features, build_features, hourly_fraction


@pytest.fixture(scope="module")
def alerts():
    return load_alerts()


@pytest.fixture(scope="module")
def full(alerts):
    g = build_grid(alerts)
    return g, hourly_fraction(g)


def _origins(full_grid, n=6, seed=0):
    rng = np.random.default_rng(seed)
    last = full_grid.n_minutes // 60 - 24
    return sorted(rng.integers(last - 24 * 400, last, size=n))


@pytest.mark.parametrize("seed", [0, 1])
def test_features_do_not_use_future(alerts, full, seed):
    g, H = full
    for k in _origins(g, seed=seed):
        T = g.t0 + pd.Timedelta(hours=int(k))
        gt = build_grid(alerts, data_end=T)
        assert gt.t0 == g.t0 and gt.regions == g.regions
        Ht = hourly_fraction(gt)
        K = np.array([k])
        a = build_features(g, K, H)
        b = build_features(gt, K, Ht)
        pd.testing.assert_frame_equal(a, b, check_dtype=False)
        for h in HORIZONS:
            pd.testing.assert_frame_equal(
                add_horizon_features(a, g, H, h), add_horizon_features(b, gt, Ht, h), check_dtype=False
            )


def test_labels_are_strictly_after_origin(full):
    from airraid.features import labels

    g, H = full
    k = g.n_minutes // 60 - 30
    K = np.array([k])
    # Wipe everything up to T: labels must not change.
    g2_active = g.active.copy()
    g2_active[:, : k * 60] = False
    H2 = g2_active[:, : (g.n_minutes // 60) * 60].reshape(len(g.regions), -1, 60).mean(axis=2)
    from airraid.features import hourly_starts

    S = hourly_starts(g)
    S2 = S.copy()
    S2[:, :k] = 0
    for h in HORIZONS:
        np.testing.assert_array_equal(labels(g, K, h, H), labels(g, K, h, H2))
        np.testing.assert_array_equal(labels(g, K, h, H, "onset", S), labels(g, K, h, H2, "onset", S2))


def test_label_is_not_a_feature():
    """Regression test: the first full run trained on the label column itself
    (val Brier 0.0000, AUC 1.0) because `y` lived in the same frame as the features."""
    from airraid.models import feature_columns

    df = pd.DataFrame(columns=["origin_hour", "region", "horizon", "y", "y_active", "y_onset", "active_now", "lag_1h"])
    assert feature_columns(df) == ["active_now", "lag_1h"]


# ------------------------------------------------------------ sub-hour origins
# The pipeline forecasts from any minute (origin = first minute after the newest
# poll). build_grid aligns the hour blocks to end at the origin; the same
# truncation test must hold there, and the blocks must really be "the 60 minutes
# ending at the origin".

def _compare(alerts, g, H, k, builder=build_features):
    """Features at origin hour k of grid g vs the same features from data truncated at that origin."""
    T = g.t0 + pd.Timedelta(hours=int(k))
    gt = build_grid(alerts, data_end=T)
    assert gt.t0 == g.t0 and gt.regions == g.regions and gt.n_minutes == 60 * int(k)
    Ht = hourly_fraction(gt)
    K = np.array([k])
    a, b = builder(g, K, H), builder(gt, K, Ht)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    for h in HORIZONS:
        pd.testing.assert_frame_equal(
            add_horizon_features(a, g, H, h), add_horizon_features(b, gt, Ht, h), check_dtype=False
        )
    return T


@pytest.fixture(scope="module", params=[10, 23, 40, 50])
def shifted(request, alerts):
    minute = request.param
    end = alerts["start"].max().floor("h") - pd.Timedelta(hours=1) + pd.Timedelta(minutes=minute)
    g = build_grid(alerts, data_end=end)
    return minute, g, hourly_fraction(g)


def test_subhour_features_do_not_use_future(alerts, shifted):
    minute, g, H = shifted
    assert (g.t0 + pd.Timedelta(minutes=g.n_minutes)).minute == minute
    for k in _origins(g, n=4, seed=minute):
        T = _compare(alerts, g, H, k)
        assert T.minute == minute  # the origin really is at :MM


def test_subhour_blocks_end_at_the_origin(alerts, shifted):
    """lag_1h is the share of [T-60 min, T) under alert and the labels look at [T, T+h),
    computed independently on a plain (midnight-aligned) minute grid."""
    from airraid.features import hourly_starts, labels

    minute, g, H = shifted
    plain = build_grid(alerts, data_end=alerts["start"].max().floor("h"))
    S = hourly_starts(g)
    for k in _origins(g, n=4, seed=100 + minute):
        T = g.t0 + pd.Timedelta(hours=int(k))
        assert T.minute == minute
        m = plain.minute(T)
        x = build_features(g, np.array([k]), H)
        np.testing.assert_allclose(x["lag_1h"].to_numpy(), plain.active[:, m - 60 : m].mean(axis=1), atol=1e-6)
        np.testing.assert_array_equal(x["active_now"].to_numpy(), plain.active[:, m - 1])
        for h in HORIZONS:
            y = labels(g, np.array([k]), h, H, "active", S)
            np.testing.assert_array_equal(y, plain.active[:, m + 60 * (h - 1) : m + 60 * h].any(axis=1))
            y = labels(g, np.array([k]), h, H, "onset", S)
            new = [((s >= m) & (s < m + 60 * h)).any() for s in plain.starts]
            np.testing.assert_array_equal(y, np.array(new, float))


def test_truncation_check_catches_a_leak(alerts, shifted):
    """Mutation check: a feature that peeks at the end of the alert running at T
    (known only after T) must make the comparison fail."""
    minute, g, H = shifted

    def leaky(grid, K, Hh):
        df = build_features(grid, K, Hh)
        T = K * 60
        total = np.zeros((len(grid.regions), len(K)), np.float32)
        for i, (s, e) in enumerate(zip(grid.starts, grid.ends)):
            cur = np.searchsorted(s, T, "left") - 1
            ok = (cur >= 0) & (e[np.maximum(cur, 0)] > T)
            total[i, ok] = e[cur[ok]] - s[cur[ok]]  # full length of the running alert: future
        df["alert_length_min"] = total.reshape(-1)
        return df

    # origins where some oblast is under alert, so the leaky feature has something to leak
    ks = [k for k in range(g.n_minutes // 60 - 400, g.n_minutes // 60 - 24)
          if g.active[:, k * 60 - 1].any()][:3]
    assert ks
    for k in ks:
        _compare(alerts, g, H, k)  # the real features pass
    with pytest.raises(AssertionError):
        for k in ks:
            _compare(alerts, g, H, k, builder=leaky)
