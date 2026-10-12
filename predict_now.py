"""Live forecast for the next 1-6 hours for every region.

    export ALERTS_IN_UA_TOKEN=...   # optional; never commit it
    python predict_now.py [--store live-data] [--models var/models]   # forecast + forecast log
    python verify.py [--store live-data]                              # score past forecasts

Models: the served bundle in --models (src/airraid/training.py), the same one
the web app and the static site use. The 10-minute pipeline restores it from
the train job's cache; when no compatible bundle exists (first run, modelling
code changed) it is trained once here on the volunteer dataset only, origins
whose label window the dataset fully covers, and saved for the next runs.

Origin: the first whole minute at or after the newest live observation (a store
poll from the last hour, or a fresh alerts.in.ua poll when ALERTS_IN_UA_TOKEN
is set); without one, the last hour of the dataset. Every input was known at
the origin (src/airraid/nowcast.py), so the pipeline forecasts "the next 60
minutes from 14:13" every 10 minutes instead of repeating the 14:00 forecast.

Prediction features (never training rows) can additionally use, all as known
at the forecast origin:
  * the store's alerts.in.ua history pulls and polls, to fill the hours
    between the end of the dataset (updated about once a day) and the origin;
  * a fresh alerts.in.ua poll when ALERTS_IN_UA_TOKEN is set (records that
    started before the origin; their later disappearance is not used).
Live records are turned into oblast-level alerts with the same definition and
12 h cap as the training data (airraid.live_alerts.oblast_intervals) and can
never change anything before the end of the dataset.

Every forecast is appended to reports/forecast_log.csv and, with --store, to the
point-in-time store (`forecasts`) so verify.py can score it.

With --text-root (a text-data checkout) the history + Telegram model
(airraid.text.serving, boosters in <models>/text) serves where it beat the
history-only model and the archive covers the origin; each row says which model
served (`model`) and why (`text_status`). AIRRAID_TEXT_TRAIN=0 (the pipeline)
only loads the text boosters the train job saved.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from airraid import nowcast, store, training  # noqa: E402
from airraid.data import download, load_alerts  # noqa: E402
from airraid.live_alerts import OBLAST_EN as UK_TO_EN  # noqa: E402,F401  (old import name)
from airraid.live_alerts import KEY, LIVE_EXPIRY_HOURS, fetch_active, parse_active, store_records, to_frame  # noqa: E402

ROOT = Path(__file__).resolve().parent
MODELS_DIR = Path(os.environ.get("AIRRAID_MODEL_DIR") or ROOT / "var" / "models")
LOG_PATH = ROOT / "reports" / "forecast_log.csv"


def live_records(token: str) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Records of a fresh active.json poll, and when it was made.

    They were running at the poll; their later disappearance is unknown, so
    finished_at stays NaT (the caller keeps only those that started before the origin).
    """
    seen = pd.Timestamp.now(tz="UTC")
    df = to_frame(parse_active(fetch_active(token)))
    return df.assign(finished_at=pd.NaT), seen


def git_sha() -> str | None:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def grids(alerts: pd.DataFrame, hist_end: pd.Timestamp, origin: pd.Timestamp, records: pd.DataFrame | None):
    """(training grid, feature grid, gap alerts), for tests and inspection.

    Training: the dataset only, up to its last full hour (airraid.training).
    Live records never enter it: between the end of the dataset and the origin
    they only describe part of what happened (alerts that started and ended
    between polls are missing), so labels there would be wrong.
    Features: the dataset plus oblast-level alerts rebuilt from the live records
    known at `origin`, only after the end of the dataset (nowcast.feature_grid).
    """
    from airraid.data import build_grid

    tgrid = build_grid(alerts, data_end=hist_end.floor("h"))
    fgrid, gap = nowcast.feature_grid(alerts, origin, records)
    assert fgrid.regions == tgrid.regions
    return tgrid, fgrid, gap


def get_models(alerts: pd.DataFrame, model_dir: Path | None, threads: int, train_if_missing: bool = True,
               reports_dir: Path = ROOT / "reports"):
    """(models, meta): the saved bundle when compatible, else trained now (and saved when model_dir is set)."""
    regions = sorted(alerts["region"].unique())
    plan, source = training.plans(reports_dir)
    if model_dir is not None and (b := training.load(model_dir, regions, plan)) is not None:
        return b[0], b[1]
    if not train_if_missing:
        raise SystemExit(f"no compatible models in {model_dir}: {training.incompatible(training.read_meta(model_dir), regions, plan)}")
    t = time.time()
    # holdout evaluation is the train job's business; here only the serving models
    models, meta, ho = training.train(alerts, plan, threads=threads, holdout=False, selection_source=source)
    print(f"trained {len(models)} models on data up to {meta['data_end']} in {time.time() - t:.0f}s")
    if model_dir is not None:
        training.save(model_dir, models, meta, ho)
    return models, meta


