"""Forecast engine behind the web app.

Wraps the modelling package (`airraid`) for serving:
  * loads the alert history (volunteer dataset) and refreshes it periodically;
  * trains one LightGBM per (target, horizon), the same way predict_now.py does,
    and caches the boosters on disk so a restart does not retrain;
  * scores each model on a held-out last week before the final refit, so the UI
    can show how the model that is actually serving performed;
  * makes the forecast for the current hour, with live alerts from alerts.in.ua
    added on top of the history when a token is configured. Live records only
    ever extend the history after the dataset ends (airraid.live_alerts.
    after_dataset) and never enter training; their oblast-level state follows
    the training data's definition (see live.py).

Feature engineering and the model class are imported from `airraid` unchanged,
so the app inherits the no-leakage guarantees tested in tests/test_leakage.py.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from airraid.data import build_grid, download, load_alerts
from airraid.evaluate import reliability, scores
from airraid.live_alerts import after_dataset
from airraid.live_alerts import parse_active as parse_records
from airraid.features import (
    HORIZONS, KYIV, add_horizon_features, build_features, hourly_fraction, hourly_starts, labels, origin_hours,
)
from airraid.models import GBM, feature_columns

from .config import Settings
from .journal import ForecastJournal, LiveRecorder
from .threats import ThreatForecast
from .live import LiveState, fetch_active, state_from_alerts, state_from_dataset
from .raion_engine import RaionForecaster, raion_state
from .regions import slug, uk_name

log = logging.getLogger("airraid_app")

TARGETS = ("active", "onset")
# Reference forecasts used for skill scores, same as run.py.
CLIM_COL = {"active": "hod_clim_28d", "onset": "onset_clim_28d"}
PERS_COL = {"active": "active_now", "onset": "onset_last_h"}
EMBARGO_H = max(HORIZONS)
MODEL_FORMAT = 1


class NotReady(RuntimeError):
    pass


@dataclass
class Plan:
    months: int
    trees: int


def _iso(ts: pd.Timestamp | None) -> str | None:
    return None if ts is None or pd.isna(ts) else ts.tz_convert("UTC").isoformat()


def _kyiv(ts: pd.Timestamp) -> str:
    return ts.tz_convert(KYIV).strftime("%Y-%m-%d %H:%M")


class ForecastEngine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.lock = threading.RLock()
        self.status = "starting"  # starting | loading | training | ready | error
        self.message = "Запуск"
        self.alerts: pd.DataFrame | None = None
        self.regions: list[str] = []
        self.hist_end: pd.Timestamp | None = None
        self.data_loaded_at: float = 0.0
        self.models: dict[tuple[str, int], GBM] = {}
        self.meta: dict = {}
        self.holdout: pd.DataFrame | None = None
        self._live: LiveState | None = None
        self._live_at: float = 0.0
        self._forecast: tuple[tuple, dict] | None = None
        self._stop = threading.Event()
        self.recorder = LiveRecorder(settings.state_dir / "live_alerts.csv")
        self.journal = ForecastJournal(settings.state_dir / "forecast_journal.parquet")
        self.threats = ThreatForecast(settings.type_forecast)
        self.raions = RaionForecaster(settings)
        # live raion state: (states by raion id, unmatched location titles, as_of); None without live data
        self._raion_live: tuple[dict, list, pd.Timestamp] | None = None

    # ------------------------------------------------------------------ data
    def load_data(self, refresh: bool | None = None) -> None:
        refresh = self.s.download if refresh is None else refresh
        self._set("loading", "Завантаження історії тривог")
        if refresh:
            try:
                download(self.s.data_path)
            except Exception as e:  # keep serving the previous copy
                if not self.s.data_path.exists():
                    raise
                log.warning("dataset download failed, using cached copy: %s", e)
        alerts = load_alerts(self.s.data_path)
        with self.lock:
            self.alerts = alerts
            self.regions = sorted(alerts["region"].unique())
            self.hist_end = alerts["start"].max()
            self.data_loaded_at = time.time()
            self._forecast = None
        log.info("history: %d alerts, last start %s", len(alerts), self.hist_end)
        self.raions.load(refresh)

    # ---------------------------------------------------------------- models
    def _plans(self) -> dict[tuple[str, int], Plan]:
        """Training window and tree count per model.

        Uses the selection made on the validation month by run.py when
        reports/run_summary.json exists, else the defaults from Settings.
        """
        selection = {}
        path = self.s.reports_dir / "run_summary.json"
        if path.exists():
            try:
                selection = json.loads(path.read_text()).get("selection", {})
            except (OSError, ValueError):
                log.warning("could not read %s", path)
        plans = {}
        for t in TARGETS:
            for h in HORIZONS:
                sel = selection.get(f"{t}_h{h}")
                if sel:
                    months = sel.get("best_months") or self.s.max_train_months
                    plans[(t, h)] = Plan(min(int(months), self.s.max_train_months), int(sel.get("best_trees") or self.s.n_trees))
                else:
                    plans[(t, h)] = Plan(self.s.train_months, self.s.n_trees)
        return plans

    def train(self) -> None:
        """Holdout evaluation on the last `holdout_days`, then refit on everything."""
        with self.lock:
            alerts, hist_end = self.alerts, self.hist_end
        if alerts is None:
            raise NotReady("no data")
        self._set("training", "Навчання моделей")
        t_start = time.time()
        plans = self._plans()
        data_end = hist_end.floor("h")
        grid = build_grid(alerts, data_end=data_end)
        H, S = hourly_fraction(grid), hourly_starts(grid)
        ho_start = data_end - pd.Timedelta(days=self.s.holdout_days)
        first = ho_start - pd.DateOffset(months=max(p.months for p in plans.values()))
        K = origin_hours(grid, first, data_end)
        base = build_features(grid, K, H)
        hour = lambda ts: grid.minute(ts) // 60  # noqa: E731
        params = {"num_threads": self.s.lgb_threads}

        models, rows, preds = {}, [], []
        n_done, n_total = 0, len(plans)
        for h in HORIZONS:
            df = add_horizon_features(base, grid, H, h, S)
            origin = df["origin_hour"].to_numpy()
            for t in TARGETS:
                plan = plans[(t, h)]
                y = labels(grid, K, h, H, t, S)
                known = ~np.isnan(y)
                # 1. holdout: train on [ho_start - months, ho_start - embargo), test on [ho_start, end)
                lo = hour(ho_start - pd.DateOffset(months=plan.months))
                tr = known & (origin >= lo) & (origin < hour(ho_start) - EMBARGO_H)
                te = known & (origin >= hour(ho_start))
                if tr.sum() and te.sum():
                    g = GBM(n_estimators=plan.trees, params=params).fit(df[tr], y[tr])
                    p = g.predict(df[te])
                    yt, clim, pers = y[te], df.loc[te, CLIM_COL[t]].to_numpy(), df.loc[te, PERS_COL[t]].to_numpy()
                    for name, q in (("lightgbm", p), ("climatology_28d", clim), ("persistence", pers)):
                        rows.append({"target": t, "horizon": h, "model": name, **scores(yt, q, clim)})
                    preds.append(pd.DataFrame({
                        "origin_hour": origin[te], "region": df.loc[te, "region"].to_numpy(), "target": t,
                        "horizon": h, "y": yt, "p": p, "clim": clim,
                    }))
                # 2. final model on the latest `months` of fully observed labels
                lo = hour(data_end - pd.DateOffset(months=plan.months))
                m = known & (origin >= lo)
                models[(t, h)] = GBM(n_estimators=plan.trees, params=params).fit(df[m], y[m])
                n_done += 1
                self._set("training", f"Навчання моделей ({n_done}/{n_total})")
            del df

        holdout = pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()
        if not holdout.empty:
            holdout["origin"] = grid.t0 + pd.to_timedelta(holdout.pop("origin_hour"), unit="h")
        meta = {
            "format": MODEL_FORMAT,
            "trained_at": _iso(pd.Timestamp.now(tz="UTC")),
            "data_end": _iso(data_end),
            "holdout_start": _iso(ho_start),
            "holdout_days": self.s.holdout_days,
            "regions": grid.regions,
            "plans": {f"{t}_h{h}": vars(p) for (t, h), p in plans.items()},
            "selection_source": "reports/run_summary.json" if (self.s.reports_dir / "run_summary.json").exists() else "defaults",
            "train_seconds": round(time.time() - t_start, 1),
            "holdout_scores": rows,
        }
        self._save(models, meta, holdout)
        self._prepare_raions(alerts)
        with self.lock:
            self.models, self.meta, self.holdout = models, meta, holdout
            self._forecast = None
        self._set("ready", "Готово")
        log.info("trained %d models in %.0fs", len(models), meta["train_seconds"])

    def _save(self, models, meta, holdout) -> None:
        d = self.s.model_dir
        d.mkdir(parents=True, exist_ok=True)
        for (t, h), g in models.items():
            g.model.save_model(str(d / f"{t}_h{h}.txt"), num_iteration=g.n_estimators)
        if not holdout.empty:
            holdout.to_parquet(d / "holdout.parquet")
        (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))

    def load_models(self) -> bool:
        """Load cached boosters if they exist and are recent enough for the data."""
        d = self.s.model_dir
        try:
            meta = json.loads((d / "meta.json").read_text())
        except (OSError, ValueError):
            return False
        if meta.get("format") != MODEL_FORMAT or meta.get("regions") != self.regions:
            return False
        if meta.get("plans") != {f"{t}_h{h}": vars(p) for (t, h), p in self._plans().items()}:
            return False  # training window / trees changed (e.g. run.py produced a new selection)
        if self.hist_end is not None:
            age = self.hist_end - pd.Timestamp(meta["data_end"])
            if age > pd.Timedelta(hours=self.s.retrain_h):
                return False
        models = {}
        for t in TARGETS:
            for h in HORIZONS:
                path = d / f"{t}_h{h}.txt"
                if not path.exists():
                    return False
                g = GBM(n_estimators=None)
                g.model = lgb.Booster(model_file=str(path))
                g.regions = meta["regions"]
                models[(t, h)] = g
        holdout = pd.read_parquet(d / "holdout.parquet") if (d / "holdout.parquet").exists() else pd.DataFrame()
        self._prepare_raions(self.alerts)
        with self.lock:
            self.models, self.meta, self.holdout = models, meta, holdout
            self._forecast = None
        self._set("ready", "Готово")
        log.info("loaded cached models trained on data to %s", meta["data_end"])
        return True

    def _prepare_raions(self, alerts: pd.DataFrame) -> None:
        # the moment forecasts will be made for: now with live data, else the end of the dataset
        now = pd.Timestamp.now(tz="UTC") if self.s.alerts_token else self.hist_end.floor("h")
        try:
            self.raions.prepare(self._history(alerts), now, self.s.lgb_threads)
        except Exception:  # noqa: BLE001 - the oblast forecast must not depend on the raion layer
            log.exception("raion layer preparation failed")

    # ------------------------------------------------------------------ live
    def live_state(self, max_age: float | None = None) -> LiveState:
        max_age = self.s.live_refresh_s if max_age is None else max_age
        with self.lock:
            if self._live is not None and time.time() - self._live_at < max_age:
                return self._live
            alerts, regions = self.alerts, self.regions
        if alerts is None:
            raise NotReady("no data")
        state, raion_live = None, None
        if self.s.alerts_token:
            try:
                records = parse_records(fetch_active(self.s.alerts_token))
                now = pd.Timestamp.now(tz="UTC")
                state = state_from_alerts(records, regions, now, cleared=self.recorder.cleared(records, now))
                states, unmatched = raion_state(records)  # display state per raion (all levels)
                raion_live = (states, unmatched, state.as_of)
            except Exception as e:
                log.warning("alerts.in.ua failed: %s", e)
                state = state_from_dataset(alerts, regions, error=f"alerts.in.ua недоступний: {type(e).__name__}")
        if state is None:
            state = state_from_dataset(alerts, regions)
        self.recorder.observe(state)
        with self.lock:
            self._live, self._live_at = state, time.time()
            self._raion_live = raion_live
        return state

    # -------------------------------------------------------------- forecast
    def forecast(self) -> dict:
        with self.lock:
            if not self.models:
                raise NotReady(self.message)
        live = self.live_state()
        with self.lock:
            alerts, hist_end = self.alerts, self.hist_end
        if live.source == "alerts.in.ua":
            origin = live.as_of.floor("h")
        else:
            origin = hist_end.floor("h")
        ongoing = live.ongoing_alerts()
        ongoing = ongoing[ongoing["start"] < origin] if live.source == "alerts.in.ua" else ongoing.iloc[0:0]
        rl = self._raion_live
        raion_key = None if rl is None else tuple(sorted((k, str(v.since), v.level) for k, v in rl[0].items() if v.active))
        key = (origin, hist_end, tuple(map(tuple, ongoing.astype(str).to_numpy())), self.recorder.version(), raion_key)
        with self.lock:
            if self._forecast and self._forecast[0] == key:
                return self._forecast[1]
        result, journal_rows = self._compute(alerts, hist_end, origin, ongoing, live)
        with self.lock:
            self._forecast = (key, result)
        if not self.journal.has(origin):
            self.journal.record(journal_rows)
        return result

    def _history(self, alerts: pd.DataFrame) -> pd.DataFrame:
        """Dataset history plus alerts recorded from live polls after it (never before its end)."""
        rec = after_dataset(self.recorder.alerts(), alerts)
        if rec.empty:
            return alerts
        return pd.concat([alerts, rec[rec["region"].isin(self.regions)]], ignore_index=True)

    def _compute(self, alerts, hist_end, origin, ongoing, live: LiveState) -> tuple[dict, pd.DataFrame]:
        dataset, alerts = alerts, self._history(alerts)
        if not ongoing.empty:
            extra = ongoing.assign(end=origin + pd.Timedelta(hours=1), naive=False)  # end is never used as a feature
            alerts = pd.concat([alerts, after_dataset(extra, dataset)], ignore_index=True)
        grid = build_grid(alerts, data_end=origin)
        H, S = hourly_fraction(grid), hourly_starts(grid)
        k_now = grid.n_minutes // 60
        base = build_features(grid, np.array([k_now]), H)
        probs = {r: {t: {} for t in TARGETS} for r in grid.regions}
        journal = []
        for h in HORIZONS:
            x = add_horizon_features(base, grid, H, h, S)
            for t in TARGETS:
                model = self.models[(t, h)]
                expected = model.model.feature_name()
                got = feature_columns(x) + ["region"]
                if sorted(expected) != sorted(got):
                    raise RuntimeError("feature set changed since the models were trained; retrain needed")
                pred = model.predict(x)
                for r, p in zip(x["region"], pred):
                    probs[r][t][h] = round(float(p), 4)
                journal.append(pd.DataFrame({
                    "origin": origin, "region": x["region"].to_numpy(), "target": t, "horizon": h,
                    "p": pred, "clim": x[CLIM_COL[t]].to_numpy().astype(float),
                    "issued_at": pd.Timestamp.now(tz="UTC"), "live": live.source == "alerts.in.ua",
                }))

        # Recent history for charts: hourly share under alert, last 72 h and 28-day hour-of-day profile.
        n72 = min(72, H.shape[1])
        hours = grid.t0 + pd.to_timedelta(np.arange(H.shape[1] - n72, H.shape[1]), unit="h")
        local_hour = (grid.t0 + pd.to_timedelta(np.arange(H.shape[1]), unit="h")).tz_convert(KYIV).hour.to_numpy()
        last28 = slice(max(0, H.shape[1] - 28 * 24), H.shape[1])
        profile = {}
        for i, r in enumerate(grid.regions):
            hh, lh = H[i, last28] > 0, local_hour[last28]
            profile[r] = [round(float(hh[lh == x].mean()), 3) if (lh == x).any() else 0.0 for x in range(24)]

        # Daily calendar: minutes under alert per Kyiv-local day over the last year.
        n_day = min(H.shape[1], 367 * 24)
        hrs = grid.t0 + pd.to_timedelta(np.arange(H.shape[1] - n_day, H.shape[1]), unit="h")
        day_key = hrs.tz_convert(KYIV).normalize().tz_localize(None)
        daily_df = pd.DataFrame((H[:, -n_day:] * 60).T, columns=grid.regions).groupby(day_key).sum().round().astype(int)
        daily_df = daily_df.iloc[-366:]
        daily_start = str(daily_df.index[0].date()) if len(daily_df) else None

        windows = {
            h: {
                "active": [_iso(origin + pd.Timedelta(hours=h - 1)), _iso(origin + pd.Timedelta(hours=h))],
                "onset": [_iso(origin), _iso(origin + pd.Timedelta(hours=h))],
                "active_kyiv": [_kyiv(origin + pd.Timedelta(hours=h - 1)), _kyiv(origin + pd.Timedelta(hours=h))],
                "onset_kyiv": [_kyiv(origin), _kyiv(origin + pd.Timedelta(hours=h))],
            }
            for h in HORIZONS
        }
        known = self.recorder.known_until(hist_end)
        cs = self.recorder.covered_since
        gap_h = ((cs - hist_end) if cs is not None and cs > hist_end else (origin - known)) / pd.Timedelta(hours=1)
        warnings = []
        if live.source == "dataset":
            warnings.append("Немає живих даних (ALERTS_IN_UA_TOKEN не задано або API недоступне): "
                            "прогноз зроблено станом на кінець датасету.")
        elif gap_h > 6:
            warnings.append(f"Історія закінчується за {gap_h:.0f} год до моменту прогнозу: "
                            "ознаки за останні години можуть бути занижені.")
        if live.error:
            warnings.append(live.error)
        result = {
            "origin": _iso(origin),
            "origin_kyiv": _kyiv(origin),
            "history_end": _iso(hist_end),
            "history_end_kyiv": _kyiv(hist_end),
            "live_source": live.source,
            "horizons": list(HORIZONS),
            "windows": windows,
            "warnings": warnings,
            "model": {k: self.meta.get(k) for k in ("trained_at", "data_end", "selection_source")},
            "regions": [
                {
                    "id": slug(r), "name": r, "name_uk": uk_name(r),
                    "active_at_origin": bool(grid.active[i, -1]) if grid.n_minutes else False,
                    "probs": probs[r],
                    "history": {"start": _iso(hours[0]) if n72 else None,
                                "hourly": [round(float(v), 3) for v in H[i, -n72:]]},
                    "hour_profile_28d": profile[r],
                    "daily_minutes": {"start": daily_start, "values": daily_df[r].tolist()},
                }
                for i, r in enumerate(grid.regions)
            ],
        }
        result["raions"] = self._raion_forecast(origin, probs, alerts)
        return result, pd.concat(journal, ignore_index=True)

    def _raion_forecast(self, origin, probs, alerts) -> dict | None:
        rl = self._raion_live
        try:
            return self.raions.forecast(origin, probs, rl[0] if rl else None, rl[2] if rl else None, alerts,
                                        rl[1] if rl else None)
        except Exception:  # noqa: BLE001 - keep serving oblasts
            log.exception("raion forecast failed")
            return None

    # ---------------------------------------------------------- verification
    def verification(self, region: str | None = None, recent_hours: int = 24) -> dict:
        """Compare issued forecasts with what happened, for windows that are fully observed."""
        with self.lock:
            alerts, hist_end = self.alerts, self.hist_end
        if alerts is None:
            raise NotReady("no data")
        J, known = self._verified_journal(alerts, hist_end)
        out = {"journal_since": _iso(J["origin"].min()) if len(J) else None, "known_until": _iso(known),
               "n_issued": int(J["origin"].nunique()) if len(J) else 0, "scores": [], "recent": {}}
        if J.empty:
            return out
        V = J.dropna(subset=["y"])
        out["n_verified"] = int(V["origin"].nunique())
        for (t, h), q in V.groupby(["target", "horizon"]):
            y, p, c = q["y"].to_numpy(), q["p"].to_numpy(), q["clim"].to_numpy()
            hi, lo = p >= 0.5, p < 0.2
            out["scores"].append({
                "target": t, "horizon": int(h), "n": int(len(q)), "base_rate": float(y.mean()),
                "brier": float(np.mean((p - y) ** 2)), "brier_clim": float(np.mean((c - y) ** 2)),
                "bss_vs_clim": float(1 - np.mean((p - y) ** 2) / np.mean((c - y) ** 2)) if np.mean((c - y) ** 2) > 0 else None,
                "n_high": int(hi.sum()), "hit_rate_high": float(y[hi].mean()) if hi.any() else None,
                "n_low": int(lo.sum()), "event_rate_low": float(y[lo].mean()) if lo.any() else None,
            })
        out["scores"] = _clean(out["scores"])
        if region is not None:
            for t, h in (("active", 1), ("onset", 3)):
                q = J[(J["region"] == region) & (J["target"] == t) & (J["horizon"] == h)].sort_values("origin").tail(recent_hours)
                out["recent"][f"{t}_h{h}"] = [
                    {"origin": _iso(o), "p": round(float(p), 3), "y": None if np.isnan(y) else int(y)}
                    for o, p, y in zip(q["origin"], q["p"], q["y"])
                ]
        return out

    def _verified_journal(self, alerts, hist_end) -> tuple[pd.DataFrame, pd.Timestamp]:
        """Journal rows with the observed outcome `y` (NaN while the window is not fully observed). Cached."""
        J = self.journal.frame()
        known = self.recorder.known_until(hist_end)
        key = (len(J), J["origin"].max() if len(J) else None, known.floor("h"), hist_end, self.recorder.version())
        with self.lock:
            if getattr(self, "_verified", None) and self._verified[0] == key:
                return self._verified[1], known
        if J.empty:
            return J.assign(y=np.nan), known
        grid = build_grid(self._history(alerts), data_end=known.floor("h"))
        H, S = hourly_fraction(grid), hourly_starts(grid)
        J["k"] = ((J["origin"] - grid.t0) / pd.Timedelta(hours=1)).round().astype(int)
        J = J[J["region"].isin(grid.regions) & (J["k"] >= 1)].reset_index(drop=True)
        r_idx = {r: i for i, r in enumerate(grid.regions)}
        ys = np.full(len(J), np.nan)
        for (t, h), q in J.groupby(["target", "horizon"]):
            ks = q["k"].to_numpy()
            ok = ks + h <= H.shape[1]
            ri = q["region"].map(r_idx).to_numpy()
            y = np.full(len(q), np.nan)
            if t == "active":
                y[ok] = H[ri[ok], ks[ok] + h - 1] > 0
            else:
                win = ks[ok][:, None] + np.arange(h)
                y[ok] = S[ri[ok][:, None], win].max(axis=1)
            ys[q.index.to_numpy()] = y
        J["y"] = ys
        with self.lock:
            self._verified = (key, J)
        return J, known

    # --------------------------------------------------------------- metrics
    def metrics(self) -> dict:
        with self.lock:
            meta, holdout = self.meta, self.holdout
        out: dict = {"holdout": None, "walk_forward": None}
        if meta:
            out["holdout"] = {
                "start": meta.get("holdout_start"), "end": meta.get("data_end"), "days": meta.get("holdout_days"),
                "trained_at": meta.get("trained_at"), "plans": meta.get("plans"),
                "selection_source": meta.get("selection_source"),
                "scores": _clean(meta.get("holdout_scores", [])),
            }
            if holdout is not None and not holdout.empty:
                rel, by_region = {}, []
                for (t, h), q in holdout.groupby(["target", "horizon"]):
                    r = reliability(q["y"].to_numpy(), q["p"].to_numpy())
                    rel[f"{t}_h{h}"] = [[round(float(a), 4), round(float(b), 4), int(c)] for a, b, c in r]
                for (t, h, reg), q in holdout.groupby(["target", "horizon", "region"]):
                    s = scores(q["y"].to_numpy(), q["p"].to_numpy(), q["clim"].to_numpy())
                    by_region.append({"target": t, "horizon": int(h), "region": reg, "name_uk": uk_name(reg),
                                      "brier": s["brier"], "bss_vs_clim": s["bss_vs_clim"], "roc_auc": s["roc_auc"],
                                      "base_rate": s["base_rate"]})
                out["holdout"]["reliability"] = rel
                out["holdout"]["by_region"] = _clean(by_region)
        wf = self.s.reports_dir / "metrics_test.csv"
        if wf.exists():
            try:
                M = pd.read_csv(wf)
                summary = json.loads((self.s.reports_dir / "run_summary.json").read_text()).get("split", {}) \
                    if (self.s.reports_dir / "run_summary.json").exists() else {}
                out["walk_forward"] = {"test": summary.get("test"), "scores": _clean(M.to_dict("records"))}
            except (OSError, ValueError) as e:
                log.warning("could not read walk-forward metrics: %s", e)
        return out

    def region_holdout(self, region: str) -> dict:
        with self.lock:
            holdout = self.holdout
        if holdout is None or holdout.empty:
            return {}
        q = holdout[holdout["region"] == region]
        out = {}
        for (t, h) in (("active", 1), ("onset", 3)):
            s = q[(q["target"] == t) & (q["horizon"] == h)].sort_values("origin")
            if not s.empty:
                out[f"{t}_h{h}"] = {"start": _iso(s["origin"].iloc[0]), "p": s["p"].round(3).tolist(),
                                    "y": s["y"].astype(int).tolist()}
        return out

    # ------------------------------------------------------------ lifecycle
    def _set(self, status: str, message: str) -> None:
        self.status, self.message = status, message

    def bootstrap(self) -> None:
        try:
            self.load_data()
            if not self.load_models():
                self.train()
            self.forecast()
        except Exception as e:
            log.exception("bootstrap failed")
            self._set("error", f"Помилка: {e}")

    def tick(self) -> None:
        """One step of the background loop: refresh data, retrain when stale, precompute the forecast."""
        now = time.time()
        if now - self.data_loaded_at > self.s.data_refresh_h * 3600:
            self.load_data()
            data_end = pd.Timestamp(self.meta["data_end"]) if self.meta else None
            if data_end is None or self.hist_end - data_end > pd.Timedelta(hours=self.s.retrain_h):
                self.train()
        if self.models:
            self.forecast()
            self.verification()  # warm the cache for the UI

    def run_forever(self, interval: float = 30.0) -> None:
        self.bootstrap()
        while not self._stop.wait(interval):
            try:
                self.tick()
            except Exception:
                log.exception("background refresh failed")
                if not self.models:
                    self._set("error", "Помилка оновлення, див. логи")

    def start_background(self) -> threading.Thread:
        th = threading.Thread(target=self.run_forever, name="forecast-engine", daemon=True)
        th.start()
        return th

    def stop(self) -> None:
        self._stop.set()


def _clean(rows: list[dict]) -> list[dict]:
    """JSON-safe floats (NaN -> None), rounded."""
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            if isinstance(v, (float, np.floating)):
                d[k] = None if not np.isfinite(v) else round(float(v), 5)
            elif isinstance(v, np.integer):
                d[k] = int(v)
            else:
                d[k] = v
        out.append(d)
    return out
