"""Raion (district) layer of the web app: live state per raion and a 1-6 h forecast per raion.

Live state
----------
alerts.in.ua reports alerts at oblast, raion, hromada and city level. A raion
is "under alert" when an air-raid alert covers the whole oblast, the raion
itself, or one of its hromadas/cities (then only part of it: `level` says
which, `locations` lists the hromadas). This is display only; the oblast model
keeps its own, training-consistent definition.

Forecast
--------
`raion_run.py` evaluates, on the latest 4 weeks with raion-level history, the
oblast forecast as is, the oblast forecast times the raion's share of the
oblast's events (`oblast_x_share`), a raion LightGBM that only needs the
current raion state (`lightgbm_state`) and a raion LightGBM that also needs 28
days of raion history (`lightgbm`). What can be used depends on the data at
forecast time, so the validation month picks the best method for three tiers
(reports/raions/summary.json):
  * `history`: raion history (live store) covers the 29 days before the forecast;
  * `live_state`: an alerts.in.ua poll says which raions are under alert now;
  * `none`: neither; only the oblast forecast and a (stale) raion share.
A method that cannot run (model not trained, no share) falls back to the next
candidate of its tier and finally to `oblast`. Every value is labelled with the
method that produced it. Kyiv City is a single unit: its raion forecast is the
oblast forecast.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from airraid.data import MAX_ALERT_HOURS
from airraid.features import HORIZONS, add_horizon_features, labels, origin_hours
from airraid.models import GBM, feature_columns
from airraid.raion_data import (
    FORECAST_RAIONS, RaionHistory, alerts_as_of, download_official, load_history,
)
from airraid.raion_features import SHARE_WINDOW_H, STATE_COLS, build_raion_features, make_inputs, oblast_labels
from airraid.raions import BY_ID, NO_DATA_OBLASTS, RAIONS, forecastable, from_alerts_in_ua, oblast_raions

from .regions import ALERTS_IN_UA, slug, uk_name

log = logging.getLogger("airraid_app")

TARGETS = ("active", "onset")
METHOD_UK = {
    "lightgbm": "окрема модель району: районна історія за 28 днів, стан районів зараз, стан області",
    "lightgbm_state": "окрема модель району: які райони під тривогою зараз і скільки триває, стан області та сусідів",
    "oblast_x_share": "похідний від прогнозу області: ймовірність для області × частка тривог області, що зачіпали цей район",
    "oblast": "прогноз для всієї області: районних даних для цього району немає",
}
# Candidates per tier in fallback order (raion_run.TIERS); the stale-share variant is served as oblast_x_share.
TIERS = {
    "history": ["lightgbm", "lightgbm_state", "oblast_x_share", "oblast"],
    "live_state": ["lightgbm_state", "oblast_x_share", "oblast"],
    "none": ["oblast_x_share", "oblast"],
}
MODEL_KINDS = ("lightgbm_state", "lightgbm")


@dataclass
class RaionState:
    active: bool = False
    since: pd.Timestamp | None = None
    level: str | None = None  # widest level that covers the raion: oblast > raion > hromada
    locations: list[str] = field(default_factory=list)  # hromadas / cities under alert


_RANK = {"hromada": 0, "city": 0, "raion": 1, "oblast": 2}


def _get(a, key, attr=None):
    return a.get(key) if isinstance(a, dict) else getattr(a, attr or key, None)


def raion_state(alerts) -> tuple[dict[str, RaionState], list[str]]:
    """Live alerts.in.ua records -> state of every forecastable raion, plus unmatched location titles.

    `alerts` is the raw `alerts` list of /v1/alerts/active.json (dicts) or
    parsed records with attributes uid, oblast (dataset name), title, level,
    raion, started_at, alert_type.
    """
    states = {rid: RaionState() for rid in FORECAST_RAIONS}
    unmatched = []
    for a in alerts:
        if _get(a, "alert_type") != "air_raid":
            continue
        start = _get(a, "started_at")
        if not start:
            continue
        start = pd.Timestamp(start)
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
        if isinstance(a, dict):
            title, level, uid = a.get("location_title") or "", a.get("location_type"), a.get("location_uid")
            raion_title = a.get("location_raion")
            oblast = ALERTS_IN_UA.get(a.get("location_oblast") or "") or ALERTS_IN_UA.get(title)
        else:
            title, level, uid, raion_title, oblast = a.title or "", a.level, a.uid, a.raion, a.oblast
        if oblast in NO_DATA_OBLASTS:
            continue
        if oblast == "Kyiv City":
            covered, level = ["kyiv-city"], "oblast"
        elif level == "oblast":
            covered = [r.id for r in oblast_raions(oblast or "") if forecastable(r)]
        else:
            r = from_alerts_in_ua(uid, title, raion_title)
            if r is None or not forecastable(r):
                unmatched.append(title)
                continue
            covered = [r.id]
            level = "raion" if level == "raion" else "hromada"
        for rid in covered:
            st = states[rid]
            st.active = True
            st.since = start if st.since is None else min(st.since, start)
            if st.level is None or _RANK.get(level, 0) > _RANK.get(st.level, 0):
                st.level = level
            if level == "hromada" and title and title not in st.locations:
                st.locations.append(title)
    return states, unmatched


def _iso(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).tz_convert("UTC").isoformat()


class RaionForecaster:
    """Raion history, shares and (optionally) direct raion models for the engine."""

    def __init__(self, settings):
        self.s = settings
        self.history: RaionHistory | None = None
        self.shares: dict[tuple[str, int], dict[str, float]] = {}
        self.share_period: tuple[pd.Timestamp, pd.Timestamp] | None = None
        self.models: dict[tuple[str, int], GBM] = {}
        self.model_info: dict = {}
        self.selection: dict = {}
        self.evaluation: dict = {}

    # --------------------------------------------------------------- data
    def load(self, refresh: bool) -> None:
        path = self.s.official_path
        # the official file stopped updating on 2026-09-07: refresh it at most once a day (31 MB)
        stale = not path.exists() or time.time() - path.stat().st_mtime > 24 * 3600
        if refresh and stale:
            try:
                download_official(path)
            except Exception as e:  # keep the previous copy
                log.warning("official raion file download failed: %s", e)
        try:
            self.history = load_history(path, self.s.store_dir, download=False)
        except Exception as e:  # noqa: BLE001 - the oblast app must keep working without raions
            log.warning("raion history unavailable: %s", e)
            self.history = None
        self.selection, self.evaluation = self._read_reports()
        if self.history is not None:
            log.info("raion history: %d records, covered until %s", len(self.history.alerts), self.history.known_until())

    def _read_reports(self) -> tuple[dict, dict]:
        d = self.s.reports_dir / "raions"
        sel, ev = {}, {}
        try:
            sel = json.loads((d / "summary.json").read_text()).get("selection", {})
        except (OSError, ValueError):
            pass
        try:
            M = pd.read_csv(d / "metrics_test.csv")
            split = json.loads((d / "summary.json").read_text()).get("split", {})
            ev = {"test": split.get("test"), "scores": M.replace({np.nan: None}).to_dict("records")}
            if (d / "by_oblast.csv").exists():
                ev["by_oblast"] = pd.read_csv(d / "by_oblast.csv").replace({np.nan: None}).to_dict("records")
        except (OSError, ValueError):
            pass
        return sel, ev

    def evaluation_for(self, oblast: str) -> dict | None:
        """Test-period quality of the raion methods (raion_run.py), pooled and for this oblast."""
        ev = self.evaluation
        if not ev:
            return None
        keep = ("target", "horizon_h", "model", "brier", "bss_vs_clim", "roc_auc", "base_rate")
        return {
            "test": ev.get("test"),
            "scores": [{k: r.get(k) for k in keep} for r in ev.get("scores", [])],
            "oblast": [r for r in ev.get("by_oblast", []) if r.get("oblast") == oblast],
        }

    def chosen(self, t: str, h: int, tier: str) -> list[str]:
        """Methods to try for (target, horizon) in this tier: the validated choice first, then fallbacks."""
        c = self.selection.get(f"{t}_h{h}", {}).get("chosen", {})
        first = c.get(tier) if isinstance(c, dict) else None
        first = "oblast_x_share" if first == "oblast_x_share_stale30" else first
        order = TIERS[tier]
        return ([first] if first in order else []) + [m for m in order if m != first]

    @staticmethod
    def _regions(vol_alerts: pd.DataFrame) -> list[str]:
        """Raions of the oblasts the oblast history covers (others have no oblast features)."""
        oblasts = set(vol_alerts["region"].unique())
        return [r for r in FORECAST_RAIONS if BY_ID[r].oblast in oblasts]

    def _plan(self, kind: str, t: str, h: int) -> tuple[int, int]:
        """Training window (months, capped like the oblast models) and trees chosen by raion_run.py."""
        p = self.selection.get(f"{t}_h{h}", {}).get(kind, {})
        return min(int(p.get("best_months") or 6), self.s.max_train_months), int(p.get("best_trees") or 200)

    def _wanted(self, kind: str) -> bool:
        return any(m == kind for t in TARGETS for h in HORIZONS for tier in TIERS
                   for m in self.chosen(t, h, tier)[:1])

    # ------------------------------------------------------------ prepare
    def prepare(self, vol_alerts: pd.DataFrame, now: pd.Timestamp, lgb_threads: int = 2) -> None:
        """Shares from the latest covered 28 days; raion models trained on the raion history.

        `vol_alerts` is the volunteer dataset only (no live records: training
        never sees live data). Everything learnt here ends where both the raion
        history and the dataset end, so no feature reads "no alerts" for hours
        the dataset does not cover yet. The current-state model only needs a
        covered past to learn from (the official file suffices); the full model
        is trained only when the raion history reaches `now`, the moment
        forecasts will be made for, since it needs the 28 days before it.
        """
        self.shares, self.share_period, self.models, self.model_info = {}, None, {}, {}
        hist = self.history
        if hist is None or hist.alerts.empty or vol_alerts.empty:
            return
        until = min(now, vol_alerts["start"].max()).floor("h")
        share_end = hist.latest_window(pd.Timedelta(hours=SHARE_WINDOW_H), until)
        if share_end is None:
            log.warning("raion history has no covered 28 days before %s: no shares", until)
        else:
            self._prepare_shares(vol_alerts, share_end.floor("h"))
        train_end = hist.latest_window(pd.Timedelta(days=14), until)
        for kind in MODEL_KINDS if self.s.raion_models and train_end is not None else ():
            if not self._wanted(kind):
                continue
            if kind == "lightgbm" and not hist.covered(now - pd.Timedelta(days=29), now - pd.Timedelta(hours=1)):
                log.info("full raion model not trained: raion history does not reach %s", now)
                continue
            try:
                self._train(kind, vol_alerts, train_end.floor("h"), lgb_threads)
            except Exception:  # noqa: BLE001 - fall back to the other methods
                log.exception("training %s raion models failed", kind)

    def _prepare_shares(self, vol_alerts: pd.DataFrame, end: pd.Timestamp) -> None:
        start = end - pd.Timedelta(hours=SHARE_WINDOW_H)
        hist = self.history
        ra = hist.alerts[hist.alerts["end"] > start - pd.Timedelta(days=2)]
        va = vol_alerts[vol_alerts["start"] >= start - pd.Timedelta(days=3)]
        inp = make_inputs(alerts_as_of(ra, end), alerts_as_of(va, end), end, regions=self._regions(vol_alerts),
                          t0=(start - pd.Timedelta(days=2)).floor("D"))
        g = inp.grid
        k0, k1 = g.minute(start) // 60, g.n_minutes // 60
        for h in HORIZONS:
            K = np.arange(k0, k1 - h + 1)
            for t in TARGETS:
                y = labels(g, K, h, inp.H, t, inp.S).reshape(len(g.regions), len(K))
                yo = oblast_labels(inp, K, h, t)
                num, den = np.nansum(y, axis=1), np.nansum(yo, axis=1)
                self.shares[(t, h)] = {r: float(min(n / d, 1.0)) if d > 0 else 1.0
                                       for r, n, d in zip(g.regions, num, den)}
        self.share_period = (start, end)

    def _train(self, kind: str, vol_alerts: pd.DataFrame, end: pd.Timestamp, threads: int) -> None:
        hist = self.history
        months = max(self._plan(kind, t, h)[0] for t in TARGETS for h in HORIZONS)
        first = (end - pd.DateOffset(months=months)).floor("D")
        t0 = first - pd.Timedelta(days=29)
        ra = hist.alerts[hist.alerts["end"] > t0]
        va = vol_alerts[vol_alerts["start"] >= t0 - pd.Timedelta(days=10)]
        inp = make_inputs(alerts_as_of(ra, end), alerts_as_of(va, end), end, regions=self._regions(vol_alerts), t0=t0)
        g = inp.grid
        K = origin_hours(g, first, end)
        ts = g.t0 + pd.to_timedelta(K, unit="h")
        # Origins whose feature or label window touches a gap in the raion history are unusable.
        back = pd.Timedelta(days=28) if kind == "lightgbm" else pd.Timedelta(hours=MAX_ALERT_HOURS + 1)
        ok = np.array([hist.covered(x - back, x + pd.Timedelta(hours=max(HORIZONS))) for x in ts])
        if ok.sum() < 24 * 14:
            log.info("%s raion models not trained: only %d covered origins", kind, ok.sum())
            return
        base = build_raion_features(inp, K)
        okr = np.tile(ok, len(g.regions))
        origin = base["origin_hour"].to_numpy()
        for h in HORIZONS:
            df = add_horizon_features(base, g, inp.H, h, inp.S)
            view = df if kind == "lightgbm" else df[["origin_hour", "region", "horizon"] + STATE_COLS]
            for t in TARGETS:
                # the full model only where it is the validated choice; the state model everywhere (fallback)
                if kind == "lightgbm" and self.chosen(t, h, "history")[0] != "lightgbm":
                    continue
                m_, trees = self._plan(kind, t, h)
                y = labels(g, K, h, inp.H, t, inp.S)
                lo = g.minute(end - pd.DateOffset(months=m_)) // 60
                m = okr & ~np.isnan(y) & (origin >= lo)
                self.models[(kind, t, h)] = GBM(n_estimators=trees, params={"num_threads": threads}).fit(view[m], y[m])
        self.model_info[kind] = {"trained_at": _iso(pd.Timestamp.now(tz="UTC")), "data_end": _iso(end)}
        log.info("trained %s raion models on raion history to %s", kind, end)

    # ----------------------------------------------------------- forecast
    def _model_probs(self, kind: str, vol_alerts, origin, live: dict[str, RaionState] | None) -> dict:
        """Raion-model probabilities at `origin`: {raion: {target: {h: p}}} (empty when not applicable)."""
        hist = self.history
        if not any(k[0] == kind for k in self.models):
            return {}
        # 31 days even for the state model: the shared feature builder indexes up to 28 days back
        # (its history columns are dropped for that model, and here hold no alerts).
        t0 = (origin - pd.Timedelta(days=31)).floor("D")
        if kind == "lightgbm":
            if hist is None or not hist.covered(origin - pd.Timedelta(days=29), origin - pd.Timedelta(hours=1)):
                return {}
            ra = alerts_as_of(hist.alerts[hist.alerts["end"] > t0], origin)
        else:
            if live is None:
                return {}
            ra = None
        if live:
            # Alerts running at the origin, under the same 12 h cap as the training history:
            # an alert that started earlier counts as over. Their end is never used as a feature.
            cap = pd.Timedelta(hours=MAX_ALERT_HOURS)
            rows = [(r, s.since) for r, s in live.items() if s.active and s.since is not None and origin - cap < s.since < origin]
            now_rows = pd.DataFrame({"region": [r for r, _ in rows],
                                     "start": pd.to_datetime([t for _, t in rows], utc=True),
                                     "end": origin + pd.Timedelta(hours=1)})
            ra = now_rows if ra is None else pd.concat([ra[["region", "start", "end"]], now_rows], ignore_index=True)
        va = vol_alerts[vol_alerts["start"] >= t0 - pd.Timedelta(days=10)]
        inp = make_inputs(ra, alerts_as_of(va, origin), origin, regions=self._regions(vol_alerts), t0=t0)
        k = inp.grid.n_minutes // 60
        base = build_raion_features(inp, np.array([k]))
        out = {}
        for (kd, t, h), model in self.models.items():
            if kd != kind:
                continue
            x = add_horizon_features(base, inp.grid, inp.H, h, inp.S)
            x = x[x["region"].isin(model.regions)]
            if kind != "lightgbm":
                x = x[["origin_hour", "region", "horizon"] + STATE_COLS]
            if sorted(model.model.feature_name()) != sorted(feature_columns(x) + ["region"]):
                raise RuntimeError("raion feature set changed since the models were trained")
            for r, p in zip(x["region"], model.predict(x)):
                out.setdefault(r, {}).setdefault(t, {})[h] = float(p)
        return out

    def tier(self, origin: pd.Timestamp, live) -> str:
        hist = self.history
        if any(k[0] == "lightgbm" for k in self.models) and hist is not None \
                and hist.covered(origin - pd.Timedelta(days=29), origin - pd.Timedelta(hours=1)):
            return "history"
        return "live_state" if live is not None else "none"

    def forecast(self, origin: pd.Timestamp, oblast_probs: dict[str, dict], live: dict[str, RaionState] | None,
                 live_as_of: pd.Timestamp | None, vol_alerts: pd.DataFrame, unmatched: list[str] | None = None) -> dict:
        """Forecast and live state for every forecastable raion."""
        tier = self.tier(origin, live)
        direct = {}
        for kind in MODEL_KINDS:
            try:
                direct[kind] = self._model_probs(kind, vol_alerts, origin, live)
            except Exception as e:  # noqa: BLE001 - fall back to the oblast-derived forecast
                log.warning("%s raion forecast failed: %s", kind, e)
                direct[kind] = {}
        rows = []
        for r in RAIONS:
            if not forecastable(r):
                continue
            ob = oblast_probs.get(r.oblast)
            probs, method = {t: {} for t in TARGETS}, {t: {} for t in TARGETS}
            for t in TARGETS:
                for h in HORIZONS:
                    po = None if ob is None else ob[t].get(h)
                    p, m = po, "oblast" if po is not None else None
                    if r.id != "kyiv-city" and po is not None:
                        for cand in self.chosen(t, h, tier):
                            if cand in MODEL_KINDS and h in direct[cand].get(r.id, {}).get(t, {}):
                                p, m = direct[cand][r.id][t][h], cand
                            elif cand == "oblast_x_share" and r.id in self.shares.get((t, h), {}):
                                p, m = po * self.shares[(t, h)][r.id], cand
                            elif cand == "oblast":
                                p, m = po, cand
                            else:
                                continue
                            break
                    probs[t][h] = None if p is None else round(float(p), 4)
                    method[t][h] = m
            st = (live or {}).get(r.id)
            rows.append({
                "id": r.id, "name_uk": r.name_uk, "name": r.name, "oblast": r.oblast, "oblast_id": slug(r.oblast),
                "oblast_uk": uk_name(r.oblast),
                "live": None if st is None else {"active": st.active, "since": _iso(st.since), "level": st.level,
                                                 "locations": st.locations},
                "probs": probs, "method": method,
                "share": {f"{t}_h{h}": round(self.shares[(t, h)][r.id], 4)
                          for t in TARGETS for h in HORIZONS if r.id in self.shares.get((t, h), {})},
            })
        sp = self.share_period
        return {
            "origin": _iso(origin),
            "tier": tier,
            "live_source": "alerts.in.ua" if live is not None else None,
            "live_as_of": _iso(live_as_of),
            "n_active": sum(1 for x in rows if x["live"] and x["live"]["active"]),
            "unmatched_locations": sorted(set(unmatched or [])),
            "history_until": _iso(self.history.known_until()) if self.history else None,
            "share_period": [_iso(sp[0]), _iso(sp[1])] if sp else None,
            "models": self.model_info or None,
            "methods": METHOD_UK,
            "no_data": [{"id": r.id, "name_uk": r.name_uk, "oblast": r.oblast} for r in RAIONS if not forecastable(r)],
            "raions": rows,
        }
