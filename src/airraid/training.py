"""The served model set: one LightGBM per (target, horizon), trained and stored once.

Used by every consumer so they all serve the same boosters:
  * the train job (`scripts/train_models.py`, .github/workflows/train.yml);
  * the 10-minute pipeline (`predict_now.py --models DIR`), which only loads them
    and trains once only when no compatible set exists;
  * the static site and the web app (`airraid_app.engine`).

A bundle is a directory with `{target}_h{h}.txt` boosters, `meta.json` and,
when the holdout evaluation ran, `holdout.parquet`:
  1. holdout: train on the `months` before the last `holdout_days` (minus an
     embargo of the longest horizon), score that last week like live, hour by
     hour (the numbers on the "model quality" page);
  2. final: refit on the latest `months` of fully observed labels.
Only the volunteer dataset is used, origins at the top of every hour whose label
window it fully covers. Live records never enter training.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import lightgbm as lgb
import numpy as np
import pandas as pd

from .data import build_grid
from .evaluate import scores
from .features import HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels, origin_hours
from .models import GBM

log = logging.getLogger("airraid")

TARGETS = ("active", "onset")
# Reference forecasts used for skill scores, same as run.py.
CLIM_COL = {"active": "hod_clim_28d", "onset": "onset_clim_28d"}
PERS_COL = {"active": "active_now", "onset": "onset_last_h"}
EMBARGO_H = max(HORIZONS)
MODEL_FORMAT = 2  # 2: grid blocks aligned to the origin (sub-hour origins), midpoint calendar features
# Code whose change makes saved boosters stale (feature definitions, model class, data cleaning).
CODE_FILES = ("data.py", "features.py", "models.py", "training.py")


@dataclass
class Plan:
    months: int
    trees: int


def code_version() -> str:
    """Short hash of the modelling code; part of the bundle's identity."""
    h = hashlib.sha1()
    for name in CODE_FILES:
        h.update((Path(__file__).resolve().parent / name).read_bytes())
    return h.hexdigest()[:12]


def plans(reports_dir: Path, train_months: int = 12, max_train_months: int = 24, n_trees: int = 300,
          ) -> tuple[dict[tuple[str, int], Plan], str]:
    """Training window and tree count per model, and where they came from.

    Uses the selection made on the validation month by run.py when
    reports/run_summary.json exists ("all history" is capped at
    `max_train_months` to keep memory in check), else the defaults.
    """
    selection, source = {}, "defaults"
    path = Path(reports_dir) / "run_summary.json"
    if path.exists():
        try:
            selection = json.loads(path.read_text()).get("selection", {})
            source = "reports/run_summary.json"
        except (OSError, ValueError):
            log.warning("could not read %s", path)
    out = {}
    for t in TARGETS:
        for h in HORIZONS:
            sel = selection.get(f"{t}_h{h}")
            if sel:
                months = sel.get("best_months") or max_train_months
                out[(t, h)] = Plan(min(int(months), max_train_months), int(sel.get("best_trees") or n_trees))
            else:
                out[(t, h)] = Plan(train_months, n_trees)
    return out, source


def plans_json(p: dict[tuple[str, int], Plan]) -> dict:
    return {f"{t}_h{h}": vars(v) for (t, h), v in p.items()}


def _iso(ts: pd.Timestamp | None) -> str | None:
    return None if ts is None or pd.isna(ts) else ts.tz_convert("UTC").isoformat()


