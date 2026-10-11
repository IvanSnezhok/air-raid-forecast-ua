"""End-to-end pipeline: data -> features -> model selection -> walk-forward test -> report.

    python run.py              # uses data/raw/volunteer_data_en.csv (downloads if missing)
    python run.py --download   # refresh the data first
"""
from __future__ import annotations

import argparse
import sys
import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid.data import build_grid, download, load_alerts
from airraid.evaluate import reliability, scores
from airraid.features import (
    HORIZONS, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels, origin_hours,
)
from airraid.models import GBM, Climatology, LogReg, MarkovPersistence, Persistence

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
FIG = REPORTS / "figures"

TEST_DAYS = 28
VAL_DAYS = 28
FOLD_DAYS = 7
EMBARGO_H = max(HORIZONS)  # training labels must end before the first evaluated origin
WINDOWS_MONTHS = [3, 6, 12, 24, None]  # None = all history
SPATIAL_PREFIXES = ("now__", "lag1__", "nat_")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


TARGETS = {
    # target: (climatology column, persistence column)
    "active": ("hod_clim_28d", "active_now"),
    "onset": ("onset_clim_28d", "onset_last_h"),
}
MODELS = ["climatology_28d", "persistence", "markov_persistence", "logistic_regression", "lightgbm_no_spatial", "lightgbm"]


def build_dataset(grid, H, S, start, end):
    K = origin_hours(grid, start, end)
    base = build_features(grid, K, H)
    out = {}
    for h in HORIZONS:
        df = add_horizon_features(base, grid, H, h, S)
        for t in TARGETS:
            df[f"y_{t}"] = labels(grid, K, h, H, t, S)
        out[h] = df
    return out


def hour_of(grid, ts):
    return grid.minute(ts) // 60


def window(df, grid, end_ts, months):
    """Rows whose origin lies in [end_ts - months, end_ts - embargo)."""
    hi = hour_of(grid, end_ts) - EMBARGO_H
    lo = 0 if months is None else hour_of(grid, end_ts - pd.DateOffset(months=months))
    m = (df["origin_hour"] >= lo) & (df["origin_hour"] < hi)
    return df[m]


def between(df, grid, a, b):
    m = (df["origin_hour"] >= hour_of(grid, a)) & (df["origin_hour"] < hour_of(grid, b))
    return df[m]


