"""Runtime settings, read from environment variables (see docs/web-app.md)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from airraid.data import RAW_PATH

ROOT = Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_path: Path = RAW_PATH
    model_dir: Path = ROOT / "var" / "models"
    state_dir: Path = ROOT / "var" / "state"  # live alert log and forecast journal
    reports_dir: Path = ROOT / "reports"
    # Threat-type forecast CSV (path or URL) from threat-type/predict_type_now.py; optional.
    type_forecast: str | None = None
    # Live alert state. Never commit the token; set it as an env var / secret.
    alerts_token: str | None = None
    # Download a fresh copy of the volunteer dataset on start and every refresh.
    download: bool = True
    # Training window and size when reports/run_summary.json has no selection.
    train_months: int = 12
    max_train_months: int = 24  # cap for "all history" selections, keeps memory in check
    n_trees: int = 300
    holdout_days: int = 7
    live_refresh_s: int = 60
    data_refresh_h: float = 6.0
    retrain_h: float = 24.0
    # Text archive (a checkout of the text-data branch): serve the history + Telegram model
    # when it covers the forecast origin, else the history-only model. None = off.
    text_root: Path | None = None
    # Run loading/training in a background thread (False in tests).
    background: bool = True
    lgb_threads: int = field(default_factory=lambda: os.cpu_count() or 2)

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        env = os.environ
        if v := env.get("AIRRAID_DATA_PATH"):
            s.data_path = Path(v)
        if v := env.get("AIRRAID_MODEL_DIR"):
            s.model_dir = Path(v)
        if v := env.get("AIRRAID_STATE_DIR"):
            s.state_dir = Path(v)
        if v := env.get("AIRRAID_REPORTS_DIR"):
            s.reports_dir = Path(v)
        if v := env.get("AIRRAID_TEXT_ROOT"):
            s.text_root = Path(v)
        s.alerts_token = env.get("ALERTS_IN_UA_TOKEN") or None
        local_types = ROOT / "threat-type" / "reports" / "type_forecast_now.csv"
        s.type_forecast = env.get("AIRRAID_TYPE_FORECAST") or (str(local_types) if local_types.exists() else None)
        s.download = _env_bool("AIRRAID_DOWNLOAD", s.download)
        for name, attr, cast in (
            ("AIRRAID_TRAIN_MONTHS", "train_months", int),
            ("AIRRAID_MAX_TRAIN_MONTHS", "max_train_months", int),
            ("AIRRAID_TREES", "n_trees", int),
            ("AIRRAID_HOLDOUT_DAYS", "holdout_days", int),
            ("AIRRAID_LIVE_REFRESH_S", "live_refresh_s", int),
            ("AIRRAID_DATA_REFRESH_H", "data_refresh_h", float),
            ("AIRRAID_RETRAIN_H", "retrain_h", float),
            ("AIRRAID_LGB_THREADS", "lgb_threads", int),
        ):
            if v := env.get(name):
                setattr(s, attr, cast(v))
        return s
