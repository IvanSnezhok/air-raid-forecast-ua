"""Forecasters: naive baselines, logistic regression and gradient boosting.

Every forecaster has the same interface: fit(train_df, y) then predict(df) -> P(alert).
"""
from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

NON_FEATURES = {"origin_hour", "region", "horizon"}


def feature_columns(df: pd.DataFrame) -> list[str]:
    # Label columns ("y", "y_active", "y_onset") must never be features: the first
    # full run trained on the label and scored AUC 1.0 on validation.
    return [c for c in df.columns if c not in NON_FEATURES and not (c == "y" or c.startswith("y_"))]


class Climatology:
    """Share of the last 28 days on which the event happened in the same window of the day."""

    name = "climatology_28d"

    def __init__(self, col: str = "hod_clim_28d"):
        self.col = col

    def fit(self, df, y):
        return self

    def predict(self, df):
        return df[self.col].to_numpy()


class Persistence:
    """What just happened will happen again: 0/1 forecast from a "recent state" column.

    active target: alert active right now; onset target: an alert started in the last h hours.
    """

    name = "persistence"

    def __init__(self, col: str = "active_now"):
        self.col = col

    def fit(self, df, y):
        return self

    def predict(self, df):
        return df[self.col].to_numpy()


class MarkovPersistence:
    """P(alert in target hour | region, alert active now), estimated on the training window."""

    name = "markov_persistence"

    def fit(self, df, y):
        t = pd.DataFrame({"region": df["region"].to_numpy(), "now": df["active_now"].to_numpy(), "y": y})
        # light smoothing towards the overall rate keeps rare cells sane
        g = t.groupby(["region", "now"])["y"].agg(["sum", "count"])
        prior, m = t["y"].mean(), 20
        self.table = ((g["sum"] + m * prior) / (g["count"] + m)).to_dict()
        self.prior = prior
        return self

    def predict(self, df):
        keys = zip(df["region"].to_numpy(), df["active_now"].to_numpy())
        return np.array([self.table.get(k, self.prior) for k in keys])


class LogReg:
    name = "logistic_regression"

    def fit(self, df, y):
        X = self._X(df, fit=True)
        self.model = make_pipeline(
            SimpleImputer(strategy="constant", fill_value=-1), StandardScaler(), LogisticRegression(C=0.1, max_iter=500)
        )
        self.model.fit(X, y)
        return self

    def _X(self, df, fit=False):
        X = df[feature_columns(df)].copy()
        if fit:
            self.regions = sorted(df["region"].unique())
        for r in self.regions:
            X[f"is_{r}"] = (df["region"] == r).astype(np.float32)
        return X

    def predict(self, df):
        return self.model.predict_proba(self._X(df))[:, 1]


LGB_PARAMS = dict(
    objective="binary",
    learning_rate=0.05,
    num_leaves=63,
    min_child_samples=100,
    feature_fraction=0.7,
    bagging_fraction=0.8,
    bagging_freq=1,
    lambda_l2=1.0,
    verbose=-1,
    num_threads=4,
    seed=42,
)


class GBM:
    name = "lightgbm"

    def __init__(self, n_estimators: int | None = None, params: dict | None = None):
        self.n_estimators = n_estimators
        self.params = {**LGB_PARAMS, **(params or {})}

    def _X(self, df):
        X = df[feature_columns(df)].copy()
        X["region"] = pd.Categorical(df["region"], categories=self.regions)
        return X

    def fit(self, df, y, valid: tuple[pd.DataFrame, np.ndarray] | None = None):
        self.regions = sorted(df["region"].unique())
        dtrain = lgb.Dataset(self._X(df), y, categorical_feature=["region"])
        if valid is not None:
            dvalid = lgb.Dataset(self._X(valid[0]), valid[1], reference=dtrain)
            self.model = lgb.train(
                self.params, dtrain, 2000, valid_sets=[dvalid],
                callbacks=[lgb.early_stopping(100, verbose=False)],
            )
            self.n_estimators = self.model.best_iteration
        else:
            self.model = lgb.train(self.params, dtrain, self.n_estimators or 500)
        return self

    def predict(self, df):
        return self.model.predict(self._X(df), num_iteration=self.n_estimators)

    def importance(self) -> pd.Series:
        imp = self.model.feature_importance("gain")
        return pd.Series(imp, index=self.model.feature_name()).sort_values(ascending=False)
