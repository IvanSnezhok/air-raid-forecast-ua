"""Threat type of the next alert: labels -> features -> walk-forward test -> report.

    python threat-type/run_type.py --posts path/to/tg_kpszsu.parquet

Question answered: "if an alert starts in oblast r within the next h hours,
which threat will it be?" Classes: drone, massed_drone, kab, missile,
ballistic; level = yellow for drone, red for everything else (the scheme
announced on 2026-09-02). Multiply by P(onset) from the main model to get
P(alert of type c within h hours).

Split mirrors the main pipeline: last TEST_DAYS days are the test period,
evaluated in weekly walk-forward folds (retrain before each fold on everything
whose label was known before the fold start); the VAL_DAYS before it pick the
number of boosting rounds.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, log_loss, roc_auc_score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
from airraid.data import load_alerts  # noqa: E402
from features import build  # noqa: E402
from labels import AFTER, TYPES, label_onsets, load_posts, onsets  # noqa: E402

TEST_DAYS, VAL_DAYS, FOLD_DAYS = 28, 28, 7
HORIZONS = (1, 3)
OUT = HERE / "reports"
K = len(TYPES)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def onehot(y):
    Y = np.zeros((len(y), K))
    Y[np.arange(len(y)), y] = 1
    return Y


def scores(y, P):
    P = np.clip(P, 1e-6, 1)
    P = P / P.sum(1, keepdims=True)
    red_true = (y != TYPES.index("drone")).astype(int)
    red_p = 1 - P[:, TYPES.index("drone")]
    out = {
        "n": int(len(y)),
        "log_loss": float(log_loss(y, P, labels=range(K))),
        "brier": float(((P - onehot(y)) ** 2).sum(1).mean()),
        "accuracy": float(accuracy_score(y, P.argmax(1))),
        "macro_f1": float(f1_score(y, P.argmax(1), average="macro", labels=range(K), zero_division=0)),
        "red_brier": float(((red_p - red_true) ** 2).mean()),
        "red_auc": float(roc_auc_score(red_true, red_p)) if 0 < red_true.sum() < len(y) else float("nan"),
    }
    return out


class Prior:
    name = "prior"

    def fit(self, X, y):
        self.p = (np.bincount(y, minlength=K) + 1) / (len(y) + K)
        return self

    def predict(self, X):
        return np.tile(self.p, (len(X), 1))


class RegionPrior:
    """Smoothed share of each type in the region over the training data's last 90 days."""

    name = "region_prior"

    def __init__(self, a=10.0):
        self.a = a

    def fit(self, X, y, origin=None):
        keep = origin >= origin.max() - pd.Timedelta(days=90)
        self.g = Prior().fit(X, y).p
        df = pd.DataFrame({"r": X["region"].astype(str).to_numpy()[keep], "y": y[keep]})
        self.t = {r: (np.bincount(g["y"], minlength=K) + self.a * self.g) / (len(g) + self.a) for r, g in df.groupby("r")}
        return self

    def predict(self, X):
        return np.stack([self.t.get(r, self.g) for r in X["region"].astype(str)])


class LastType:
    """Persistence: the type of the region's last labelled alert, blended with the region prior."""

    name = "persistence"

    def __init__(self, w=0.6):
        self.w = w

    def fit(self, X, y, origin=None):
        self.rp = RegionPrior().fit(X, y, origin)
        return self

    def predict(self, X):
        P = (1 - self.w) * self.rp.predict(X)
        last = X["reg_last_type"].to_numpy().astype(int)
        ok = last >= 0
        P[ok, last[ok]] += self.w
        P[~ok] += self.w * self.rp.predict(X)[~ok]
        return P


PARAMS = dict(objective="multiclass", num_class=K, learning_rate=0.05, num_leaves=31, min_data_in_leaf=40,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1)


