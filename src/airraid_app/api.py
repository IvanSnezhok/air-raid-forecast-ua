"""FastAPI application: JSON API under /api and the web UI at /."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import Settings
from .engine import ForecastEngine, NotReady
from .regions import slug, uk_name
from .threats import LEVEL, TYPE_SHORT, TYPE_UK, TYPES

STATIC = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None, engine: ForecastEngine | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = engine or ForecastEngine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.background:
            engine.start_background()
        yield
        engine.stop()

    app = FastAPI(
        title="Прогноз повітряних тривог",
        version=__version__,
        description="Поточний стан і прогноз повітряних тривог по областях України на 1-6 годин.",
        lifespan=lifespan,
    )
    app.state.engine = engine

    def _ready():
        if engine.alerts is None:
            raise HTTPException(503, detail={"status": engine.status, "message": engine.message})

    async def _forecast() -> dict:
        _ready()
        try:
            return await run_in_threadpool(engine.forecast)
        except NotReady:
            raise HTTPException(503, detail={"status": engine.status, "message": engine.message})

    @app.get("/api/health", tags=["service"])
    def health():
        return {
            "status": engine.status,
            "message": engine.message,
            "version": __version__,
            "live_api_configured": bool(settings.alerts_token),
            "history_end": engine.hist_end.isoformat() if engine.hist_end is not None else None,
            "model_trained_at": engine.meta.get("trained_at"),
        }

    @app.get("/api/regions", tags=["data"])
    def regions():
        _ready()
        return [{"id": slug(r), "name": r, "name_uk": uk_name(r)} for r in engine.regions]

    @app.get("/api/state", tags=["data"], summary="Поточний стан тривог по областях")
    async def state():
        _ready()
        live = await run_in_threadpool(engine.live_state)
        return {
            "source": live.source,
            "as_of": live.as_of.isoformat(),
            "error": live.error,
            "n_active": sum(s.active for s in live.regions.values()),
            "regions": [
                {"id": slug(r), "name": r, "name_uk": uk_name(r), "active": s.active,
                 "since": s.since.isoformat() if s.since is not None else None, "locations": s.locations}
                for r, s in live.regions.items()
            ],
        }

    def _threats_meta(t: dict | None) -> dict | None:
        if not t:
            return None
        return {"origin": t["origin_utc"], "posts_until": t["posts_until_utc"], "horizons": t["horizons"],
                "types": [{"id": k, "name_uk": TYPE_UK[k], "short": TYPE_SHORT[k], "level": LEVEL[k]} for k in TYPES]}

    @app.get("/api/forecast", tags=["forecast"], summary="Прогноз для всіх областей")
    async def forecast():
        f = await _forecast()
        t = await run_in_threadpool(engine.threats.get)
        return {k: v for k, v in f.items() if k not in ("regions", "raions")} | {
            "threats": _threats_meta(t),
            "regions": [{**{k: r[k] for k in ("id", "name", "name_uk", "active_at_origin", "probs")},
                         "types": (t or {}).get("regions", {}).get(r["name"])} for r in f["regions"]],
        }

    @app.get("/api/threats", tags=["forecast"], summary="Прогноз типу загрози (дрони, ракети, балістика) і рівня")
    async def threats():
        _ready()
        t = await run_in_threadpool(engine.threats.get)
        if not t:
            raise HTTPException(404, detail="threat-type forecast is not configured (AIRRAID_TYPE_FORECAST)")
        return _threats_meta(t) | {"regions": [{"id": slug(r), "name": r, "name_uk": uk_name(r), "types": v}
                                               for r, v in sorted(t["regions"].items())]}

    @app.get("/api/regions/{region_id}", tags=["forecast"], summary="Прогноз, історія і якість для однієї області")
    async def region(region_id: str):
        f = await _forecast()
        match = next((r for r in f["regions"] if r["id"] == region_id), None)
        if match is None:
            raise HTTPException(404, detail=f"unknown region '{region_id}'")
        return {
            **match,
            "origin": f["origin"], "origin_kyiv": f["origin_kyiv"], "windows": f["windows"],
            "holdout": engine.region_holdout(match["name"]),
            "types": ((await run_in_threadpool(engine.threats.get)) or {}).get("regions", {}).get(match["name"]),
            "verification": (await run_in_threadpool(engine.verification, match["name"]))["recent"],
        }

    async def _raions() -> tuple[dict, dict]:
        f = await _forecast()
        r = f.get("raions")
        if not r:
            raise HTTPException(503, detail={"status": engine.status, "message": "raion layer is not available"})
        return f, r

    @app.get("/api/raions", tags=["raions"], summary="Райони: хто під тривогою зараз і прогноз на 1-6 год")
    async def raions():
        f, r = await _raions()
        return {k: v for k, v in r.items()} | {"horizons": f["horizons"], "windows": f["windows"],
                                               "origin_kyiv": f["origin_kyiv"]}

    @app.get("/api/raions/{raion_id}", tags=["raions"], summary="Один район: стан, прогноз, метод і його якість")
    async def raion(raion_id: str):
        f, r = await _raions()
        match = next((x for x in r["raions"] if x["id"] == raion_id), None)
        if match is None:
            raise HTTPException(404, detail=f"unknown or not forecast raion '{raion_id}'")
        oblast = next((x for x in f["regions"] if x["name"] == match["oblast"]), None)
        return {
            **match,
            "origin": f["origin"], "origin_kyiv": f["origin_kyiv"], "windows": f["windows"],
            "live_source": r["live_source"], "live_as_of": r["live_as_of"], "share_period": r["share_period"],
            "history_until": r["history_until"], "methods": r["methods"], "tier": r["tier"], "models": r["models"],
            "oblast_probs": oblast["probs"] if oblast else None,
            "oblast_active_at_origin": oblast["active_at_origin"] if oblast else None,
            "evaluation": engine.raions.evaluation_for(match["oblast"]),
        }

    @app.get("/api/verification", tags=["evaluation"], summary="Перевірка виданих прогнозів: що справдилось")
    async def verification():
        _ready()
        return await run_in_threadpool(engine.verification)

    @app.get("/api/metrics", tags=["evaluation"], summary="Якість моделей")
    def metrics():
        if not engine.meta:
            raise HTTPException(503, detail={"status": engine.status, "message": engine.message})
        return engine.metrics()

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory airraid_app.api:app_factory`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
