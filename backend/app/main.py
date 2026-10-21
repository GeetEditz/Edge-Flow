"""FastAPI control plane entry point.

  cd backend && .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import routes, ws
from app.config import Settings
from app.core.cluster import ClusterError
from app.core.services import Services
from app.core.watchdog import improve_timer_resolution
from app.experiments.runner import ExperimentBusy


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d)


def configure_logging() -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger("cdn")
    if not root.handlers:
        root.addHandler(h)
        root.setLevel(logging.INFO)
        root.propagate = False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    improve_timer_resolution()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = Services(settings)
        app.state.services = services
        await services.start()
        try:
            yield
        finally:
            await services.stop()

    app = FastAPI(title="Distributed CDN Simulator — Control Plane", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Content-Type"])
    access = logging.getLogger("cdn.api")

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        if request.method != "GET" or response.status_code >= 400:
            access.info("%s %s -> %s in %.1f ms", request.method, request.url.path, response.status_code,
                        (time.perf_counter() - t0) * 1000)
        return response

    def err(status: int, code: str, detail) -> JSONResponse:
        return JSONResponse({"error": code, "detail": detail}, status_code=status)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_, exc: StarletteHTTPException):
        return err(exc.status_code, {404: "not_found", 409: "conflict", 400: "bad_request"}.get(exc.status_code, "error"),
                   exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(_, exc: RequestValidationError):
        msgs = [f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors()]
        return err(422, "validation_error", "; ".join(msgs))

    @app.exception_handler(ClusterError)
    async def _cluster(_, exc: ClusterError):
        return err(exc.status, "not_found" if exc.status == 404 else "edge_error", str(exc))

    @app.exception_handler(ExperimentBusy)
    async def _busy(_, exc: ExperimentBusy):
        return err(409, "conflict", str(exc))

    @app.exception_handler(KeyError)
    async def _key(_, exc: KeyError):
        return err(404, "not_found", f"{exc.args[0]} not found" if exc.args else "not found")

    @app.exception_handler(ValueError)
    async def _value(_, exc: ValueError):
        return err(400, "bad_request", str(exc))

    app.include_router(routes.router)
    app.include_router(ws.router)
    return app


app = create_app()
