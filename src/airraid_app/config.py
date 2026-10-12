"""Runtime settings, read from environment variables (see docs/web-app.md)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from airraid.data import RAW_PATH
from airraid.raion_data import OFFICIAL_PATH

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
    # Raion-level history: the official alert file (stopped 2026-09-07) and, when
    # available, the live alert store written by the collector (airraid.store).
    official_path: Path = OFFICIAL_PATH
    store_dir: Path | None = None
    # Train the raion models (a few hundred MB while training); off on 512 MB hosts:
    # the raion layer then derives raion forecasts from the oblast forecast.
    raion_models: bool = True
    # False (AIRRAID_RAION_TRAIN=0): never train raion models here; serve the ones saved in
    # raion_model_dir (default <model_dir>/raions) by a separate job, else oblast x share.
    raion_train: bool = True
    raion_model_dir: Path | None = None
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

    @property
    def raion_models_path(self) -> Path:
        """Where saved raion models are read from (AIRRAID_RAION_MODEL_DIR, default <model_dir>/raions)."""
        return self.raion_model_dir or self.model_dir / "raions"

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
        if v := env.get("AIRRAID_OFFICIAL_PATH"):
            s.official_path = Path(v)
        if v := env.get("AIRRAID_STORE") or env.get("AIRRAID_STORE_DIR"):  # same variable as predict_now.py
            s.store_dir = Path(v)
        s.alerts_token = env.get("ALERTS_IN_UA_TOKEN") or None
        local_types = ROOT / "threat-type" / "reports" / "type_forecast_now.csv"
        s.type_forecast = env.get("AIRRAID_TYPE_FORECAST") or (str(local_types) if local_types.exists() else None)
        s.download = _env_bool("AIRRAID_DOWNLOAD", s.download)
        s.raion_models = _env_bool("AIRRAID_RAION_MODELS", s.raion_models)
        s.raion_train = _env_bool("AIRRAID_RAION_TRAIN", s.raion_train)
        if v := env.get("AIRRAID_RAION_MODEL_DIR"):
            s.raion_model_dir = Path(v)
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
