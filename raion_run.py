"""Raion-level forecast: leak-free walk-forward evaluation on the latest period with raion data.

    python raion_run.py                    # data/raw/{volunteer,official}_data_en.csv, downloads if missing
    python raion_run.py --threads 2 --reuse-oblast

Raion-level history comes from the official file, which stopped on 2026-09-07,
so the test period is the last 4 weeks before that (not the oblast model's test
period). Same protocol as run.py: validation month for model selection, then 4
weekly folds; before each fold every model is refit on data up to the fold
start, with a 6-hour embargo.

Methods compared for each target (active / onset) and horizon:
  climatology_28d   the raion's own same-hour share of the last 28 days (reference for skill)
  rate_28d          the raion's event rate over the last 28 days at any hour (a less noisy
                    baseline for single raions; reported, not served)
  persistence       0/1: raion under alert now / alert started in the last h hours
  oblast            the oblast model's probability for the raion's oblast, as is
  oblast_x_share    oblast probability x trailing 28-day ratio of raion events to
                    (volunteer) oblast events; the ratio absorbs the definitional
                    mismatch between the two files
  oblast_x_share_stale30
                    the same with the share estimated 30 days earlier: how the
                    share degrades when no recent raion history exists (live today)
  lightgbm_state    raion LightGBM that needs no raion history: which raions are
                    under alert now and since when (one live poll), oblast and
                    neighbour state, the volunteer oblast history, calendar
  lightgbm          raion LightGBM with all features, incl. 28 days of raion
                    history (lags, starts, same-hour climatology)

What can run live depends on the data available at forecast time, so the
validation month picks the best method separately for three tiers:
  history     raion history covers the last 28 days (live store): all methods
  live_state  only the current raion state (alerts.in.ua poll): lightgbm_state,
              oblast_x_share with a stale share, oblast
  none        no raion data at all: stale-share oblast_x_share or oblast

Outputs reports/raions/: metrics_test.csv/.md, by_oblast.csv, coverage.csv,
agreement.csv, selection.json, summary.json, feature_importance_top30.csv, figures/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid.data import load_alerts
from airraid.evaluate import reliability, scores
from airraid.features import HORIZONS, add_horizon_features, build_features, labels, origin_hours
from airraid.models import GBM
from airraid.raion_data import coverage, load_official, official_known_until, read_official
from airraid.raion_features import STATE_COLS, build_raion_features, make_inputs, oblast_labels, trailing_share
from airraid.raions import BY_ID
from airraid_app.regions import uk_name

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports" / "raions"
FIG = REPORTS / "figures"
CACHE = ROOT / "var" / "raion_eval"

TEST_DAYS = 28
VAL_DAYS = 28
FOLD_DAYS = 7
EMBARGO_H = max(HORIZONS)
WINDOWS_MONTHS = [3, 6, 9]
MAX_OBLAST_MONTHS = 24  # same cap as the web app (Settings.max_train_months)
GRID_SINCE = pd.Timestamp("2025-08-01", tz="UTC")
TARGETS = {"active": ("hod_clim_28d", "active_now"), "onset": ("onset_clim_28d", "onset_last_h")}
METHODS = ["climatology_28d", "rate_28d", "persistence", "oblast", "oblast_x_share", "oblast_x_share_stale30", "lightgbm_state", "lightgbm"]
STALE_H = 30 * 24
TIERS = {
    "history": ["lightgbm", "lightgbm_state", "oblast_x_share", "oblast"],
    "live_state": ["lightgbm_state", "oblast_x_share_stale30", "oblast"],
    "none": ["oblast_x_share_stale30", "oblast"],
}
KEEP = ["origin_hour", "region", "horizon"]


def trailing_rate(y: np.ndarray, h: int, window: int = 28 * 24) -> np.ndarray:
    """[R, n] labels of consecutive origins -> mean over the origins i-window .. i-h (label known at i)."""
    yz = np.nan_to_num(y.astype(np.float64))
    c = np.concatenate([np.zeros((yz.shape[0], 1)), np.cumsum(yz, axis=1)], axis=1)
    out = np.full(yz.shape, np.nan)
    i = np.arange(window, yz.shape[1])
    out[:, i] = (c[:, i - h + 1] - c[:, i - window]) / (window - h + 1)
    return out


def model_view(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Columns a raion model sees: everything, or only the current-state features."""
    return df if kind == "lightgbm" else df[KEEP + STATE_COLS]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def oblast_plans() -> dict:
    """Training window and trees of the served oblast model (reports/run_summary.json, capped like the app)."""
    sel = json.loads((ROOT / "reports" / "run_summary.json").read_text())["selection"]
    out = {}
    for k, v in sel.items():
        months = v.get("best_months") or MAX_OBLAST_MONTHS
        out[k] = (min(int(months), MAX_OBLAST_MONTHS), int(v["best_trees"]))
    return out


