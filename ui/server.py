"""HTTP API + static frontend for the acquisition station."""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .service import CameraService, UserError

STATIC = Path(__file__).resolve().parent / "static"
NO_CACHE = {"Cache-Control": "no-store"}


def create_app(service: CameraService) -> FastAPI:
    app = FastAPI(title="Milof acquisition station", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(UserError)
    async def user_error(_, exc: UserError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(TimeoutError)
    async def timeout_error(_, exc: TimeoutError):
        return JSONResponse({"detail": f"המצלמה לא הגיבה בזמן: {exc}"}, status_code=504)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers=NO_CACHE)

    @app.get("/api/status")
    def status():
        return service.status()

    @app.get("/api/stats")
    def stats():
        return service.stats() or {}

    @app.get("/api/preview.jpg")
    def preview(width: int = 900):
        jpeg = service.preview_jpeg(width)
        if jpeg is None:
            return Response(status_code=204, headers=NO_CACHE)
        return Response(jpeg, media_type="image/jpeg", headers=NO_CACHE)

    @app.get("/api/settings")
    def settings():
        return service.settings()

    @app.post("/api/settings/camera")
    def set_camera(values: dict = Body(...)):
        return service.set_camera(values)

    @app.post("/api/settings/segmentation")
    def set_segmentation(values: dict = Body(...)):
        return service.set_segmentation(values)

    @app.post("/api/settings/storage")
    def set_storage(values: dict = Body(...)):
        return service.set_storage(values)

    @app.post("/api/settings/save")
    def save_settings():
        return service.save_config()

    @app.post("/api/cuts")
    def cuts(on: bool = Body(..., embed=True)):
        service.set_show_cuts(on)
        return {"on": on}

    @app.post("/api/capture/start")
    def capture_start(mode: str = Body("birds", embed=True)):
        service.start_capture(mode)
        return service.status()

    @app.post("/api/capture/stop")
    def capture_stop():
        return service.stop_capture()

    @app.get("/api/recent")
    def recent():
        return service.recent()

    @app.get("/api/recent/{seq}.jpg")
    def recent_jpeg(seq: int):
        jpeg = service.recent_jpeg(seq)
        if jpeg is None:
            raise HTTPException(404)
        return Response(jpeg, media_type="image/jpeg")

    @app.post("/api/reconnect")
    def reconnect():
        service.reconnect()
        return service.status()

    @app.get("/api/calibration")
    def calibration():
        return service.calibration_info()

    @app.post("/api/calibration/prnu")
    def prnu():
        return service.run_prnu()

    @app.post("/api/calibration/user-set")
    def user_set():
        return service.save_user_set()

    @app.post("/api/calibration/record")
    def record(kind: str = Body(..., embed=True), note: str = Body("", embed=True)):
        return service.record(kind, note)

    @app.post("/api/calibration/board")
    def board():
        return service.save_board_image()

    @app.post("/api/sim/scene")
    def sim_scene(scene: str = Body(..., embed=True)):
        service.set_sim_scene(scene)
        return {"scene": scene}

    return app
