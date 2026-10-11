"""Mechanical no-leakage check for the raion forecast (same idea as tests/test_leakage.py).

For random origins T in the raion era, every raion feature and the trailing
raion/oblast share are rebuilt from data that ends exactly at T (alerts that
started later dropped, running ones cut at T) and must equal the values
computed from the full data. The mutation tests show the check bites: a lag-0
feature and a share that peeks at the current label window both fail it.
Downloads the official and volunteer files on first run.
"""
import numpy as np
import pandas as pd
import pytest

from airraid.data import load_alerts
from airraid.features import HORIZONS, add_horizon_features, labels
from airraid.raion_data import alerts_as_of, load_official
from airraid.raion_features import build_raion_features, make_inputs, oblast_labels, trailing_share

SINCE = pd.Timestamp("2026-03-01", tz="UTC")
END = pd.Timestamp("2026-09-07", tz="UTC")


@pytest.fixture(scope="module")
def data():
    ra = load_official(since=SINCE)
    ra = ra[ra["start"] >= SINCE]
    vol = load_alerts()
    vol = vol[vol["start"] >= SINCE - pd.Timedelta(days=40)]
    full = make_inputs(alerts_as_of(ra, END), alerts_as_of(vol, END), END)
    return ra, vol, full


def _origins(full, n=4, seed=0):
    rng = np.random.default_rng(seed)
    last = full.grid.n_minutes // 60 - 24
    return sorted(int(k) for k in rng.integers(last - 24 * 120, last, size=n))


def _truncated(ra, vol, T):
    return make_inputs(alerts_as_of(ra, T), alerts_as_of(vol, T), T)


@pytest.mark.parametrize("seed", [0, 1])
def test_raion_features_do_not_use_future(data, seed):
    ra, vol, full = data
    for k in _origins(full, seed=seed):
        T = full.grid.t0 + pd.Timedelta(hours=k)
        tr = _truncated(ra, vol, T)
        assert tr.grid.t0 == full.grid.t0 and tr.vgrid.t0 == full.vgrid.t0
        assert tr.grid.regions == full.grid.regions
        K = np.array([k])
        a, b = build_raion_features(full, K), build_raion_features(tr, K)
        pd.testing.assert_frame_equal(a, b, check_dtype=False)
        for h in HORIZONS:
            pd.testing.assert_frame_equal(add_horizon_features(a, full.grid, full.H, h, full.S),
                                          add_horizon_features(b, tr.grid, tr.H, h, tr.S), check_dtype=False)


def _share_at(inp, k, h, target, peek=0):
    K = np.arange(k - 28 * 24 - 2 * max(HORIZONS), k + 1)
    R = len(inp.grid.regions)
    y = labels(inp.grid, K, h, inp.H, target, inp.S).reshape(R, len(K))
    yo = oblast_labels(inp, K, h, target)
    return trailing_share(y, yo, h - peek)[:, -1]


@pytest.mark.parametrize("target", ["active", "onset"])
def test_trailing_share_does_not_use_future(data, target):
    ra, vol, full = data
    for k in _origins(full, n=3, seed=2):
        T = full.grid.t0 + pd.Timedelta(hours=k)
        tr = _truncated(ra, vol, T)
        for h in HORIZONS:
            np.testing.assert_allclose(_share_at(full, k, h, target), _share_at(tr, k, h, target))


def _leaky_features(inp, K):
    """Mutation: add the hour that starts at T (lag 0) as a feature."""
    df = build_raion_features(inp, K)
    H = np.pad(inp.H, ((0, 0), (0, 1)))  # data truncated at T has no hour T: pad with zeros
    df["lag_0h"] = H[:, K].reshape(-1)
    return df


def test_mutations_are_caught(data):
    """Injected leaks must make the comparisons above fail, otherwise the test would not bite."""
    ra, vol, full = data
    ks = [k for k in _origins(full, n=40, seed=3) if full.H[:, k].sum() > 0]
    assert ks, "need an origin followed by an alert"
    k = ks[0]
    tr = _truncated(ra, vol, full.grid.t0 + pd.Timedelta(hours=k))
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(_leaky_features(full, np.array([k])), _leaky_features(tr, np.array([k])),
                                      check_dtype=False)
    # share over label windows that are not finished at T (window h - 2 instead of h)
    caught = False
    for kk in _origins(full, n=6, seed=4):
        trk = _truncated(ra, vol, full.grid.t0 + pd.Timedelta(hours=kk))
        if not np.allclose(_share_at(full, kk, 3, "onset", peek=2), _share_at(trk, kk, 3, "onset", peek=2)):
            caught = True
            break
    assert caught, "a share that peeks past T should differ between full and truncated data"
