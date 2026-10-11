"""Build the web UI as a static site (GitHub Pages): no server, JSON precomputed.

    python scripts/build_static_site.py site                       # standalone: trains if needed
    AIRRAID_STORE=live-data AIRRAID_MODEL_DIR=var/models \
        python scripts/build_static_site.py site --status status.json  # the 10-minute pipeline

Runs the same ForecastEngine as the FastAPI service once, saves the answers of
/api/state, /api/forecast, /api/regions[/{id}], /api/metrics, /api/verification
and /api/threats (only if the threat-type forecast exists) as api/*.json and
copies the UI into site/. The UI detects the <meta name="airraid-static"> tag
and reads api/<name>.json instead of calling /api/<name>. Without threat-type
data the threat layer is hidden by the UI itself (forecast.threats is null).

With a store (AIRRAID_STORE or --store) the engine runs in store mode: no
second training and no second forecast. It loads the model bundle predict_now.py
used (AIRRAID_MODEL_DIR), serves the forecast predict_now.py logged to the store
for the newest origin, the live state of the newest store poll, and the
verification verify.py wrote to the store. --status adds api/status.json
(written by scripts/pipeline.py status: last update, step timings, data ends).
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from airraid_app.api import STATIC, create_app  # noqa: E402
from airraid_app.config import Settings  # noqa: E402
from airraid_app.engine import ForecastEngine  # noqa: E402

STATIC_API_NOTE = (
    '<p>Усі дані сторінки доступні як JSON (оновлюються разом зі збіркою сайту): '
    '<a href="api/state.json">state</a>, <a href="api/forecast.json">forecast</a>, '
    '<a href="api/regions.json">regions</a>, <a href="api/metrics.json">metrics</a>, '
    '<a href="api/verification.json">verification</a>, <a href="api/status.json">status</a>.</p>'
)


def build(out: Path, settings: Settings, status: Path | None = None) -> None:
    engine = ForecastEngine(settings)
    if settings.store_dir is not None:
        # pipeline: serve exactly the models and forecast of this run, never train here
        engine.load_data()
        if not engine.load_models():
            raise SystemExit(f"no compatible models in {settings.model_dir}: run predict_now.py --models first")
    else:
        engine.bootstrap()
    if engine.status == "error":
        raise SystemExit(f"engine failed: {engine.message}")
    client = TestClient(create_app(settings, engine))

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out / "static")
    api = out / "api"

    def save(path: str, required: bool = True) -> dict | list | None:
        r = client.get(f"/api/{path}")
        if r.status_code != 200:
            if required:
                raise SystemExit(f"/api/{path} -> {r.status_code}: {r.text[:200]}")
            logging.info("skip /api/%s (%s)", path, r.status_code)
            return None
        dest = api / f"{path}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(r.json(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return r.json()

    save("state")
    save("forecast")
    for reg in save("regions"):
        save(f"regions/{reg['id']}")
    save("metrics", required=False)
    save("verification", required=False)
    save("threats", required=False)  # 404 without a threat-type forecast
    if status is not None and status.exists():
        shutil.copy(status, api / "status.json")
    else:
        save("status")

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace("<head>", '<head>\n<meta name="airraid-static" content="1">', 1)
    start = html.index('<p>Усі дані сторінки доступні як JSON')
    end = html.index("</p>", start) + 4
    html = html[:start] + STATIC_API_NOTE + html[end:]
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("")
    logging.info("site written to %s", out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", nargs="?", default="site", type=Path)
    ap.add_argument("--store", type=Path, default=None, help="point-in-time store (else AIRRAID_STORE)")
    ap.add_argument("--status", type=Path, default=None, help="status.json to publish as api/status.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings.from_env()
    s.background = False
    if args.store is not None:
        s.store_dir = args.store
    build(args.out, s, args.status)


if __name__ == "__main__":
    main()
