"""Command line: `airraid-app serve | train | forecast`."""
from __future__ import annotations

import argparse
import json
import logging
import os


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="airraid-app", description="Air raid alert forecast service")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the web app")
    sv.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    sv.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    sub.add_parser("train", help="download data, train and cache the models")
    sub.add_parser("forecast", help="print the current forecast as JSON")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("airraid_app.api:app_factory", factory=True, host=args.host, port=args.port)
        return

    from .config import Settings
    from .engine import ForecastEngine

    engine = ForecastEngine(Settings.from_env())
    engine.load_data()
    if args.cmd == "train":
        engine.train()
        print(json.dumps(engine.meta["plans"], indent=1))
    else:
        if not engine.load_models():
            engine.train()
        f = engine.forecast()
        print(json.dumps({k: f[k] for k in ("origin_kyiv", "live_source", "warnings")}, ensure_ascii=False, indent=1))
        for r in sorted(f["regions"], key=lambda r: -r["probs"]["active"][1]):
            p = r["probs"]
            print(f"{r['name_uk']:<18} " + " ".join(f"a{h}={p['active'][h]:.2f}" for h in p["active"])
                  + "  " + " ".join(f"o{h}={p['onset'][h]:.2f}" for h in p["onset"]))


if __name__ == "__main__":
    main()
