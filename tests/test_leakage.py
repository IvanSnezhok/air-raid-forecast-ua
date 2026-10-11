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