def folds(start, end):
    edges = list(pd.date_range(start, end, freq=f"{FOLD_DAYS}D"))
    if edges[-1] < end:
        edges.append(end)
    return list(zip(edges[:-1], edges[1:]))


def oblast_predictions(inp, val_start, test_start, test_end, threads, horizons=HORIZONS) -> pd.DataFrame:
    """Walk-forward oblast-model probabilities for every origin of the validation and test periods."""
    vg, vH, vS = inp.vgrid, inp.vH, inp.vS
    hour = lambda ts: vg.minute(ts) // 60  # noqa: E731
    plans = oblast_plans()
    first = val_start - pd.DateOffset(months=MAX_OBLAST_MONTHS)
    K = origin_hours(vg, first, test_end)
    base = build_features(vg, K, vH)
    params = {"num_threads": threads}
    out = []
    for h in horizons:
        df = add_horizon_features(base, vg, vH, h, vS)
        origin = df["origin_hour"].to_numpy()
        for t in TARGETS:
            months, trees = plans[f"{t}_h{h}"]
            y = labels(vg, K, h, vH, t, vS)
            for fs, fe in folds(val_start, test_start) + folds(test_start, test_end):
                tr = ~np.isnan(y) & (origin >= hour(fs - pd.DateOffset(months=months))) & (origin < hour(fs) - EMBARGO_H)
                te = (origin >= hour(fs)) & (origin < hour(fe))
                g = GBM(n_estimators=trees, params=params).fit(df[tr], y[tr])
                out.append(pd.DataFrame({"origin": vg.t0 + pd.to_timedelta(origin[te], unit="h"),
                                         "oblast": df.loc[te, "region"].to_numpy(), "target": t, "horizon": h,
                                         "p": g.predict(df[te])}))
                log(f"oblast model {t} h={h} fold {fs:%m-%d}: {tr.sum():,} train rows")
        del df
    return pd.concat(out, ignore_index=True)


