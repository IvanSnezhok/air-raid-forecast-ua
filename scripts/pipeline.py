"""Glue for .github/workflows/pipeline.yml and train.yml (standard library only).

    python3 scripts/pipeline.py models-key                 # cache key prefix of the served model bundle
    python3 scripts/pipeline.py models-info --models DIR   # fresh=true if trained in this job; cache key suffix
    python3 scripts/pipeline.py gate                       # skip a run that comes right after another one
    python3 scripts/pipeline.py timed NAME -- CMD ...      # run CMD, record its wall time as step NAME
    python3 scripts/pipeline.py status --site site --store live-data   # api/status.json + _state/pipeline.json
    python3 scripts/pipeline.py retrain-check --models var/models      # dispatch train.yml when models are stale
    python3 scripts/pipeline.py keepalive                  # dead man's switch for the 10-minute schedule

GitHub API calls use GITHUB_TOKEN (never printed). Decisions are pure functions
(`gate_decision`, `next_slot`, `keepalive_decision`, `retrain_reason`) with
tests in tests/test_pipeline_glue.py.

Why a keep-alive: GitHub's `schedule` is best effort. In this repository the
hourly forecast cron fired twice in 14 hours and the 3-hourly Pages cron 48
minutes late. The keep-alive is a bounded dead man's switch, not a loop: after
a run it waits until the next 10-minute slot plus a grace period and dispatches
the workflow only if nothing else (cron, an external pinger, a person) has
started a newer run by then. Chains of keep-alive runs stop after
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
    for f in MODEL_FILES:
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
    """Run or skip. Manual runs and pushes always run; scheduled, keep-alive and pinger
    runs are skipped when another run finished less than `min_gap` ago (two triggers
    for the same slot)."""
    if event == "push" or (event == "workflow_dispatch" and source in ("", "manual")):
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


def build_status(now: datetime, timings: dict, forecast: dict, models: dict, verification: dict,
                 threats: dict, env: dict) -> dict:
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
                  "holdout": models.get("holdout_start") is not None, "code_version": models.get("code_version")},
        "threat_type_origin": utc(threats.get("origin_utc")),
        "verification": {"n_verified": verification.get("n_verified"), "pending": verification.get("pending"),
                         "by_label_source": verification.get("n_verified_by_label_source"),
                         "windows": windows},
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
                      _read_json(ROOT / "threat-type" / "reports" / "type_forecast_meta.json"), dict(os.environ))
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
            f.write(f"### Pipeline run\n\nForecast origin {st['origin']} (last poll {st['last_poll']}), "
                    f"models trained until {st['model']['trained_until']}, "
                    f"{st['verification']['n_verified']} forecasts verified.\n\n"
                    f"| step | seconds |\n|---|---|\n{rows}\n| **total so far** | {st['elapsed_s']} |\n")


# ------------------------------------------------------------- retrain trigger
def retrain_reason(meta: dict, dataset_end: datetime | None, after_h: float) -> str | None:
    """Why the train job should run now (None: the served models are fine)."""
    if not meta:
        return "no models"
    if meta.get("holdout_start") is None:
        return "models were trained by the pipeline's fallback (no holdout scores)"
    trained = parse_ts(meta.get("data_end"))
    if dataset_end is not None and trained is not None and dataset_end - trained >= timedelta(hours=after_h):
        return f"dataset has {(dataset_end - trained).total_seconds() / 3600:.0f} h more data than the models"
    return None


def cmd_retrain_check(a) -> None:
    meta = _read_json(Path(a.models) / "meta.json")
    fc = _read_json(ROOT / "reports" / "forecast_meta.json")
    end = parse_ts((fc.get("history_ends_utc") or "").replace(" ", "T") + "Z") if fc.get("history_ends_utc") else None
    why = retrain_reason(meta, end, a.after_hours)
    if why is None:
        print("models are current")
        return
    ref = os.environ["GITHUB_REF_NAME"]
    recent = runs(a.workflow, ref, per_page=5)
    cutoff = utcnow() - timedelta(minutes=a.cooldown)
    busy = [r for r in recent if r["status"] != "completed" or parse_ts(r["created_at"]) > cutoff]
    if busy:
        print(f"retrain wanted ({why}) but a train run is running or started < {a.cooldown} min ago: {busy[0]['html_url']}")
        return
    dispatch(a.workflow, ref)
    notice(f"dispatched {a.workflow} on {ref}: {why}")


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
        return False, f"a newer run exists ({r.get('event')}, {r.get('status')}): the schedule is working"
    return True, "no newer run since this one started"


def cmd_keepalive(a) -> None:
    enabled = os.environ.get("KEEPALIVE", "on").strip().lower() not in ("off", "false", "0", "no")
    ref = os.environ["GITHUB_REF_NAME"]
    on_default = ref == (os.environ.get("DEFAULT_BRANCH") or "main")
    max_hops = int(os.environ.get("MAX_HOPS") or (72 if on_default else 1))
    hop = int(os.environ.get("HOP") or 0)
    ok, why = keepalive_decision(enabled, hop, max_hops, [])
    if not ok:
        notice(f"keep-alive: {why}")
        return
    me = gh("GET", f"/repos/{repo()}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    created, wf = parse_ts(me["created_at"]), me["workflow_id"]

    def newer() -> list[dict]:
        return [r for r in runs(wf, ref, per_page=10)
                if r["id"] != me["id"] and parse_ts(r["created_at"]) > created]

    target = next_slot(utcnow()) + timedelta(minutes=a.grace)
    print(f"keep-alive: waiting until {target:%H:%M:%S}Z for the next slot (+{a.grace} min grace)")
    while (left := (target - utcnow()).total_seconds()) > 0:
        time.sleep(min(60.0, left))
        if n := newer():
            notice(f"keep-alive: {keepalive_decision(enabled, hop, max_hops, n)[1]}")
            return
    ok, why = keepalive_decision(enabled, hop, max_hops, newer())
    if not ok:
        notice(f"keep-alive: {why}")
        return
    dispatch(str(wf), ref, {"source": "keepalive", "hop": str(hop + 1)})
    notice(f"keep-alive: no trigger for the {target - timedelta(minutes=a.grace):%H:%M}Z slot; dispatched run (hop {hop + 1}/{max_hops})")


def main(argv: list[str] | None = None) -> None:
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
    k = sub.add_parser("keepalive")
    k.add_argument("--grace", type=float, default=3, help="minutes after the slot to wait for cron")
    a = ap.parse_args(argv)
    if a.cmd == "models-key":
        print(models_key())
    else:
        {"models-info": cmd_models_info, "gate": cmd_gate, "timed": cmd_timed, "status": cmd_status, "retrain-check": cmd_retrain_check,
         "keepalive": cmd_keepalive}[a.cmd](a)


if __name__ == "__main__":
    main()