def drop_spatial(df):
    return df[[c for c in df.columns if not c.startswith(SPATIAL_PREFIXES)]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--targets", default=",".join(TARGETS))
    ap.add_argument("--reuse-selection", action="store_true",
                    help="skip step 1 and reuse reports/selection.json from a previous run")
    args = ap.parse_args()
    if args.download:
        download()
    targets = args.targets.split(",")
    FIG.mkdir(parents=True, exist_ok=True)

    alerts = load_alerts()
    grid = build_grid(alerts)
    H, S = hourly_fraction(grid), hourly_starts(grid)
    data_end = grid.data_end
    # Last origin whose 6-hour-ahead label is fully observed.
    test_end = data_end - pd.Timedelta(hours=max(HORIZONS) - 1)
    test_start = test_end - pd.Timedelta(days=TEST_DAYS)
    val_start = test_start - pd.Timedelta(days=VAL_DAYS)
    split = {
        "data_end_utc": str(data_end),
        "val": [str(val_start), str(test_start)],
        "test": [str(test_start), str(test_end)],
        "n_alert_records": int(len(alerts)),
        "regions": grid.regions,
    }
    log(f"data to {data_end}; val {val_start} .. {test_start}; test {test_start} .. {test_end}")

    data = build_dataset(grid, H, S, grid.t0, test_end)
    log(f"features: {len(data[1]):,} rows x {data[1].shape[1]} cols per horizon")

    # ---------- 1. model selection on the validation month ----------
    # Early stopping and the window choice both look at the validation month, so
    # validation scores are optimistic. The test month is never looked at here.
    sel_path = REPORTS / "selection.json"
    selection = json.loads(sel_path.read_text()) if args.reuse_selection and sel_path.exists() else {}
    for t in targets:
        for h in HORIZONS:
            if f"{t}_h{h}" in selection:
                continue
            df = data[h]
            val = between(df, grid, val_start, test_start)
            yv = val[f"y_{t}"].to_numpy()
            cands, best = [], None
            for months in WINDOWS_MONTHS:
                tr = window(df, grid, val_start, months)
                m = GBM().fit(tr, tr[f"y_{t}"].to_numpy(), valid=(val, yv))
                s = scores(yv, m.predict(val), val[TARGETS[t][0]].to_numpy())
                if s["roc_auc"] > 0.995:
                    raise RuntimeError(f"validation AUC {s['roc_auc']:.4f} is too good to be true: check for label leakage")
                log(f"{t} h={h} window={months} months: val brier={s['brier']:.4f} auc={s['roc_auc']:.4f} trees={m.n_estimators}")
                cands.append({"months": months, "trees": m.n_estimators, **s})
                if best is None or s["brier"] < best[0]:
                    best = (s["brier"], months, m.n_estimators)
            selection[f"{t}_h{h}"] = {"candidates": cands, "best_months": best[1], "best_trees": best[2]}
            sel_path.write_text(json.dumps(selection, indent=2))

    # ---------- 2. walk-forward evaluation on the latest 4 weeks ----------
    preds, importances = [], []
    folds = pd.date_range(test_start, test_end, freq=f"{FOLD_DAYS}D")
    for t in targets:
        clim_col, pers_col = TARGETS[t]
        for h in HORIZONS:
            df = data[h]
            sel = selection[f"{t}_h{h}"]
            for i, fs in enumerate(folds):
                fe = min(fs + pd.Timedelta(days=FOLD_DAYS), test_end + pd.Timedelta(hours=1))
                te = between(df, grid, fs, fe)
                if te.empty:
                    continue
                tr = window(df, grid, fs, sel["best_months"])
                ytr = tr[f"y_{t}"].to_numpy()
                out = te[["origin_hour", "region", "active_now"]].copy()
                out["y"] = te[f"y_{t}"].to_numpy()
                out["clim_ref"] = te[clim_col].to_numpy()
                out["target"], out["horizon"], out["fold"] = t, h, i
                for model in (Climatology(clim_col), Persistence(pers_col), MarkovPersistence()):
                    out[model.name] = model.fit(tr, ytr).predict(te)
                # logistic regression: last 12 months only, it is the slowest model on ~1M rows
                tr12 = window(df, grid, fs, 12)
                out["logistic_regression"] = LogReg().fit(tr12, tr12[f"y_{t}"].to_numpy()).predict(te)
                gbm = GBM(n_estimators=sel["best_trees"]).fit(tr, ytr)
                out["lightgbm"] = gbm.predict(te)
                importances.append(gbm.importance().rename(f"{t}_h{h}_f{i}"))
                ns = GBM(n_estimators=sel["best_trees"]).fit(drop_spatial(tr), ytr)
                out["lightgbm_no_spatial"] = ns.predict(drop_spatial(te))
                preds.append(out)
                log(f"{t} h={h} fold {i} ({fs:%m-%d}): trained on {len(tr):,} rows, tested on {len(te):,}")
    P = pd.concat(preds, ignore_index=True)
    P["origin_utc"] = grid.t0 + pd.to_timedelta(P["origin_hour"], unit="h")
    P.drop(columns=["origin_hour"]).to_parquet(REPORTS / "test_predictions.parquet")

    # ---------- 3. report ----------
    rows, per_region = [], []
    for (t, h), q in P.groupby(["target", "horizon"]):
        for mname in MODELS:
            rows.append({"target": t, "horizon_h": h, "model": mname,
                         **scores(q["y"].to_numpy(), q[mname].to_numpy(), q["clim_ref"].to_numpy())})
    for (t, h, r), q in P.groupby(["target", "horizon", "region"]):
        y, ref = q["y"].to_numpy(), q["clim_ref"].to_numpy()
        s_g, s_m = scores(y, q["lightgbm"].to_numpy(), ref), scores(y, q["markov_persistence"].to_numpy(), ref)
        per_region.append({"target": t, "horizon_h": h, "region": r, "base_rate": s_g["base_rate"],
                           "brier_lgbm": s_g["brier"], "brier_markov": s_m["brier"],
                           "bss_lgbm_vs_clim": s_g["bss_vs_clim"], "auc_lgbm": s_g["roc_auc"]})
    M, R = pd.DataFrame(rows), pd.DataFrame(per_region)
    imp = pd.concat(importances, axis=1).mean(axis=1).sort_values(ascending=False)

    M.to_csv(REPORTS / "metrics_test.csv", index=False)
    R.to_csv(REPORTS / "metrics_by_region.csv", index=False)
    imp.head(30).to_csv(REPORTS / "feature_importance_top30.csv", header=["gain"])
    (REPORTS / "run_summary.json").write_text(json.dumps({"split": split, "selection": selection}, indent=2, default=str))
    with open(REPORTS / "metrics_test.md", "w") as f:
        f.write(f"Test period (walk-forward, weekly retrain): {test_start:%Y-%m-%d %H:%M} .. {test_end:%Y-%m-%d %H:%M} UTC "
                f"(forecast origins), {len(grid.regions)} regions.\n")
        for t in targets:
            q = M[M["target"] == t]
            for metric in ("brier", "bss_vs_clim", "roc_auc", "log_loss"):
                tab = q.pivot(index="model", columns="horizon_h", values=metric).loc[MODELS].round(4)
                f.write(f"\n### target = {t}, {metric}\n\n" + tab.to_markdown() + "\n")
                log(f"{t} {metric}\n{tab.to_string()}")
        f.write("\nBase rates: " + ", ".join(
            f"{t} h={h}: {q['base_rate'].iloc[0]:.3f}" for (t, h), q in M.groupby(["target", "horizon_h"])) + "\n")

    _plots(P, M, R, imp, targets)
    log("done")


def _plots(P, M, R, imp, targets):
    # skill by horizon, one panel per target
    fig, axes = plt.subplots(1, len(targets), figsize=(6 * len(targets), 4), squeeze=False)
    for ax, t in zip(axes[0], targets):
        for mname in MODELS[1:]:
            q = M[(M["model"] == mname) & (M["target"] == t)]
            ax.plot(q["horizon_h"], q["bss_vs_clim"], marker="o", label=mname)
        ax.axhline(0, color="grey", lw=0.8)
        ax.set_ylim(max(ax.get_ylim()[0], -1), None)
        ax.set_xlabel("forecast horizon (hours)")
        ax.set_ylabel("Brier skill score vs 28-day climatology")
        ax.set_title(f"target: {t} (latest 4 weeks)")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "skill_by_horizon.png", dpi=130)
    plt.close(fig)

    # reliability
    fig, axes = plt.subplots(len(targets), 2, figsize=(9, 4 * len(targets)), squeeze=False)
    for row, t in zip(axes, targets):
        for ax, h in zip(row, (1, max(HORIZONS))):
            q = P[(P["horizon"] == h) & (P["target"] == t)]
            for mname in ("logistic_regression", "lightgbm", "climatology_28d"):
                rel = reliability(q["y"].to_numpy(), q[mname].to_numpy())
                ax.plot(rel[:, 0], rel[:, 1], marker="o", label=mname)
            ax.plot([0, 1], [0, 1], color="grey", lw=0.8, ls="--")
            ax.set_title(f"Reliability: {t}, horizon {h} h")
            ax.set_xlabel("forecast probability")
            ax.set_ylabel("observed frequency")
            ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "reliability.png", dpi=130)
    plt.close(fig)

    # last test week, two regions: P(alert active in the next hour / 3rd hour)
    last_fold = P["fold"].max()
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.5), sharex=True)
    Q = P[(P["target"] == "active") & (P["fold"] == last_fold)]
    for ax, reg in zip(axes, ("Kyiv City", "Kharkivska oblast")):
        for h, c in ((1, "tab:blue"), (3, "tab:orange")):
            q = Q[(Q["region"] == reg) & (Q["horizon"] == h)].sort_values("origin_utc")
            ax.plot(q["origin_utc"] + pd.to_timedelta(h - 1, unit="h"), q["lightgbm"], color=c, lw=1,
                    label=f"P(alert), forecast made {h} h ahead")
        q = Q[(Q["region"] == reg) & (Q["horizon"] == 1)].sort_values("origin_utc")
        ax.fill_between(q["origin_utc"], 0, q["y"], step="post", color="red", alpha=0.15, label="alert in that hour")
        ax.set_title(reg)
        ax.set_ylim(0, 1)
        ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(FIG / "last_week_example.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 6))
    top = imp.head(20)[::-1]
    ax.barh(top.index, top.values / imp.sum())
    ax.set_xlabel("share of total gain (LightGBM, mean over targets, horizons, folds)")
    fig.tight_layout()
    fig.savefig(FIG / "feature_importance.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(1, len(targets), figsize=(7 * len(targets), 6), squeeze=False)
    for ax, t in zip(axes[0], targets):
        h = 1 if t == "active" else 3
        q = R[(R["horizon_h"] == h) & (R["target"] == t)]
        # a region with (almost) no events in the test weeks makes the skill score meaningless
        q = q[q["base_rate"].between(0.02, 0.98)].sort_values("bss_lgbm_vs_clim")
        ax.barh(q["region"], q["bss_lgbm_vs_clim"])
        ax.axvline(0, color="grey", lw=0.8)
        ax.set_xlabel(f"Brier skill score vs climatology ({t}, {h} h)")
    fig.tight_layout()
    fig.savefig(FIG / "skill_by_region.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