def agreement(inp, start, end) -> pd.DataFrame:
    """Minute-level agreement between the volunteer oblast alert and 'any raion of the oblast' (official)."""
    g, vg = inp.grid, inp.vgrid
    a, b = g.minute(start), g.minute(end)
    va, vb = vg.minute(start), vg.minute(end)
    rows = []
    oblast = np.array([BY_ID[r].oblast for r in g.regions])
    for i, o in enumerate(vg.regions):
        m = oblast == o
        if not m.any():
            continue
        R = g.active[m, a:b]
        anyr = R.any(axis=0)
        v = vg.active[i, va:vb]
        rows.append({
            "oblast": o, "name_uk": uk_name(o), "raions": int(m.sum()),
            "vol_share_time": float(v.mean()), "any_raion_share_time": float(anyr.mean()),
            "p_any_raion_given_vol": float(anyr[v].mean()) if v.any() else None,
            "p_vol_given_any_raion": float(v[anyr].mean()) if anyr.any() else None,
            "mean_raion_share_when_any": float(R[:, anyr].mean()) if anyr.any() else None,
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--reuse-oblast", action="store_true", help="reuse cached oblast-model predictions")
    ap.add_argument("--horizons", default=",".join(map(str, HORIZONS)), help="smoke runs: e.g. 1")
    ap.add_argument("--windows", default=",".join(map(str, WINDOWS_MONTHS)), help="training windows (months) to try")
    ap.add_argument("--rescore", action="store_true",
                    help="rebuild the report from the cached test predictions (adds baselines computed from labels)")
    args = ap.parse_args()
    if args.rescore:
        rescore()
        return
    horizons = [int(x) for x in args.horizons.split(",")]
    windows = [int(x) for x in args.windows.split(",")]
    REPORTS.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    vol = load_alerts()
    raw = read_official()
    known = official_known_until(raw)
    # Alerts still running at the file's last update are missing from it: keep a margin.
    data_end = (known - pd.Timedelta(hours=5)).floor("D")
    test_end = data_end - pd.Timedelta(hours=max(HORIZONS) - 1)  # exclusive: last origin's 6 h label ends at data_end
    test_start = test_end - pd.Timedelta(days=TEST_DAYS)
    val_start = test_start - pd.Timedelta(days=VAL_DAYS)
    log(f"official file known until {known}; data_end {data_end}; val {val_start} .. {test_start}; test {test_start} .. {test_end}")

    cov = coverage(raw)
    cov.to_csv(REPORTS / "coverage.csv", index=False)
    ra = load_official(raw=raw, since=GRID_SINCE)
    ra = ra[ra["start"] >= GRID_SINCE]
    inp = make_inputs(ra, vol, data_end)
    g, H, S = inp.grid, inp.H, inp.S
    R = len(g.regions)
    log(f"raion grid: {R} units from {g.t0} to {g.data_end}")
    agree = agreement(inp, test_start, test_end)
    agree.to_csv(REPORTS / "agreement.csv", index=False)

    cache = CACHE / "oblast_preds.parquet"
    if args.reuse_oblast and cache.exists():
        OP = pd.read_parquet(cache)
    else:
        OP = oblast_predictions(inp, val_start, test_start, test_end, args.threads, horizons)
        OP.to_parquet(cache)

    hour = lambda ts: g.minute(ts) // 60  # noqa: E731
    first_train = val_start - pd.DateOffset(months=max(windows))
    K = origin_hours(g, first_train - pd.Timedelta(days=28), test_end)
    assert (np.diff(K) == 1).all()
    base = build_raion_features(inp, K)
    log(f"raion features: {len(base):,} rows x {base.shape[1]} cols")
    origin_ts = g.t0 + pd.to_timedelta(K, unit="h")
    oblast_of = np.array([BY_ID[r].oblast for r in g.regions])
    params = {"num_threads": args.threads}
    selection, preds, importances = {}, [], []
    for h in horizons:
        df = add_horizon_features(base, g, H, h, S)
        origin = df["origin_hour"].to_numpy()
        for t, (clim_col, pers_col) in TARGETS.items():
            y = labels(g, K, h, H, t, S)
            yo = oblast_labels(inp, K, h, t)
            rate = trailing_rate(y.reshape(R, len(K)), h).reshape(-1)
            share2 = trailing_share(y.reshape(R, len(K)), yo, h)
            share = share2.reshape(-1)
            stale = np.full_like(share2, np.nan)
            stale[:, STALE_H:] = share2[:, :-STALE_H]
            stale = stale.reshape(-1)
            q = OP[(OP["target"] == t) & (OP["horizon"] == h)].pivot(index="origin", columns="oblast", values="p")
            q = q.reindex(origin_ts)
            p_obl = np.vstack([q[o].to_numpy() if o in q else np.full(len(K), np.nan) for o in oblast_of]).reshape(-1)
            known_y = ~np.isnan(y)
            val = (origin >= hour(val_start)) & (origin < hour(test_start))
            yv, cv = y[val], df.loc[val, clim_col].to_numpy()

            # 1. raion models: training window and trees on the validation month (early stopping)
            plans, val_pred = {}, {}
            for kind in ("lightgbm_state", "lightgbm"):
                cands, best = [], None
                for months in windows:
                    tr = known_y & (origin >= hour(val_start - pd.DateOffset(months=months))) & (origin < hour(val_start) - EMBARGO_H)
                    m = GBM(params=params).fit(model_view(df[tr], kind), y[tr], valid=(model_view(df[val], kind), yv))
                    pv = m.predict(model_view(df[val], kind))
                    s_ = scores(yv, pv, cv)
                    if s_["roc_auc"] > 0.995:
                        raise RuntimeError(f"validation AUC {s_['roc_auc']:.4f} is too good to be true: check for label leakage")
                    cands.append({"months": months, "trees": m.n_estimators, "brier": s_["brier"], "roc_auc": s_["roc_auc"]})
                    log(f"{kind} {t} h={h} window={months}: val brier={s_['brier']:.4f} auc={s_['roc_auc']:.4f} trees={m.n_estimators}")
                    if best is None or s_["brier"] < best[0]:
                        best = (s_["brier"], months, m.n_estimators)
                        val_pred[kind] = pv
                plans[kind] = {"candidates": cands, "best_months": best[1], "best_trees": best[2]}
            val_preds = {"climatology_28d": cv, "persistence": df.loc[val, pers_col].to_numpy(), "oblast": p_obl[val],
                         "oblast_x_share": p_obl[val] * share[val], "oblast_x_share_stale30": p_obl[val] * stale[val],
                         **val_pred}
            val_scores = {k: scores(yv, v, cv) for k, v in val_preds.items()}
            chosen = {tier: min(cands_, key=lambda k: val_scores[k]["brier"]) for tier, cands_ in TIERS.items()}
            selection[f"{t}_h{h}"] = {
                **plans, "chosen": chosen,
                "val": {k: {kk: round(float(vv), 5) for kk, vv in v.items()} for k, v in val_scores.items()},
            }
            (REPORTS / "selection.json").write_text(json.dumps(selection, indent=2))

            # 2. walk-forward test: weekly refit on data before each fold
            for i, (fs, fe) in enumerate(folds(test_start, test_end)):
                te = (origin >= hour(fs)) & (origin < hour(fe))
                out = pd.DataFrame({
                    "origin": g.t0 + pd.to_timedelta(origin[te], unit="h"), "raion": df.loc[te, "region"].to_numpy(),
                    "target": t, "horizon": h, "fold": i, "y": y[te], "active_now": df.loc[te, "active_now"].to_numpy(),
                    "climatology_28d": df.loc[te, clim_col].to_numpy(), "rate_28d": rate[te],
                    "persistence": df.loc[te, pers_col].to_numpy(),
                    "oblast": p_obl[te], "share": share[te], "oblast_x_share": p_obl[te] * share[te],
                    "oblast_x_share_stale30": p_obl[te] * stale[te],
                })
                for kind in ("lightgbm_state", "lightgbm"):
                    pl = plans[kind]
                    tr = known_y & (origin >= hour(fs - pd.DateOffset(months=pl["best_months"]))) & (origin < hour(fs) - EMBARGO_H)
                    gbm = GBM(n_estimators=pl["best_trees"], params=params).fit(model_view(df[tr], kind), y[tr])
                    out[kind] = gbm.predict(model_view(df[te], kind))
                    importances.append(gbm.importance().rename(f"{kind}_{t}_h{h}_f{i}"))
                preds.append(out)
                log(f"raion {t} h={h} fold {i} ({fs:%m-%d}): tested on {te.sum():,}")
        del df

    P = pd.concat(preds, ignore_index=True)
    P["oblast_name"] = P["raion"].map(lambda r: BY_ID[r].oblast)
    P.to_parquet(CACHE / "test_predictions.parquet")
    report(P, selection, cov, agree, importances, {
        "official_known_until": str(known), "data_end": str(data_end), "val": [str(val_start), str(test_start)],
        "test": [str(test_start), str(test_end)], "raions": R, "grid_since": str(GRID_SINCE),
    })
    log("done")


def rescore():
    """Report from var/raion_eval/test_predictions.parquet (no refits); adds rate_28d if missing."""
    summary = json.loads((REPORTS / "summary.json").read_text())
    P = pd.read_parquet(CACHE / "test_predictions.parquet")
    if "rate_28d" not in P:
        raw = read_official()
        ra = load_official(raw=raw, since=GRID_SINCE)
        inp = make_inputs(ra[ra["start"] >= GRID_SINCE], load_alerts(), pd.Timestamp(summary["split"]["data_end"]))
        g = inp.grid
        idx = {r: i for i, r in enumerate(g.regions)}
        P["rate_28d"] = np.nan
        for (t, h), q in P.groupby(["target", "horizon"]):
            k0 = g.minute(q["origin"].min()) // 60 - 28 * 24 - h
            K = np.arange(k0, g.minute(q["origin"].max()) // 60 + 1)
            rate = trailing_rate(labels(g, K, h, inp.H, t, inp.S).reshape(len(g.regions), len(K)), h)
            ko = ((q["origin"] - g.t0) / pd.Timedelta(hours=1)).round().astype(int).to_numpy() - k0
            P.loc[q.index, "rate_28d"] = rate[q["raion"].map(idx).to_numpy(), ko]
        P.to_parquet(CACHE / "test_predictions.parquet")
    imp = pd.read_csv(REPORTS / "feature_importance_top30.csv", index_col=0)
    report(P, summary["selection"], None, None, None, summary["split"], importance=imp)
    log("rescored")


def report(P, selection, cov, agree, importances, split, importance=None):
    if P[METHODS].isna().any().any():
        raise RuntimeError("missing predictions in the test set")
    rows, by_obl = [], []
    for (t, h), q in P.groupby(["target", "horizon"]):
        for m in METHODS:
            rows.append({"target": t, "horizon_h": h, "model": m,
                         **scores(q["y"].to_numpy(), q[m].to_numpy(), q["climatology_28d"].to_numpy())})
    for (t, h, o), q in P.groupby(["target", "horizon", "oblast_name"]):
        if (t, h) not in (("active", 1), ("onset", 3)):
            continue
        y, ref = q["y"].to_numpy(), q["climatology_28d"].to_numpy()
        row = {"target": t, "horizon_h": h, "oblast": o, "name_uk": uk_name(o), "raions": q["raion"].nunique(),
               "base_rate": float(y.mean())}
        for m in ("oblast", "oblast_x_share", "oblast_x_share_stale30", "lightgbm_state", "lightgbm"):
            s = scores(y, q[m].to_numpy(), ref)
            row[f"brier_{m}"], row[f"bss_{m}"] = s["brier"], s["bss_vs_clim"]
        by_obl.append(row)
    M, B = pd.DataFrame(rows), pd.DataFrame(by_obl)
    M.to_csv(REPORTS / "metrics_test.csv", index=False)
    B.round(4).to_csv(REPORTS / "by_oblast.csv", index=False)
    if importance is None:
        I = pd.concat(importances, axis=1)
        imp = pd.DataFrame({k: I[[c for c in I.columns if c.startswith(k + "_")]].mean(axis=1)
                            for k in ("lightgbm", "lightgbm_state")})
        imp = imp.sort_values("lightgbm", ascending=False)
        imp.head(30).round(1).to_csv(REPORTS / "feature_importance_top30.csv")
    (REPORTS / "summary.json").write_text(json.dumps({"split": split, "selection": selection}, indent=2, default=str))
    with open(REPORTS / "metrics_test.md", "w") as f:
        f.write(f"Raion-level test (walk-forward, weekly refit): origins {split['test'][0]} .. {split['test'][1]} UTC, "
                f"{split['raions']} units (118 raions + Kyiv City).\n")
        for t in TARGETS:
            q = M[M["target"] == t]
            for metric in ("brier", "bss_vs_clim", "roc_auc"):
                tab = q.pivot(index="model", columns="horizon_h", values=metric).loc[METHODS].round(4)
                f.write(f"\n### target = {t}, {metric}\n\n" + tab.to_markdown() + "\n")
                log(f"{t} {metric}\n{tab.to_string()}")
        f.write("\nBase rates: " + ", ".join(
            f"{t} h={h}: {q['base_rate'].iloc[0]:.3f}" for (t, h), q in M.groupby(["target", "horizon_h"])) + "\n")
        f.write("\nChosen on validation (by data available at forecast time):\n\n| target | " + " | ".join(TIERS) + " |\n|---|"
                + "---|" * len(TIERS) + "\n")
        for k, v in selection.items():
            f.write(f"| {k} | " + " | ".join(v["chosen"][tier] for tier in TIERS) + " |\n")

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for ax, t in zip(axes, TARGETS):
        for m in METHODS[1:]:
            q = M[(M["model"] == m) & (M["target"] == t)]
            ax.plot(q["horizon_h"], q["bss_vs_clim"], marker="o", label=m)
        ax.axhline(0, color="grey", lw=0.8)
        ax.set_ylim(max(ax.get_ylim()[0], -1), None)
        ax.set_xlabel("forecast horizon (hours)")
        ax.set_ylabel("Brier skill vs raion climatology")
        ax.set_title(f"raions, target: {t}")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "skill_by_horizon.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    for ax, (t, h) in zip(axes, (("active", 1), ("onset", 3))):
        q = P[(P["target"] == t) & (P["horizon"] == h)]
        for m in ("climatology_28d", "oblast_x_share", "lightgbm_state", "lightgbm"):
            rel = reliability(q["y"].to_numpy(), q[m].to_numpy()) if len(q) else np.empty((0, 3))
            if len(rel):
                ax.plot(rel[:, 0], rel[:, 1], marker="o", label=m)
        ax.plot([0, 1], [0, 1], color="grey", lw=0.8, ls="--")
        ax.set_title(f"Reliability, raions: {t}, {h} h")
        ax.set_xlabel("forecast probability")
        ax.set_ylabel("observed frequency")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(FIG / "reliability.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
