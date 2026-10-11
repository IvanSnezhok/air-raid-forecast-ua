"""Threat-type forecast (drones, massed drones, KAB, missiles, ballistic; yellow/red level).

Produced by threat-type/predict_type_now.py (needs the Air Force Telegram post
history) and published hourly by the forecast workflow as type_forecast_now.csv
next to the demo page. The app only reads that file, from a local path or a URL
(AIRRAID_TYPE_FORECAST), and shows it as an extra map layer when it is there.
"""
from __future__ import annotations

import io
import json
import logging
import time
import urllib.request
from pathlib import Path

import pandas as pd

log = logging.getLogger("airraid_app")

TYPES = ("drone", "massed_drone", "kab", "missile", "ballistic")
TYPE_UK = {
    "drone": "Ударні дрони",
    "massed_drone": "Масована атака дронів",
    "kab": "Керовані авіабомби (КАБ)",
    "missile": "Крилаті ракети",
    "ballistic": "Балістика",
}
TYPE_SHORT = {"drone": "БпЛА", "massed_drone": "Масов.", "kab": "КАБ", "missile": "Ракети", "ballistic": "Баліст."}
LEVEL = {"drone": "yellow", "massed_drone": "red", "kab": "red", "missile": "red", "ballistic": "red"}
REFRESH_S = 600


def _read_bytes(src: str) -> bytes:
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"User-Agent": "air-raid-forecast-ua"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    return Path(src).read_bytes()


def _meta_source(src: str) -> str:
    return src.rsplit("/", 1)[0] + "/type_forecast_meta.json" if "/" in src else "type_forecast_meta.json"


class ThreatForecast:
    def __init__(self, source: str | None):
        self.source = source
        self._cache: tuple[float, dict | None] | None = None

    def get(self) -> dict | None:
        """Parsed forecast {origin, regions: {region: {h: {...}}}}, or None when unavailable."""
        if not self.source:
            return None
        if self._cache and time.time() - self._cache[0] < REFRESH_S:
            return self._cache[1]
        try:
            data = self._load()
        except Exception as e:  # missing file, network: the layer is optional
            log.info("threat-type forecast unavailable (%s): %s", self.source, e)
            data = None
        self._cache = (time.time(), data)
        return data

    def _load(self) -> dict:
        df = pd.read_csv(io.BytesIO(_read_bytes(self.source)))
        meta = {}
        try:
            meta = json.loads(_read_bytes(_meta_source(self.source)))
        except Exception:
            pass
        regions: dict[str, dict] = {}
        for r in df.to_dict("records"):
            h = int(r["horizon_h"])
            entry = {
                "p_type": {t: _f(r.get(f"p_{t}")) for t in TYPES},
                "p_onset_type": {t: _f(r.get(f"p_onset_{t}")) for t in TYPES if f"p_onset_{t}" in r},
                "p_red": _f(r.get("p_red")),
                "level": r.get("level") if r.get("level") in ("yellow", "red") else None,
                "most_likely": r.get("most_likely") if r.get("most_likely") in TYPES else None,
            }
            regions.setdefault(r["region"], {})[h] = entry
        horizons = sorted({h for v in regions.values() for h in v})
        return {"origin_utc": meta.get("origin_utc"), "posts_until_utc": meta.get("posts_until_utc"),
                "horizons": horizons, "regions": regions}


def _f(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else round(v, 4)
