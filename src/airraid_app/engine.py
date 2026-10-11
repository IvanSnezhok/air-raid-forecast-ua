"""Forecast engine behind the web app and the static site.

Wraps the modelling package (`airraid`) for serving:
  * loads the alert history (volunteer dataset) and refreshes it periodically;
  * serves the model bundle of airraid.training (one LightGBM per target and
    horizon, holdout-scored on the last week before the final refit): the same
    files the train job publishes and the 10-minute pipeline forecasts with.
    It trains only when no compatible bundle exists;
  * makes the forecast with airraid.nowcast (same origin rule and features as
    predict_now.py): origin = the first whole minute after the newest live
    observation, hour blocks aligned to end at it.

Two sources of live data:
  * server mode (default): polls alerts.in.ua itself when a token is set and
    turns the polls into oblast-level alerts with the training data's
    definition (journal.LiveRecorder, airraid.live_alerts);
  * store mode (`Settings.store_dir`, AIRRAID_STORE): reads the point-in-time store
    the pipeline writes (src/airraid/store.py): the newest poll, the gap after
    the dataset (live_alerts.store_records), the forecasts issued by
    predict_now.py (served as logged, so the site shows exactly the verified
    numbers) and the verification. The static GitHub Pages build uses this.
Live records only ever extend the history after the dataset ends and never
enter training.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np
import pandas as pd

from airraid import nowcast, training
from airraid import store as pit
from airraid.data import build_grid, download, load_alerts
from airraid.evaluate import reliability, scores
from airraid.features import HORIZONS, KYIV, hourly_fraction
from airraid.live_alerts import LIVE_EXPIRY_HOURS, after_dataset, from_frame, store_records
from airraid.live_alerts import parse_active as parse_records
from airraid.training import TARGETS, Plan  # noqa: F401  (Plan re-exported for old imports)
from airraid.verification import outcomes, site_summary

from .config import Settings
from .journal import ForecastJournal, LiveRecorder
from .live import LiveState, RegionState, fetch_active, state_from_alerts, state_from_dataset
from .regions import slug, uk_name
from .threats import ThreatForecast

log = logging.getLogger("airraid_app")

MODEL_FORMAT = training.MODEL_FORMAT
JOURNAL_EVERY = pd.Timedelta(minutes=10)  # server mode: journal at most one origin per 10 minutes
VERIFY_DAYS = 8


class NotReady(RuntimeError):
    pass


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
        self.models: dict = {}  # (target, horizon) -> airraid.models.GBM
        self.meta: dict = {}
        self.holdout: pd.DataFrame | None = None
        self._live: LiveState | None = None
        self._live_at: float = 0.0
        self._forecast: tuple[tuple, dict] | None = None
        self._stop = threading.Event()
        self.recorder = LiveRecorder(settings.state_dir / "live_alerts.csv")
        self.journal = ForecastJournal(settings.state_dir / "forecast_journal.parquet")
        self.threats = ThreatForecast(settings.type_forecast)

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

    # ---------------------------------------------------------------- models
    def _plans(self) -> dict[tuple[str, int], Plan]:
        """Training window and tree count per model (airraid.training.plans)."""
        return training.plans(self.s.reports_dir, self.s.train_months, self.s.max_train_months, self.s.n_trees)[0]

    def train(self) -> None:
        """Holdout evaluation on the last `holdout_days`, then refit on everything (airraid.training)."""
        with self.lock:
            alerts = self.alerts
        if alerts is None:
            raise NotReady("no data")
        self._set("training", "Навчання моделей")
        plan, source = training.plans(self.s.reports_dir, self.s.train_months, self.s.max_train_months, self.s.n_trees)
        models, meta, holdout = training.train(
            alerts, plan, self.s.holdout_days, self.s.lgb_threads, selection_source=source,
            progress=lambda n, total: self._set("training", f"Навчання моделей ({n}/{total})"))
        training.save(self.s.model_dir, models, meta, holdout)
        with self.lock:
            self.models, self.meta, self.holdout = models, meta, holdout
            self._forecast = None
        self._set("ready", "Готово")
        log.info("trained %d models in %.0fs", len(models), meta["train_seconds"])

    def load_models(self) -> bool:
        """Load the saved bundle if it is compatible and (server mode) recent enough for the data.

        In store mode the age is not checked: the pipeline decides when to
        retrain, and the store's forecasts were made with exactly this bundle.
        """
        b = training.load(self.s.model_dir, self.regions, self._plans())
        if b is None:
            return False
        models, meta, holdout = b
        if self.hist_end is not None and self.s.store_dir is None:
            age = self.hist_end - pd.Timestamp(meta["data_end"])
            if age > pd.Timedelta(hours=self.s.retrain_h):
                return False
        with self.lock:
            self.models, self.meta, self.holdout = models, meta, holdout
            self._forecast = None
        self._set("ready", "Готово")
        log.info("loaded saved models trained on data to %s", meta["data_end"])
        return True

    # ------------------------------------------------------------------ live
    def live_state(self, max_age: float | None = None) -> LiveState:
        if self.s.store_dir is not None:
            return self._store_snapshot()["live"]
        max_age = self.s.live_refresh_s if max_age is None else max_age
        with self.lock:
            if self._live is not None and time.time() - self._live_at < max_age:
                return self._live
            alerts, regions = self.alerts, self.regions
        if alerts is None:
            raise NotReady("no data")
        state = None
        if self.s.alerts_token:
            try:
                records = parse_records(fetch_active(self.s.alerts_token))
                now = pd.Timestamp.now(tz="UTC")
                state = state_from_alerts(records, regions, now, cleared=self.recorder.cleared(records, now))
            except Exception as e:
                log.warning("alerts.in.ua failed: %s", e)
                state = state_from_dataset(alerts, regions, error=f"alerts.in.ua недоступний: {type(e).__name__}")
        if state is None:
            state = state_from_dataset(alerts, regions)
        self.recorder.observe(state)
        with self.lock:
            self._live, self._live_at = state, time.time()
        return state

    def _store_snapshot(self) -> dict:
        """Store mode: origin, feature grid, live state and coverage from the newest store poll. Cached."""
        with self.lock:
            alerts, hist_end, regions = self.alerts, self.hist_end, self.regions
        if alerts is None:
            raise NotReady("no data")
        root = self.s.store_dir
        now = pd.Timestamp.now(tz="UTC")
        polls = pit.load(root, "polls", as_of=now, since=now - pd.Timedelta(days=1))
        seen = polls["observed_at"].max() if len(polls) else None
        key = (seen, hist_end)
        with self.lock:
            snap = getattr(self, "_snap", None)
            if snap is not None and snap["key"] == key:
                return snap
        if seen is None or seen < hist_end or now - seen > pd.Timedelta(hours=LIVE_EXPIRY_HOURS):
            live = state_from_dataset(alerts, regions, error=None if seen is None else
                                      f"Останнє опитування alerts.in.ua було {_kyiv(seen)}: показано стан на кінець датасету.")
            origin, records, covered = hist_end.floor("h"), None, None
        else:
            origin = nowcast.origin_after(seen)
            records, covered = store_records(root, origin, hist_end)
            live = None
        grid, gap = nowcast.feature_grid(alerts, origin, records)
        if live is None:
            # the map's "now" is the forecast's active_now: the same grid at the origin
            act = pit.load(root, "alerts_active", as_of=seen, since=seen.floor("D"))
            act = act[act["observed_at"] == seen] if len(act) else act
            polled = from_frame(act) if len(act) else []
            states = {r: RegionState(False) for r in regions}
            for i, r in enumerate(grid.regions):
                if grid.n_minutes and grid.active[i, -1]:
                    s = grid.starts[i]
                    k = np.searchsorted(s, grid.n_minutes, "left") - 1
                    states[r] = RegionState(True, grid.t0 + pd.Timedelta(minutes=int(s[k])) if k >= 0 else None)
            for a in polled:
                if a.alert_type == "air_raid" and a.oblast in states:
                    states[a.oblast].reported = True
                    if a.level != "oblast" and a.title:
                        states[a.oblast].locations.append(a.title)
            live = LiveState("alerts.in.ua", seen, states, alerts=polled)
        snap = {"key": key, "origin": origin, "grid": grid, "gap": gap, "live": live,
                "known": max(hist_end, covered) if covered is not None else hist_end}
        with self.lock:
            self._snap = snap
        return snap

    # -------------------------------------------------------------- forecast
    def forecast(self) -> dict:
        if self.s.store_dir is not None:
            return self._store_forecast()
        with self.lock:
            if not self.models:
                raise NotReady(self.message)
        live = self.live_state()
        with self.lock:
            alerts, hist_end = self.alerts, self.hist_end
        if live.source == "alerts.in.ua":
            origin = nowcast.origin_after(live.as_of)
        else:
            origin = hist_end.floor("h")
        ongoing = live.ongoing_alerts()
        ongoing = ongoing[ongoing["start"] < origin] if live.source == "alerts.in.ua" else ongoing.iloc[0:0]
        key = (origin, hist_end, tuple(map(tuple, ongoing.astype(str).to_numpy())), self.recorder.version())
        with self.lock:
            if self._forecast and self._forecast[0] == key:
                return self._forecast[1]
        dataset, hist = alerts, self._history(alerts)
        if not ongoing.empty:
            extra = ongoing.assign(end=origin + pd.Timedelta(hours=1), naive=False)  # end is never used as a feature
            hist = pd.concat([hist, after_dataset(extra, dataset)], ignore_index=True)
        grid, _ = nowcast.feature_grid(hist, origin)
        probs = nowcast.predict(self.models, grid)
        known = self.recorder.known_until(hist_end)
        cs = self.recorder.covered_since
        gap_h = ((cs - hist_end) if cs is not None and cs > hist_end else (origin - known)) / pd.Timedelta(hours=1)
        result = self._compute(grid, origin, hist_end, probs, live, gap_h, self.meta)
        with self.lock:
            self._forecast = (key, result)
        J = self.journal.frame()
        if not len(J) or pd.to_datetime(J["origin"], utc=True).max() <= origin - JOURNAL_EVERY:
            self.journal.record(probs.assign(issued_at=pd.Timestamp.now(tz="UTC"), live=live.source == "alerts.in.ua"))
        return result

    def _store_forecast(self) -> dict:
        """Store mode: the forecast the pipeline issued for the newest origin, as logged."""
        snap = self._store_snapshot()
        origin, grid = snap["origin"], snap["grid"]
        with self.lock:
            hist_end, meta = self.hist_end, self.meta
            if self._forecast and self._forecast[0] == snap["key"]:
                return self._forecast[1]
        F = pit.load(self.s.store_dir, "forecasts", since=origin.floor("D"))
        F = F[pd.to_datetime(F["origin"], utc=True) == origin] if len(F) else F
        if len(F):
            probs = F.sort_values("issued_at").drop_duplicates(["region", "target", "horizon"], keep="last")
            meta = {**meta, "data_end": str(pd.Timestamp(probs["trained_until"].iloc[0]).isoformat())} \
                if "trained_until" in probs else meta
        else:
            with self.lock:
                if not self.models:
                    raise NotReady(self.message)
            probs = nowcast.predict(self.models, grid)
            log.warning("no stored forecast for origin %s: computed it with the loaded models", origin)
        gap_h = (origin - snap["known"]) / pd.Timedelta(hours=1)
        result = self._compute(grid, origin, hist_end, probs, snap["live"], gap_h, meta)
        result["issued_at"] = _iso(pd.to_datetime(probs["issued_at"], utc=True).max()) if "issued_at" in probs else None
        with self.lock:
            self._forecast = (snap["key"], result)
        return result

    def _history(self, alerts: pd.DataFrame) -> pd.DataFrame:
        """Dataset history plus alerts recorded from live polls after it (never before its end)."""
        rec = after_dataset(self.recorder.alerts(), alerts)
        if rec.empty:
            return alerts
        return pd.concat([alerts, rec[rec["region"].isin(self.regions)]], ignore_index=True)

    def _compute(self, grid, origin, hist_end, probs_long: pd.DataFrame, live: LiveState, gap_h: float,
                 meta: dict) -> dict:
        probs = {r: {t: {} for t in TARGETS} for r in grid.regions}
        for r, t, h, p in zip(probs_long["region"], probs_long["target"], probs_long["horizon"], probs_long["p"]):
            if r in probs:
                probs[r][t][int(h)] = round(float(p), 4)
        H = hourly_fraction(grid)

        # Recent history for charts: share of each 60-min block (ending at the origin) under alert,
        # last 72 h, and the 28-day hour-of-day profile.
        n72 = min(72, H.shape[1])
        hours = grid.t0 + pd.to_timedelta(np.arange(H.shape[1] - n72, H.shape[1]), unit="h")
        local_hour = (grid.t0 + pd.to_timedelta(np.arange(H.shape[1]), unit="h")
                      + pd.Timedelta(minutes=30)).tz_convert(KYIV).hour.to_numpy()
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
        warnings = []
        if live.source == "dataset":
            warnings.append("Немає живих даних (ALERTS_IN_UA_TOKEN не задано або API недоступне): "
                            "прогноз зроблено станом на кінець датасету.")
        elif gap_h > 6:
            warnings.append(f"Історія закінчується за {gap_h:.0f} год до моменту прогнозу: "
                            "ознаки за останні години можуть бути занижені.")
        if live.error:
            warnings.append(live.error)
        return {
            "origin": _iso(origin),
            "origin_kyiv": _kyiv(origin),
            "history_end": _iso(hist_end),
            "history_end_kyiv": _kyiv(hist_end),
            "live_source": live.source,
            "horizons": list(HORIZONS),
            "windows": windows,
            "warnings": warnings,
            "model": {k: meta.get(k) for k in ("trained_at", "data_end", "selection_source")},
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

    # ---------------------------------------------------------- verification
    def verification(self, region: str | None = None, recent_hours: int = 24) -> dict:
        """Issued forecasts compared with what happened, for windows that are fully observed.

        Store mode: the pipeline's forecasts and verify.py's outcomes from the
        store (dataset labels preferred over store labels). Server mode: the
        service's own journal, scored with the same minute-based labels.
        """
        with self.lock:
            alerts, hist_end = self.alerts, self.hist_end
        if alerts is None:
            raise NotReady("no data")
        J, known = self._store_verified() if self.s.store_dir is not None else self._verified_journal(alerts, hist_end)
        return site_summary(J, known, region=region, recent=recent_hours)

    def _store_verified(self) -> tuple[pd.DataFrame, pd.Timestamp]:
        root, now = self.s.store_dir, pd.Timestamp.now(tz="UTC")
        since = now - pd.Timedelta(days=VERIFY_DAYS)
        F = pit.load(root, "forecasts", since=since)
        key = (len(F), F["issued_at"].max() if len(F) else None)
        with self.lock:
            if getattr(self, "_verified", None) and self._verified[0] == key:
                return self._verified[1]
        V = pit.load(root, "verification", since=since)
        cols = ["origin", "region", "target", "horizon"]
        known = pd.to_datetime(V["verified_at"], utc=True).max() if len(V) else since
        if len(F):
            F = F.copy()
            F["origin"] = pd.to_datetime(F["origin"], utc=True)
            F = F.sort_values("issued_at").drop_duplicates(cols, keep="last")
            if len(V):
                V = V.copy()
                V["origin"] = pd.to_datetime(V["origin"], utc=True)
                V = V.assign(_d=V["label_source"] == "dataset").sort_values(["_d", "verified_at"])
                V = V.drop_duplicates(cols, keep="last")[cols + ["y", "label_source"]]
                F = F.merge(V, on=cols, how="left")
            else:
                F = F.assign(y=np.nan, label_source=None)
        out = (F[F["origin"] >= since].reset_index(drop=True) if len(F) else F, known)
        with self.lock:
            self._verified = (key, out)
        return out

    def _verified_journal(self, alerts, hist_end) -> tuple[pd.DataFrame, pd.Timestamp]:
        """Journal rows with the observed outcome `y` (NaN while the window is not fully observed). Cached."""
        J = self.journal.frame()
        known = self.recorder.known_until(hist_end)
        key = (len(J), J["origin"].max() if len(J) else None, known.floor("min"), hist_end, self.recorder.version())
        with self.lock:
            if getattr(self, "_verified", None) and self._verified[0] == key:
                return self._verified[1], known
        if J.empty:
            return J.assign(y=np.nan), known
        J = J.copy()
        J["origin"] = pd.to_datetime(J["origin"], utc=True)
        # the record is complete up to the dataset's last hour, or up to the last poll of an unbroken run
        end = known.floor("min") if known > hist_end else hist_end.floor("h")
        hist = self._history(alerts)
        J["y"] = outcomes(J, build_grid(hist[hist["start"] < end], data_end=end))
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