def text_model(text_root: Path, model_dir: Path | None, alerts: pd.DataFrame, hist_end: pd.Timestamp, grid,
               threads: int, as_of: bool, fetch: bool, reports_dir: Path = ROOT / "reports"):
    """(TextServing, text rows at the origin, status): the history + Telegram model and its
    features at the grid's end, or (None, None, reason) and the history-only model serves.

    Trained like the history model on the dataset grid when allowed and needed; with
    AIRRAID_TEXT_TRAIN=0 only the saved boosters are loaded (the grid is not even built).
    """
    from airraid.data import build_grid
    from airraid.features import hourly_fraction, hourly_starts
    from airraid.text.serving import TextServing

    text = TextServing(text_root, (model_dir or MODELS_DIR) / ("text_replay" if as_of else "text"), reports_dir,
                       threads=threads, fetch=fetch and not as_of)
    tgrid = build_grid(alerts, data_end=hist_end.floor("h")) if text.allow_train else None
    H, S = (hourly_fraction(tgrid), hourly_starts(tgrid)) if tgrid is not None else (None, None)
    if not text.ensure_models(tgrid, H, S, hist_end, 0 if as_of else 24):
        return None, None, f"text model unavailable: {text.status}"
    rows, status = text.rows_now(grid, grid.n_minutes // 60)
    print(f"text signals: {status}")
    return text, rows, status


def append_log(out_long: pd.DataFrame):
    """Append this run's forecasts to the forecast log (one row per region/target/horizon)."""
    LOG_PATH.parent.mkdir(exist_ok=True)
    if LOG_PATH.exists():
        old = pd.read_csv(LOG_PATH, parse_dates=["origin", "issued_at", "trained_until"])
        out_long = pd.concat([old, out_long], ignore_index=True)
    key = ["origin", "region", "target", "horizon"]
    out_long = out_long.drop_duplicates(key, keep="last").sort_values(key)
    out_long.to_csv(LOG_PATH, index=False)


def forecast(alerts: pd.DataFrame, now: pd.Timestamp, store_root: Path | None = None, token: str | None = None,
             model_dir: Path | None = MODELS_DIR, threads: int = 4, as_of: bool = False, train: bool = True,
             reports_dir: Path = ROOT / "reports", complete_until: pd.Timestamp | None = None,
             text_root: Path | None = None, text_fetch: bool = True) -> tuple[pd.DataFrame, dict]:
    """(forecast rows, meta) at the origin defined by the newest live observation before `now`.

    `alerts` is the volunteer dataset (for a replay: truncated at `now`, and
    `complete_until` says up to when the truncated file is complete). With
    `as_of` the origin is `now` itself and the models are trained on `alerts`
    without being saved.
    """
    hist_end = alerts["start"].max() if complete_until is None else complete_until
    # Newest live observation: a fresh poll (token) or the store's last poll.
    fresh, seen = None, None
    last_poll = None
    if store_root is not None:
        polls = store.load(store_root, "polls", as_of=now, since=now - pd.Timedelta(days=1))
        last_poll = polls["observed_at"].max() if len(polls) else None
    if token:
        fresh, seen = live_records(token)
    elif last_poll is not None and last_poll > hist_end and now - last_poll <= pd.Timedelta(hours=LIVE_EXPIRY_HOURS):
        seen = last_poll
    if as_of:
        origin = now
        print(f"replay as of {origin}")
    elif seen is not None:
        origin = nowcast.origin_after(seen)
    else:
        origin = hist_end.floor("h")
        print("No ALERTS_IN_UA_TOKEN and no store poll in the last hour: forecasting as of the end of the dataset")

    records, covered = None, None
    if store_root is not None and origin > hist_end:
        records, covered = store_records(store_root, origin, hist_end)
    if fresh is not None:
        fresh = fresh[fresh["started_at"] < origin]
        records = fresh if records is None else (
            pd.concat([records, fresh], ignore_index=True)  # a known end beats "still running"
            .sort_values("finished_at", na_position="last").drop_duplicates(KEY))
        print(f"alerts.in.ua: {len(fresh)} active records that started before {origin:%H:%M} UTC")
    known_until = max(hist_end, covered) if covered is not None else hist_end
    if origin - known_until > pd.Timedelta(hours=6):
        print(f"WARNING: alert record complete only up to {known_until}, {origin - known_until} before the "
              "forecast origin; recent-history features will be understated")

    # Training: the dataset only (saved bundle). Features: dataset + live records known at the origin.
    models, meta = get_models(alerts, None if as_of else model_dir, threads, train_if_missing=train,
                              reports_dir=reports_dir)
    trained_until = pd.Timestamp(meta["data_end"])
    if trained_until > origin:
        raise SystemExit(f"models were trained on data up to {trained_until}, after the origin {origin}: "
                         "refusing to forecast with them (retrain on the current dataset)")
    grid, gap = nowcast.feature_grid(alerts, origin, records)
    if len(gap):
        print(f"gap after the dataset: {len(gap)} oblast-level alerts rebuilt from live records")
    print(f"models trained on data up to {trained_until} ({meta.get('trained_at')})")

    text, text_rows, text_status = None, None, "off (no --text-root)"
    if text_root is not None:
        text, text_rows, text_status = text_model(text_root, None if as_of else model_dir, alerts, hist_end, grid,
                                                  threads, as_of, text_fetch, reports_dir)
    log = nowcast.predict(models, grid, text, text_rows)
    served = sorted(set(log["model"]))
    issued_at = pd.Timestamp.now(tz="UTC").floor("s")
    log = log.assign(issued_at=issued_at, live=bool(token) or len(gap) > 0 or seen is not None,
                     trained_until=trained_until, hist_end=hist_end, git_sha=git_sha(), text_status=text_status)
    if store_root is not None and not as_of:
        store.append(store_root, "forecasts", log)
    local = origin.tz_convert("Europe/Kyiv")
    return log, {
        "origin_kyiv": f"{local:%Y-%m-%d %H:%M}",
        "origin_utc": f"{origin:%Y-%m-%d %H:%M}",
        "issued_at_utc": f"{issued_at:%Y-%m-%d %H:%M:%S}",
        "last_poll_utc": None if seen is None else f"{seen:%Y-%m-%d %H:%M:%S}",
        "history_ends_utc": f"{hist_end:%Y-%m-%d %H:%M}",
        "record_complete_until_utc": f"{known_until:%Y-%m-%d %H:%M}",
        "gap_alerts": int(len(gap)),
        "models_trained_until_utc": f"{trained_until:%Y-%m-%d %H:%M}",
        "models_trained_at_utc": meta.get("trained_at"),
        "live_api": bool(token),
        "model_served": ",".join(served),
        "text_status": text_status,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help="replay: forecast as if it were this UTC minute (data after it is "
                                    "dropped, store rows are used as known then, models are trained on the "
                                    "truncated dataset and not saved); e.g. 2026-10-10T12:20")
    ap.add_argument("--store", type=Path, default=os.environ.get("AIRRAID_STORE") or None,
                    help="point-in-time store (live-data branch checkout): fills the gap after the dataset "
                         "and receives the forecasts")
    ap.add_argument("--models", type=Path, default=MODELS_DIR, help="served model bundle (default %(default)s)")
    ap.add_argument("--no-train", action="store_true", help="fail instead of training when no compatible models")
    ap.add_argument("--no-download", action="store_true", default=os.environ.get("AIRRAID_DOWNLOAD") == "0",
                    help="use the local dataset copy (the pipeline downloads it once per run)")
    ap.add_argument("--threads", type=int, default=int(os.environ.get("AIRRAID_LGB_THREADS", 4)))
    ap.add_argument("--text-root", type=Path, default=os.environ.get("AIRRAID_TEXT_ROOT") or None,
                    help="text archive (text-data branch checkout): serve the history + Telegram model "
                         "when the archive covers the origin, else the history-only model")
    ap.add_argument("--text-no-fetch", action="store_true",
                    help="do not fetch new posts first (the pipeline ran `airraid.text.collect update` just before)")
    args = ap.parse_args()

    if not args.no_download:
        download()
    alerts = load_alerts()
    now = pd.Timestamp.now(tz="UTC")
    token = os.environ.get("ALERTS_IN_UA_TOKEN")
    complete = None
    if args.as_of:
        now = pd.Timestamp(args.as_of, tz="UTC").floor("min")
        # everything after the origin is unknown; before the dataset end the file is taken as
        # complete up to the origin, after it the gap is filled from the store as known then
        complete = min(now, alerts["start"].max())
        alerts, token = alerts[alerts["start"] < now], None
    log, meta = forecast(alerts, now, args.store, token, args.models, args.threads, as_of=bool(args.as_of),
                         train=not args.no_train, complete_until=complete, text_root=args.text_root,
                         text_fetch=not args.text_no_fetch)
    append_log(log)
    out = nowcast.wide(log)
    print(f"\nForecast for the next hours from {meta['origin_kyiv']} Kyiv time by {meta['model_served']} "
          f"(text: {meta['text_status']})")
    print("active_Nh: P(alert active during hour N); onset_Nh: P(new alert starts within N hours)\n")
    print(out.sort_values("active_1h", ascending=False).to_string(index=False))
    (ROOT / "reports").mkdir(exist_ok=True)
    out.to_csv(ROOT / "reports" / "forecast_now.csv", index=False)
    (ROOT / "reports" / "forecast_meta.json").write_text(json.dumps(meta))


if __name__ == "__main__":
    main()