def gbm_fit(X, y, rounds, valid=None):
    ds = lgb.Dataset(X, y, categorical_feature=["region"], free_raw_data=False)
    if valid is not None:
        dv = lgb.Dataset(valid[0], valid[1], reference=ds)
        b = lgb.train(PARAMS, ds, num_boost_round=1000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        return b, b.best_iteration
    return lgb.train(PARAMS, ds, num_boost_round=rounds), rounds


def rows_for(lab: pd.DataFrame, h: int) -> pd.DataFrame:
    r = lab[["region", "start", "type"]].copy()
    r["origin"] = r["start"].dt.floor("h") - pd.Timedelta(hours=h - 1)
    r["y"] = r["type"].map(TYPES.index)
    return r.reset_index(drop=True)


BADK_CITY = {
    "Київ": "Kyiv City", "Одеса": "Odeska oblast", "Дніпро": "Dnipropetrovska oblast",
    "Кривий Ріг": "Dnipropetrovska oblast", "Павлоград": "Dnipropetrovska oblast", "Харків": "Kharkivska oblast",
    "Запоріжжя": "Zaporizka oblast", "Кременчук": "Poltavska oblast", "Полтава": "Poltavska oblast",
    "Житомир": "Zhytomyrska oblast", "Херсон": "Khersonska oblast", "Миколаїв": "Mykolaivska oblast",
    "Чернігів": "Chernihivska oblast", "Прилуки": "Chernihivska oblast", "Суми": "Sumska oblast",
    "Вінниця": "Vinnytska oblast", "Олександрія": "Kirovohradska oblast", "Світловодськ": "Kirovohradska oblast",
}


def badk_check(lab: pd.DataFrame, path: Path) -> dict:
    """Agreement of our ballistic labels with the hand-curated BADK registry (city, date)."""
    reg = pd.read_csv(path)
    reg = reg[reg["Категорія загрози"].astype(str).str.startswith("Балістика")]
    reg["region"] = reg["Місто / модель"].map(BADK_CITY)
    reg["date"] = pd.to_datetime(reg["Дата балістичної фази"], errors="coerce").dt.date
    reg = reg.dropna(subset=["region", "date"])
    lo = lab["start"].min().tz_convert("Europe/Kyiv").date()
    hi = lab["start"].max().tz_convert("Europe/Kyiv").date()
    reg = reg[(reg["date"] >= lo) & (reg["date"] <= hi)]
    d = lab.assign(date=lab["start"].dt.tz_convert("Europe/Kyiv").dt.date)
    ours = set(map(tuple, d.loc[d["type"] == "ballistic", ["region", "date"]].to_numpy()))
    any_alert = set(map(tuple, d[["region", "date"]].to_numpy()))
    theirs = set(map(tuple, reg[["region", "date"]].to_numpy()))
    hit = len(theirs & ours)
    return {
        "badk_ballistic_days": len(theirs),
        "found_as_ballistic": hit,
        "recall": round(hit / max(len(theirs), 1), 3),
        "badk_days_with_any_alert": len(theirs & any_alert),
        "our_ballistic_days_in_badk_regions": len({x for x in ours if x[0] in set(BADK_CITY.values())}),
    }


def plot_months(month: pd.DataFrame):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"drone": "#e3b505", "massed_drone": "#e07a1f", "kab": "#8c564b", "missile": "#d62728",
              "ballistic": "#7b1fa2", "unknown": "#c7c7c7"}
    cols = [c for c in TYPES + ["unknown"] if c in month]
    ax = month[cols].plot.bar(stacked=True, figsize=(11, 4.5), color=[colors[c] for c in cols], width=0.85)
    ax.set_xlabel("")
    ax.set_ylabel("alert onsets")
    ax.set_title("Alert onsets by threat type (labels from Air Force posts)")
    ax.legend(ncol=6, fontsize=8, loc="upper left")
    plt.tight_layout()
    plt.savefig(OUT / "types_by_month.png", dpi=120)
    plt.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", required=True)
    ap.add_argument("--badk", default=None, help="BADK registry CSV for a label sanity check")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    posts = load_posts(Path(a.posts))
    alerts = load_alerts()
    lab = label_onsets(onsets(alerts), posts)
    log(f"{len(posts)} posts {posts['date'].min()} .. {posts['date'].max()}; {len(lab)} onsets labelled")
    lab.to_parquet(OUT / "onset_labels.parquet", index=False)
    stats = {
        "posts": int(len(posts)),
        "posts_from": str(posts["date"].min()), "posts_to": str(posts["date"].max()),
        "onsets": int(len(lab)),
        "type_counts": lab["type"].value_counts().to_dict(),
        "label_source": lab["label_source"].value_counts().to_dict(),
    }
    if a.badk:
        stats["badk_check"] = badk_check(lab, Path(a.badk))
    month = lab.assign(m=lab["start"].dt.strftime("%Y-%m")).pivot_table(index="m", columns="type", values="region", aggfunc="count", fill_value=0)
    month.to_csv(OUT / "types_by_month.csv")
    plot_months(month)
    log(json.dumps(stats, ensure_ascii=False))

    known = lab[lab["type"] != "unknown"].reset_index(drop=True)
    end = known["start"].max().floor("D")
    test_start = end - pd.Timedelta(days=TEST_DAYS)
    val_start = test_start - pd.Timedelta(days=VAL_DAYS)

    results, preds = [], []
    for h in HORIZONS:
        R = rows_for(known, h)
        X = build(R, lab, alerts, posts)
        y = R["y"].to_numpy()
        emb = pd.Timedelta(hours=h) + AFTER  # a training label must be known before the evaluated origin
        tr = (R["start"] + AFTER < val_start - pd.Timedelta(hours=h)).to_numpy()
        va = ((R["origin"] >= val_start) & (R["origin"] < test_start)).to_numpy()
        _, rounds = gbm_fit(X[tr], y[tr], 0, valid=(X[va], y[va]))
        rounds = max(rounds, 20)
        log(f"h={h}: train {tr.sum()} val {va.sum()} rounds {rounds}")
        for fs in pd.date_range(test_start, end, freq=f"{FOLD_DAYS}D"):
            fe = fs + pd.Timedelta(days=FOLD_DAYS)
            te = ((R["origin"] >= fs) & (R["origin"] < fe)).to_numpy()
            if not te.any():
                continue
            trn = ((R["start"] + emb) < fs).to_numpy()
            Xtr, ytr, otr = X[trn], y[trn], R.loc[trn, "origin"]
            out = R.loc[te, ["region", "start", "origin", "type", "y"]].copy()
            out["horizon"] = h
            for m in (Prior().fit(Xtr, ytr), RegionPrior().fit(Xtr, ytr, otr), LastType().fit(Xtr, ytr, otr)):
                P = m.predict(X[te])
                for k, c in enumerate(TYPES):
                    out[f"{m.name}__{c}"] = P[:, k]
            b, _ = gbm_fit(Xtr, ytr, rounds)
            P = b.predict(X[te])
            for k, c in enumerate(TYPES):
                out[f"lightgbm__{c}"] = P[:, k]
            preds.append(out)
        imp = pd.Series(b.feature_importance("gain"), index=b.feature_name()).sort_values(ascending=False)
        imp.head(25).to_csv(OUT / f"importance_h{h}.csv")

    P = pd.concat(preds, ignore_index=True)
    P.to_parquet(OUT / "test_predictions.parquet", index=False)
    for h, q in P.groupby("horizon"):
        for m in ("prior", "region_prior", "persistence", "lightgbm"):
            M = q[[f"{m}__{c}" for c in TYPES]].to_numpy()
            results.append({"horizon_h": h, "model": m, **scores(q["y"].to_numpy(), M)})
    res = pd.DataFrame(results)
    res.to_csv(OUT / "metrics_test.csv", index=False)
    q = P[P["horizon"] == 1]
    pred = pd.Categorical([TYPES[i] for i in q[[f"lightgbm__{c}" for c in TYPES]].to_numpy().argmax(1)], categories=TYPES)
    cm = pd.crosstab(pd.Categorical(q["type"], categories=TYPES), pred, rownames=["true"], colnames=["pred"], dropna=False)
    cm.to_csv(OUT / "confusion_h1.csv")
    stats["split"] = {"val": [str(val_start), str(test_start)], "test": [str(test_start), str(end)]}
    (OUT / "run.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False, default=str))
    print(res.to_string(index=False))
    print(cm)


if __name__ == "__main__":
    main()
