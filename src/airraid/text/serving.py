"""Serve the history + Telegram model next to the history-only one, with a fallback.

Used by predict_now.py and the web engine with a few lines each:

    text = TextServing(root="text-data", model_dir=Path("models/text"))
    text.ensure_models(grid, H, S, hist_end, retrain_hours=24)    # trains or loads
    rows, status = text.rows_now(grid, k_now)                      # fetch + features, or (None, reason)
    p, served = text.predict(x_now, target, h, rows, fallback=history_model)

The text model is used only when (a) it beat the history-only model on the
validation month for that target and horizon (reports/text_selection.json), (b) its
boosters exist and were trained with the current feature set, and (c) the archive
covers the origin (fresh fetch worked, no gap). Otherwise the history-only model
serves, and `served` says which one did.

`allow_train=False` (env AIRRAID_TEXT_TRAIN=0, the 10-minute pipeline): never train
here; serve the boosters a separate job saved (`python -m airraid.text.serving train`),
whatever their age (they never saw data after hist_end), else the history-only model.
`status` says what happened.
Training mirrors run.py's lightgbm_text: origins from text coverage on, labels
fully observed before the history end, trees from reports/text_selection.json.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from airraid.features import HORIZONS, add_horizon_features, build_features, labels, origin_hours
from airraid.models import GBM, feature_columns

from . import features as tf
from . import live

log = logging.getLogger("airraid.text")
TARGETS = ("active", "onset")
FORMAT = 1


class TextServing:
    def __init__(self, root: Path | str, model_dir: Path | str, reports_dir: Path | str | None = None,
                 threads: int = 2, fetch: bool = True, min_fetch_s: float = 300, allow_train: bool | None = None):
        self.root, self.model_dir = Path(root), Path(model_dir)
        if allow_train is None:
            allow_train = os.environ.get("AIRRAID_TEXT_TRAIN", "1").strip().lower() not in ("0", "false", "no", "off")
        self.allow_train = allow_train
        self.status = "not loaded"
        self.reports_dir = Path(reports_dir) if reports_dir else Path(__file__).resolve().parents[3] / "reports"
        self.threads, self.fetch, self.min_fetch_s = threads, fetch, min_fetch_s
        self._fetched_at = -np.inf
        self.models: dict[tuple[str, int], GBM] = {}
        self.meta: dict = {}

    # ---------------------------------------------------------------- models
    def _selection(self) -> dict:
        p = self.reports_dir / "text_selection.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _trees(self) -> dict:
        sel = self._selection()
        return {(t, h): int(sel.get(f"lightgbm_text|{t}_h{h}", {}).get("trees") or 300) for t in TARGETS for h in HORIZONS}

    def enabled(self) -> set[tuple[str, int]]:
        """Targets/horizons where the text model beat the history-only one on the validation month.

        The rule is fixed before the test weeks are looked at (run.py's selection step);
        elsewhere the history-only model serves. Without a selection file, all are enabled.
        """
        sel, out = self._selection(), set()
        for t in TARGETS:
            for h in HORIZONS:
                txt, cov = sel.get(f"lightgbm_text|{t}_h{h}"), sel.get(f"lightgbm_cov|{t}_h{h}")
                if txt is None or cov is None or txt["val_brier"] < cov["val_brier"]:
                    out.add((t, h))
        return out

    def load(self, hist_end: pd.Timestamp, max_age_h: float | None) -> bool:
        """Saved boosters trained on data up to <= hist_end (and, with max_age_h, not older than that)."""
        try:
            meta = json.loads((self.model_dir / "meta.json").read_text())
        except (OSError, ValueError):
            return False
        trained = pd.Timestamp(meta["trained_until_utc"])
        too_old = max_age_h is not None and hist_end > trained + pd.Timedelta(hours=max_age_h)
        if meta.get("format") != FORMAT or trained > hist_end or too_old:
            return False
        models = {}
        for key, info in meta["models"].items():
            t, h = key.rsplit("_h", 1)
            g = GBM(n_estimators=info["trees"])
            g.regions = info["regions"]
            g.model = lgb.Booster(model_file=str(self.model_dir / f"{key}.txt"))
            models[(t, int(h))] = g
        self.models, self.meta = models, meta
        return True

    def train(self, grid, H, S, hist_end: pd.Timestamp) -> None:
        posts = tf.load_posts(self.root)
        cov = posts.attrs["coverage"]
        if any(c[0] is None for c in cov.values()):
            raise RuntimeError(f"text archive has no coverage for some channels: {sorted(cov)}")
        cov_start = (max(c[0] for c in cov.values()) + tf.WARMUP).ceil("h")
        last_hour = grid.minute(hist_end.floor("h")) // 60
        K = origin_hours(grid, cov_start, hist_end.floor("h"))
        base = tf.attach(build_features(grid, K, H), tf.build_text_features(grid.t0, K, grid.regions, posts))
        trees = self._trees()
        models, meta = {}, {"format": FORMAT, "trained_until_utc": str(hist_end), "coverage_start_utc": str(cov_start),
                            "channels": sorted(cov), "models": {}}
        for h in HORIZONS:
            df = add_horizon_features(base, grid, H, h, S)
            k = df["origin_hour"].to_numpy()
            for t in TARGETS:
                y = labels(grid, K, h, H, t, S)
                m = ~np.isnan(y) & (k + h <= last_hour) & (df["tg_covered"].to_numpy() == 1)
                g = GBM(n_estimators=trees[(t, h)], params={"num_threads": self.threads}).fit(df[m], y[m])
                models[(t, h)] = g
                meta["models"][f"{t}_h{h}"] = {"regions": g.regions, "trees": g.n_estimators}
        self.model_dir.mkdir(parents=True, exist_ok=True)
        for (t, h), g in models.items():
            g.model.save_model(str(self.model_dir / f"{t}_h{h}.txt"), num_iteration=g.n_estimators)
        (self.model_dir / "meta.json").write_text(json.dumps(meta, indent=1))
        self.models, self.meta = models, meta

    def ensure_models(self, grid, H, S, hist_end: pd.Timestamp, retrain_hours: float = 24) -> bool:
        """Load fresh boosters or train new ones; False (and the history model serves) if that fails.

        Without allow_train only loads, accepting boosters of any age."""
        if self.load(hist_end, retrain_hours if self.allow_train else None):
            self.status = f"loaded (trained until {self.meta.get('trained_until_utc')})"
            return True
        if not self.allow_train:
            self.models = {}
            self.status = f"no saved text model in {self.model_dir} and training is off (AIRRAID_TEXT_TRAIN=0)"
            log.info(self.status)
            return False
        try:
            self.train(grid, H, S, hist_end)
            self.status = "trained"
            return True
        except Exception as e:  # never let the text model break the forecast
            log.warning("text model not available: %r", e)
            self.models = {}
            self.status = f"training failed: {type(e).__name__}"
            return False

    # -------------------------------------------------------------- forecast
    def rows_now(self, grid, k_now: int):
        if not self.models:
            return None, f"no text model ({self.status})"
        fetch = self.fetch and time.monotonic() - self._fetched_at >= self.min_fetch_s  # be polite to t.me
        if fetch:
            self._fetched_at = time.monotonic()
        try:
            return live.text_rows_at(grid.t0, np.array([k_now]), grid.regions, self.root, fetch_new=fetch,
                                     channels=self.meta.get("channels"), log=log.info)
        except Exception as e:
            return None, f"text features failed: {e!r}"

    def predict(self, x: pd.DataFrame, t: str, h: int, rows: pd.DataFrame | None, fallback: GBM):
        """P(event) from the text model when it can serve, else from `fallback`; plus the model's name."""
        g = self.models.get((t, h)) if (t, h) in self.enabled() else None
        if g is not None and rows is not None:
            xt = tf.attach(x, rows)
            names = [c for c in g.model.feature_name() if c != "region"]
            if sorted(names) == sorted(feature_columns(xt)):
                # LightGBM takes columns by position: use the order the booster was trained with
                return g.predict(xt[["origin_hour", "region", *names]]), "lightgbm_text"
            log.warning("text model feature set changed since training; serving the history model")
        return fallback.predict(x), "lightgbm"


def main(argv=None) -> int:
    """Train the text models outside the forecast run (e.g. in a daily training job):

        PYTHONPATH=src python -m airraid.text.serving train --root text-data --model-dir models/text
    """
    import argparse

    from airraid.data import build_grid, load_alerts
    from airraid.features import hourly_fraction, hourly_starts

    ap = argparse.ArgumentParser(prog="python -m airraid.text.serving")
    ap.add_argument("cmd", choices=["train"])
    ap.add_argument("--root", required=True, help="text archive (text-data checkout)")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--reports-dir", default=None, help="where text_selection.json is (default: reports/)")
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args(argv)
    alerts = load_alerts()
    hist_end = alerts["start"].max()
    grid = build_grid(alerts, data_end=hist_end.floor("h"))
    text = TextServing(a.root, a.model_dir, a.reports_dir, threads=a.threads, fetch=False)
    text.train(grid, hourly_fraction(grid), hourly_starts(grid), hist_end)
    print(f"trained {len(text.models)} text models on data up to {hist_end} -> {a.model_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