def train(alerts: pd.DataFrame, plan: dict[tuple[str, int], Plan], holdout_days: int = 7, threads: int = 4,
          holdout: bool = True, selection_source: str = "defaults",
          progress: Callable[[int, int], None] | None = None) -> tuple[dict, dict, pd.DataFrame]:
    """(models {(target, h): GBM}, meta, holdout predictions) trained on the dataset `alerts`."""
    t_start = time.time()
    hist_end = alerts["start"].max()
    data_end = hist_end.floor("h")
    grid = build_grid(alerts, data_end=data_end)
    H, S = hourly_fraction(grid), hourly_starts(grid)
    ho_start = data_end - pd.Timedelta(days=holdout_days)
    first = (ho_start if holdout else data_end) - pd.DateOffset(months=max(p.months for p in plan.values()))
    K = origin_hours(grid, first, data_end)
    base = build_features(grid, K, H)
    hour = lambda ts: grid.minute(ts) // 60  # noqa: E731
    params = {"num_threads": threads}

    models, rows, preds = {}, [], []
    n_done = 0
    for h in HORIZONS:
        df = add_horizon_features(base, grid, H, h, S)
        origin = df["origin_hour"].to_numpy()
        for t in TARGETS:
            p = plan[(t, h)]
            y = labels(grid, K, h, H, t, S)
            known = ~np.isnan(y)
            if holdout:
                # train on [ho_start - months, ho_start - embargo), test on [ho_start, end)
                lo = hour(ho_start - pd.DateOffset(months=p.months))
                tr = known & (origin >= lo) & (origin < hour(ho_start) - EMBARGO_H)
                te = known & (origin >= hour(ho_start))
                if tr.sum() and te.sum():
                    g = GBM(n_estimators=p.trees, params=params).fit(df[tr], y[tr])
                    pr = g.predict(df[te])
                    yt, clim, pers = y[te], df.loc[te, CLIM_COL[t]].to_numpy(), df.loc[te, PERS_COL[t]].to_numpy()
                    for name, q in (("lightgbm", pr), ("climatology_28d", clim), ("persistence", pers)):
                        rows.append({"target": t, "horizon": h, "model": name, **scores(yt, q, clim)})
                    preds.append(pd.DataFrame({
                        "origin_hour": origin[te], "region": df.loc[te, "region"].to_numpy(), "target": t,
                        "horizon": h, "y": yt, "p": pr, "clim": clim,
                    }))
            # final model on the latest `months` of fully observed labels
            m = known & (origin >= hour(data_end - pd.DateOffset(months=p.months)))
            models[(t, h)] = GBM(n_estimators=p.trees, params=params).fit(df[m], y[m])
            n_done += 1
            if progress:
                progress(n_done, len(plan))
        del df

    ho = pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()
    if not ho.empty:
        ho["origin"] = grid.t0 + pd.to_timedelta(ho.pop("origin_hour"), unit="h")
    meta = {
        "format": MODEL_FORMAT,
        "code_version": code_version(),
        "trained_at": _iso(pd.Timestamp.now(tz="UTC")),
        "data_end": _iso(data_end),  # last moment of data used for training
        "hist_end": _iso(hist_end),
        "holdout_start": _iso(ho_start) if holdout else None,
        "holdout_days": holdout_days if holdout else None,
        "regions": grid.regions,
        "plans": plans_json(plan),
        "selection_source": selection_source,
        "train_seconds": round(time.time() - t_start, 1),
        "holdout_scores": rows,
    }
    return models, meta, ho


def save(model_dir: Path, models: dict, meta: dict, holdout: pd.DataFrame | None = None) -> None:
    d = Path(model_dir)
    d.mkdir(parents=True, exist_ok=True)
    for (t, h), g in models.items():
        g.model.save_model(str(d / f"{t}_h{h}.txt"), num_iteration=g.n_estimators)
    ho = d / "holdout.parquet"
    if holdout is not None and not holdout.empty:
        holdout.to_parquet(ho)
    elif ho.exists():
        ho.unlink()  # never pair new boosters with an old holdout
    (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))


def read_meta(model_dir: Path) -> dict | None:
    try:
        return json.loads((Path(model_dir) / "meta.json").read_text())
    except (OSError, ValueError):
        return None


def incompatible(meta: dict | None, regions: list[str], plan: dict | None = None) -> str | None:
    """Why a saved bundle cannot be served with this code and data (None if it can)."""
    if meta is None:
        return "no saved models"
    if meta.get("format") != MODEL_FORMAT:
        return f"model format {meta.get('format')} != {MODEL_FORMAT}"
    if meta.get("code_version") != code_version():
        return "modelling code changed since training"
    if meta.get("regions") != regions:
        return "region list changed"
    if plan is not None and meta.get("plans") != plans_json(plan):
        return "training plan changed (new run.py selection)"
    return None


def load(model_dir: Path, regions: list[str], plan: dict | None = None) -> tuple[dict, dict, pd.DataFrame] | None:
    """(models, meta, holdout) from `model_dir`, or None if missing or incompatible."""
    d = Path(model_dir)
    meta = read_meta(d)
    if (why := incompatible(meta, regions, plan)) is not None:
        log.info("saved models not used: %s", why)
        return None
    models = {}
    for t in TARGETS:
        for h in HORIZONS:
            path = d / f"{t}_h{h}.txt"
            if not path.exists():
                return None
            g = GBM(n_estimators=None)
            g.model = lgb.Booster(model_file=str(path))
            g.regions = meta["regions"]
            models[(t, h)] = g
    ho = pd.read_parquet(d / "holdout.parquet") if (d / "holdout.parquet").exists() else pd.DataFrame()
    return models, meta, ho
