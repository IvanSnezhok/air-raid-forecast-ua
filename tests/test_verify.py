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
