"""Glue for .github/workflows/pipeline.yml and train.yml (standard library only).

    python3 scripts/pipeline.py models-key                 # cache key prefix of the served model bundle
    python3 scripts/pipeline.py models-info --models DIR   # fresh=true if trained in this job; cache key suffix
    python3 scripts/pipeline.py gate                       # skip a run that comes right after another one
    python3 scripts/pipeline.py timed NAME -- CMD ...      # run CMD, record its wall time as step NAME
    python3 scripts/pipeline.py status --site site --store live-data   # api/status.json + _state/pipeline.json
    python3 scripts/pipeline.py retrain-check --models var/models      # dispatch train.yml when models are stale
    python3 scripts/pipeline.py history-check                          # dispatch collect.yml when the history pull is due
    python3 scripts/pipeline.py daily-check --workflow threat-labels.yml   # dispatch a daily job its cron did not run
    python3 scripts/pipeline.py keepalive                  # dead man's switch for the 10-minute schedule
    python3 scripts/pipeline.py wait-origin --store live-data   # until the forecast origin minute has begun

GitHub API calls use GITHUB_TOKEN (never printed). Decisions are pure functions
(`gate_decision`, `next_slot`, `keepalive_decision`, `retrain_reason`, `dispatch_reason`) with
tests in tests/test_pipeline_glue.py.

Why a keep-alive: GitHub's `schedule` is best effort. In this repository the
hourly forecast cron fired twice in 14 hours and the 3-hourly Pages cron 48
minutes late. The keep-alive is a bounded dead man's switch, not a loop: after
every run (also one the gate skipped) it waits until the next 10-minute slot
plus a grace period and dispatches the workflow only if nothing else (cron, an
external pinger, a person) has started a newer run by then. Chains of keep-alive runs stop after
PIPELINE_KEEPALIVE_MAX_HOPS hops (default 72, about 12 hours) and restart only
from a real trigger; the repository variable PIPELINE_KEEPALIVE=off disables it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
MODEL_FILES = ("src/airraid/data.py", "src/airraid/features.py", "src/airraid/models.py",
               "src/airraid/training.py", "reports/run_summary.json")
# The raion models (<models>/raions) and the history + Telegram models (<models>/text) are in
# the same cached bundle, so their code and selections are part of its key (hashed when present).
OPTIONAL_MODEL_FILES = ("src/airraid/raions.py", "src/airraid/raion_data.py", "src/airraid/raion_features.py",
                        "src/airraid_app/raion_engine.py", "reports/raions/summary.json",
                        "src/airraid/text/features.py", "src/airraid/text/serving.py",
                        "reports/text_selection.json")
STATUS_PATH = "_state/pipeline.json"  # in the live-data store
SLOT_MIN, SLOT_OFFSET = 10, 3  # cron "3-59/10 * * * *": :03, :13, ... (off the busy top of the hour)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def output(**kv) -> None:
    """Step outputs (GITHUB_OUTPUT) and a log line."""
    path = os.environ.get("GITHUB_OUTPUT")
    for k, v in kv.items():
        print(f"{k}={v}")
        if path:
            with open(path, "a") as f:
                f.write(f"{k}={v}\n")


def notice(msg: str) -> None:
    print(f"::notice::{msg}")


# ------------------------------------------------------------------ GitHub API
def gh(method: str, path: str, body: dict | None = None, raw: bool = False):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    req = urllib.request.Request(f"{API}{path}", method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.raw+json" if raw else "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    if raw:
        return data
    return json.loads(data) if data else None


def repo() -> str:
    return os.environ["GITHUB_REPOSITORY"]


def runs(workflow: str, branch: str, per_page: int = 30, **params) -> list[dict]:
    q = "&".join(f"{k}={v}" for k, v in {"branch": branch, "per_page": per_page, **params}.items())
    return gh("GET", f"/repos/{repo()}/actions/workflows/{workflow}/runs?{q}")["workflow_runs"]


def dispatch(workflow: str, ref: str, inputs: dict | None = None) -> None:
    gh("POST", f"/repos/{repo()}/actions/workflows/{workflow}/dispatches", {"ref": ref, "inputs": inputs or {}})


# ------------------------------------------------------------------ models key
def models_key(root: Path = ROOT) -> str:
    h = hashlib.sha1()
    for f in MODEL_FILES + tuple(f for f in OPTIONAL_MODEL_FILES if (root / f).exists()):
        h.update(f.encode() + b"\0" + (root / f).read_bytes())
    return f"models-v2-{h.hexdigest()[:12]}"


def cmd_models_info(a) -> None:
    """fresh=true when the bundle was trained during this job (pipeline fallback), and a
    cache key suffix unique to the bundle (data end + training time)."""
    meta = _read_json(Path(a.models) / "meta.json")
    trained = parse_ts(meta.get("trained_at"))
    t0 = os.environ.get("JOB_T0")
    fresh = bool(trained and t0 and trained.timestamp() >= float(t0))
    stamp = lambda s: "".join(ch for ch in (s or "")[:16] if ch.isdigit())  # noqa: E731
    print(f"models: trained at {meta.get('trained_at')} on data up to {meta.get('data_end')}")
    output(fresh=str(fresh).lower(), suffix=f"{stamp(meta.get('data_end'))}-{stamp(meta.get('trained_at'))}")


# ------------------------------------------------------------------------ gate
def gate_decision(event: str, source: str, last_update: datetime | None, now: datetime,
                  min_gap: timedelta = timedelta(minutes=5)) -> tuple[bool, str]:
    """Run or skip. Manual, push and keep-alive runs always run (a keep-alive dispatch
    happens only when nothing else started a run for the slot); scheduled and pinger
    runs are skipped when another run finished less than `min_gap` ago (two triggers
    for the same slot). A skipped run still carries the keep-alive."""
    if event == "push" or (event == "workflow_dispatch" and source in ("", "manual", "keepalive")):
        return True, f"{event} ({source or 'manual'}): always runs"
    if last_update is None:
        return True, "no previous run recorded"
    age = now - last_update
    if age < min_gap:
        return False, f"last update {age.total_seconds() / 60:.1f} min ago (< {min_gap.total_seconds() / 60:.0f}): duplicate trigger"
    return True, f"last update {age.total_seconds() / 60:.1f} min ago"


def cmd_gate(a) -> None:
    last = None
    try:
        st = json.loads(gh("GET", f"/repos/{repo()}/contents/{STATUS_PATH}?ref={a.store_branch}", raw=True))
        last = parse_ts(st.get("updated_at"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"could not read {STATUS_PATH}: HTTP {e.code}")
    except Exception as e:  # never block the pipeline on the gate itself
        print(f"could not read {STATUS_PATH}: {type(e).__name__}")
    go, why = gate_decision(os.environ.get("GITHUB_EVENT_NAME", ""), os.environ.get("SOURCE", ""), last, utcnow(),
                            timedelta(minutes=a.min_gap))
    (print if go else notice)(f"gate: {'run' if go else 'skip'} ({why})")
    output(go=str(go).lower())


# ---------------------------------------------------------------------- timing
def _timings_path() -> Path:
    return Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "pipeline-timings.json"


def record(name: str, seconds: float, ok: bool) -> None:
    p = _timings_path()
    t = json.loads(p.read_text()) if p.exists() else {"steps": []}
    t0 = os.environ.get("JOB_T0")
    if not t["steps"] and t0:  # everything before the first timed step: checkout, Python, packages, caches
        first_start = time.time() - seconds
        t["steps"].append({"name": "setup", "seconds": round(first_start - float(t0), 1), "ok": True})
    t["steps"].append({"name": name, "seconds": round(seconds, 1), "ok": ok})
    p.write_text(json.dumps(t))


def cmd_timed(a) -> None:
    cmd = a.argv[1:] if a.argv and a.argv[0] == "--" else a.argv
    t = time.time()
    rc = subprocess.call(cmd)
    dt = time.time() - t
    record(a.name, dt, rc == 0)
    print(f"[timing] {a.name}: {dt:.1f}s{'' if rc == 0 else f' (exit {rc})'}")
    sys.exit(rc)


# ---------------------------------------------------------------------- status
def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def raion_summary(raions: dict) -> dict | None:
    """Which raion method served (api/raions.json): where the models came from, the data
    tier, and how many raion x target x horizon values each method produced."""
    if not raions:
        return None
    counts: dict[str, int] = {}
    for r in raions.get("raions", []):
        for by_h in (r.get("method") or {}).values():
            for m in by_h.values():
                counts[str(m)] = counts.get(str(m), 0) + 1
    return {"models_source": raions.get("models_source"), "tier": raions.get("tier"),
            "n_raions": len(raions.get("raions", [])), "n_active": raions.get("n_active"),
            "methods": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}


def build_status(now: datetime, timings: dict, forecast: dict, models: dict, verification: dict,
                 threats: dict, env: dict, raions: dict | None = None) -> dict:
    steps = {s["name"]: s["seconds"] for s in timings.get("steps", [])}
    failed = [s["name"] for s in timings.get("steps", []) if not s.get("ok", True)]
    t0 = env.get("JOB_T0")
    windows = {}
    for g in verification.get("groups", []):
        if g["window"] in ("24h", "7d") and (g["target"], g["horizon_h"]) in (("active", 1), ("onset", 3)):
            windows.setdefault(g["window"], []).append({k: g.get(k) for k in (
                "target", "horizon_h", "n", "n_origins", "brier_model", "brier_clim", "skill_vs_clim",
                "hit_rate_at_0.5")})

    def utc(s):  # "2026-10-11 20:13" -> ISO with zone
        return None if not s else s.replace(" ", "T") + ("" if "+" in s else "Z")

    run_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env.get('GITHUB_RUN_ID', '')}"
    return {
        "updated_at": now.isoformat(timespec="seconds"),
        "source": "pipeline",
        "origin": utc(forecast.get("origin_utc")),
        "issued_at": utc(forecast.get("issued_at_utc")),
        "last_poll": utc(forecast.get("last_poll_utc")),
        "dataset_end": utc(forecast.get("history_ends_utc")),
        "record_complete_until": utc(forecast.get("record_complete_until_utc")),
        "model": {"trained_until": models.get("data_end"), "trained_at": models.get("trained_at"),
                  "holdout": models.get("holdout_start") is not None, "code_version": models.get("code_version"),
                  # [hook: text signals] which model served this run's rows, and why (predict_now meta)
                  "served": forecast.get("model_served"), "text_status": forecast.get("text_status")},
        "raions": raion_summary(raions or {}),
        "threat_type_origin": utc(threats.get("origin_utc")),
        "verification": {"n_verified": verification.get("n_verified"), "pending": verification.get("pending"),
                         "by_label_source": verification.get("n_verified_by_label_source"),
                         "windows": windows},
        # learning from verified forecasts (airraid.recalibration): fitted by verify, applied by the next forecast
        "calibration": {"n_active": (verification.get("calibration") or {}).get("n_active"),
                        "fitted_at": (verification.get("calibration") or {}).get("fitted_at"),
                        "applied_to_this_forecast": forecast.get("calibration_active")},
        "steps_s": steps,
        "failed_steps": failed,
        "elapsed_s": round(now.timestamp() - float(t0), 1) if t0 else None,
        "run": {"id": env.get("GITHUB_RUN_ID"), "url": run_url if env.get("GITHUB_RUN_ID") else None,
                "event": env.get("GITHUB_EVENT_NAME"), "trigger": env.get("SOURCE") or None,
                "hop": env.get("HOP") or None, "ref": env.get("GITHUB_REF_NAME"), "sha": env.get("GITHUB_SHA")},
    }


def cmd_status(a) -> None:
    timings = _read_json(_timings_path())
    st = build_status(utcnow(), timings, _read_json(ROOT / "reports" / "forecast_meta.json"),
                      _read_json(Path(a.models) / "meta.json"), _read_json(ROOT / "reports" / "verification_summary.json"),
                      _read_json(ROOT / "threat-type" / "reports" / "type_forecast_meta.json"), dict(os.environ),
                      _read_json(Path(a.site) / "api" / "raions.json") if a.site else None)
    text = json.dumps(st, indent=1)
    if a.site:
        (Path(a.site) / "api").mkdir(parents=True, exist_ok=True)
        (Path(a.site) / "api" / "status.json").write_text(text)
    if a.store:
        (Path(a.store) / STATUS_PATH).parent.mkdir(parents=True, exist_ok=True)
        (Path(a.store) / STATUS_PATH).write_text(text)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        rows = "\n".join(f"| {k} | {v:.1f} |" for k, v in st["steps_s"].items())
        with open(summary, "a") as f:
            rz = st["raions"] or {}
            f.write(f"### Pipeline run\n\nForecast origin {st['origin']} (last poll {st['last_poll']}), "
                    f"models trained until {st['model']['trained_until']}, served by {st['model']['served']} "
                    f"(text: {st['model']['text_status']}); raions: {rz.get('models_source')} models, "
                    f"tier {rz.get('tier')}, methods {rz.get('methods')}; "
                    f"{st['verification']['n_verified']} forecasts verified.\n\n"
                    f"| step | seconds |\n|---|---|\n{rows}\n| **total so far** | {st['elapsed_s']} |\n")


# ------------------------------------------------------------- retrain trigger
def retrain_reason(meta: dict, dataset_end: datetime | None, after_h: float,
                   missing_layers: list[str] | None = None) -> str | None:
    """Why the train job should run now (None: the served models are fine)."""
    if not meta:
        return "no models"
    if missing_layers:
        return f"the bundle has no {' and no '.join(missing_layers)} models"
    if meta.get("holdout_start") is None:
        return "models were trained by the pipeline's fallback (no holdout scores)"
    trained = parse_ts(meta.get("data_end"))
    if dataset_end is not None and trained is not None and dataset_end - trained >= timedelta(hours=after_h):
        return f"dataset has {(dataset_end - trained).total_seconds() / 3600:.0f} h more data than the models"
    return None


def train_blockers(recent: list[dict], why: str, cooldown_min: float, bundle_trained: datetime | None,
                   now: datetime) -> list[dict]:
    """Train runs that make a new dispatch pointless: one still running, or one started recently.

    A bundle without the raion/text layers waits 6 h between dispatches, so a layer that keeps
    failing to train does not trigger a train run every hour. Only train runs started after
    that bundle was trained count for it: a run from before (e.g. under the previous models
    key, when modelling code changed) cannot have produced its layers.
    """
    missing = why.startswith("the bundle has no")
    cutoff = now - timedelta(minutes=max(cooldown_min, 360) if missing else cooldown_min)
    busy = []
    for r in recent:
        created = parse_ts(r["created_at"])
        if r["status"] != "completed":
            busy.append(r)
        elif created > cutoff and not (missing and bundle_trained is not None and created < bundle_trained):
            busy.append(r)
    return busy


def cmd_retrain_check(a) -> None:
    meta = _read_json(Path(a.models) / "meta.json")
    fc = _read_json(ROOT / "reports" / "forecast_meta.json")
    end = parse_ts((fc.get("history_ends_utc") or "").replace(" ", "T") + "Z") if fc.get("history_ends_utc") else None
    missing = [name for name, sub in (("raion", "raions"), ("text", "text"))
               if not (Path(a.models) / sub / "meta.json").exists()]
    why = retrain_reason(meta, end, a.after_hours, missing)
    if why is None:
        print("models are current")
        return
    ref = os.environ["GITHUB_REF_NAME"]
    recent = runs(a.workflow, ref, per_page=5)
    busy = train_blockers(recent, why, a.cooldown, parse_ts(meta.get("trained_at")), utcnow())
    cooldown = max(a.cooldown, 360) if why.startswith("the bundle has no") else a.cooldown
    if busy:
        print(f"retrain wanted ({why}) but a train run is running or started < {cooldown:.0f} min ago: {busy[0]['html_url']}")
        return
    dispatch(a.workflow, ref)
    notice(f"dispatched {a.workflow} on {ref}: {why}")


# ------------------------------------------------------------- daily jobs
def dispatch_reason(lag_h: float | None, recent: list[dict], now: datetime, daily_h: float = 22,
                    lag_limit_h: float = 6, cooldown_h: float = 3) -> str | None:
    """Why a daily job (collect.yml's history pull, threat-labels.yml) should be dispatched now, or None.

    GitHub's schedule does not fire reliably in this repository (the daily crons of collect.yml
    and threat-labels.yml never ran), so the pipeline dispatches them itself: once a day, and,
    for the history pull, sooner when the alert record the forecast used is complete only up to
    more than `lag_limit_h` hours before its origin (a poll gap over POLL_GAP stops the polls'
    coverage until the next pull re-anchors it), then at most every `cooldown_h` hours. Never
    while a run of the job is queued or running. `recent`: the job's latest runs on this branch.
    """
    if any(r["status"] != "completed" for r in recent):
        return None
    last = max((parse_ts(r["created_at"]) for r in recent), default=None)
    age_h = None if last is None else (now - last).total_seconds() / 3600
    if age_h is None or age_h >= daily_h:
        return "daily run" + ("" if age_h is None else f" (the last one {age_h:.0f} h ago)")
    if lag_h is not None and lag_h > lag_limit_h and age_h >= cooldown_h:
        return f"the alert record is complete only up to {lag_h:.0f} h before the forecast"
    return None


def cmd_history_check(a) -> None:
    fc = _read_json(ROOT / "reports" / "forecast_meta.json")
    at = lambda k: parse_ts(fc[k].replace(" ", "T") + "Z") if fc.get(k) else None  # noqa: E731
    origin, complete = at("origin_utc"), at("record_complete_until_utc")
    lag_h = (origin - complete).total_seconds() / 3600 if origin and complete else None
    ref = os.environ["GITHUB_REF_NAME"]
    why = dispatch_reason(lag_h, runs(a.workflow, ref, per_page=5), utcnow())
    if why is None:
        print("history pull not due" + ("" if lag_h is None else f" (record complete up to {lag_h:.1f} h before the origin)"))
        return
    dispatch(a.workflow, ref)
    notice(f"dispatched {a.workflow} on {ref}: {why}")


def cmd_daily_check(a) -> None:
    ref = os.environ["GITHUB_REF_NAME"]
    why = dispatch_reason(None, runs(a.workflow, ref, per_page=5), utcnow())
    if why is None:
        print(f"{a.workflow}: not due")
        return
    dispatch(a.workflow, ref)
    notice(f"dispatched {a.workflow} on {ref}: {why}")


# ------------------------------------------------------------------- origin
def origin_wait(last_poll: datetime, now: datetime, margin_s: float = 2.0) -> float:
    """Seconds until the forecast origin (the first whole minute at or after the poll) has
    passed. Text features at the origin need the archive to be complete up to it, so the
    archive is fetched only then: never later information, never a gap."""
    origin = last_poll if last_poll.second == 0 and last_poll.microsecond == 0 else \
        last_poll.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return max(0.0, (origin - now).total_seconds() + margin_s)


def cmd_wait_origin(a) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    import pandas as pd  # noqa: E402  (only this command needs the store)

    from airraid import store

    now = utcnow()
    try:
        polls = store.load(a.store, "polls", since=pd.Timestamp(now) - pd.Timedelta(hours=1))
    except Exception as e:  # never block the archive update: the forecast checks coverage itself
        print(f"could not read the polls ({e!r}): not waiting")
        return
    if polls.empty:
        print("no poll in the last hour: nothing to wait for")
        return
    last = polls["observed_at"].max().to_pydatetime()
    wait = origin_wait(last, now)
    print(f"last poll {last:%H:%M:%S}Z: waiting {wait:.0f} s for the origin minute before fetching the text archive")
    time.sleep(min(wait, 62.0))


# ------------------------------------------------------------------ keep-alive
def next_slot(now: datetime, slot: int = SLOT_MIN, offset: int = SLOT_OFFSET) -> datetime:
    """The next cron slot strictly after `now` (minutes offset, offset+slot, ...)."""
    base = now.replace(second=0, microsecond=0)
    m = (base.minute - offset) % slot
    t = base + timedelta(minutes=slot - m)
    return t if t > now else t + timedelta(minutes=slot)


def keepalive_decision(enabled: bool, hop: int, max_hops: int, newer_runs: list[dict]) -> tuple[bool, str]:
    if not enabled:
        return False, "disabled (repository variable PIPELINE_KEEPALIVE=off)"
    if hop >= max_hops:
        return False, f"chain of {hop} keep-alive runs reached the limit {max_hops}; waiting for cron, the pinger or a person"
    if newer_runs:
        r = newer_runs[0]
        return False, f"a newer run exists ({r.get('event')}, {r.get('status')}): another trigger has the next slot"
    return True, "no newer run since this one started"


def cmd_keepalive(a) -> None:
    enabled = os.environ.get("KEEPALIVE", "on").strip().lower() not in ("off", "false", "0", "no")
    ref = os.environ["GITHUB_REF_NAME"]
    on_default = ref == (os.environ.get("DEFAULT_BRANCH") or "main")
    # other branches (tests): one keep-alive hop unless PIPELINE_KEEPALIVE_MAX_HOPS says otherwise
    max_hops = int(os.environ.get("MAX_HOPS") or (72 if on_default else 1))
    hop = int(os.environ.get("HOP") or 0)
    ok, why = keepalive_decision(enabled, hop, max_hops, [])
    if not ok:
        notice(f"keep-alive: {why}")
        return
    for attempt in range(4):
        try:
            me = gh("GET", f"/repos/{repo()}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
            break
        except Exception as e:
            print(f"keep-alive: could not read this run ({type(e).__name__}), retrying")
            time.sleep(10 * (attempt + 1))
    else:
        raise SystemExit("keep-alive: GitHub API unavailable")
    created, wf = parse_ts(me["created_at"]), me["workflow_id"]

    def newer() -> list[dict]:
        try:
            return [r for r in runs(wf, ref, per_page=10)
                    if r["id"] != me["id"] and parse_ts(r["created_at"]) > created]
        except Exception as e:  # a failed check must not end the chain; the next one decides
            print(f"keep-alive: could not list runs ({type(e).__name__}), checking again")
            return []

    target = next_slot(utcnow()) + timedelta(minutes=a.grace)
    print(f"keep-alive: waiting until {target:%H:%M:%S}Z for the next slot (+{a.grace} min grace)")
    while (left := (target - utcnow()).total_seconds()) > 0:
        time.sleep(min(20.0, left))
        if n := newer():
            notice(f"keep-alive: {keepalive_decision(enabled, hop, max_hops, n)[1]}")
            return
    ok, why = keepalive_decision(enabled, hop, max_hops, newer())
    if not ok:
        notice(f"keep-alive: {why}")
        return
    for attempt in range(4):
        try:
            dispatch(str(wf), ref, {"source": "keepalive", "hop": str(hop + 1)})
            break
        except Exception as e:
            print(f"keep-alive: dispatch failed ({type(e).__name__}), retrying")
            time.sleep(10 * (attempt + 1))
    else:
        raise SystemExit("keep-alive: could not dispatch the next run")
    notice(f"keep-alive: no trigger for the {target - timedelta(minutes=a.grace):%H:%M}Z slot; dispatched run (hop {hop + 1}/{max_hops})")


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(line_buffering=True)  # progress lines appear in the Actions log as they happen
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models-key")
    mi = sub.add_parser("models-info")
    mi.add_argument("--models", default="var/models")
    g = sub.add_parser("gate")
    g.add_argument("--min-gap", type=float, default=5, help="minutes")
    g.add_argument("--store-branch", default="live-data")
    t = sub.add_parser("timed")
    t.add_argument("name")
    t.add_argument("argv", nargs=argparse.REMAINDER)
    s = sub.add_parser("status")
    s.add_argument("--site")
    s.add_argument("--store")
    s.add_argument("--models", default="var/models")
    r = sub.add_parser("retrain-check")
    r.add_argument("--models", default="var/models")
    r.add_argument("--after-hours", type=float, default=6)
    r.add_argument("--cooldown", type=float, default=60, help="minutes between train dispatches")
    r.add_argument("--workflow", default="train.yml")
    hc = sub.add_parser("history-check")
    hc.add_argument("--workflow", default="collect.yml")
    dc = sub.add_parser("daily-check")
    dc.add_argument("--workflow", required=True)
    w = sub.add_parser("wait-origin")
    w.add_argument("--store", default="live-data")
    k = sub.add_parser("keepalive")
    k.add_argument("--grace", type=float, default=3, help="minutes after the slot to wait for cron")
    a = ap.parse_args(argv)
    if a.cmd == "models-key":
        print(models_key())
    else:
        {"models-info": cmd_models_info, "gate": cmd_gate, "timed": cmd_timed, "status": cmd_status, "retrain-check": cmd_retrain_check,
         "history-check": cmd_history_check, "daily-check": cmd_daily_check, "keepalive": cmd_keepalive, "wait-origin": cmd_wait_origin}[a.cmd](a)


if __name__ == "__main__":
    main()
