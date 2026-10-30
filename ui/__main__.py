"""Acquisition station UI.

    python -m ui                 # real camera, http://127.0.0.1:8000
    python -m ui --sim           # simulator, no camera needed
    python -m ui --host 0.0.0.0  # reachable from other machines on the network

There is no login: anyone who can reach the port can change camera settings and run
flat-field calibration. Only expose it on the plant's camera network.
"""
from __future__ import annotations

import argparse
import logging

import uvicorn

from acquisition.config import DEFAULT_CONFIG, load_config

from .server import create_app
from .service import CameraService

log = logging.getLogger("ui")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m ui")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--sim", action="store_true", help="use the simulator instead of the camera")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    service = CameraService(cfg, args.config, sim=args.sim).start()
    if args.host not in ("127.0.0.1", "localhost"):
        log.warning("listening on %s — no authentication; keep this on the camera network", args.host)
    log.info("open http://%s:%d", "127.0.0.1" if args.host == "0.0.0.0" else args.host, args.port)
    try:
        uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="warning")
    finally:
        service.shutdown()


if __name__ == "__main__":
    main()
