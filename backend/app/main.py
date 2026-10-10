import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from backend.app.api.routers.exams import router as exams_router
from backend.app.api.routers.grading import router as grading_router
from backend.app.api.routers.health import router as health_router
from backend.app.api.routers.review import router as review_router
from backend.app.api.routers.scripts import router as scripts_router
from backend.app.api.routers.students import router as students_router
from backend.app.container import Container
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

WEB_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """Serve the built frontend; unknown paths get index.html so React Router can
    handle deep links like /exams/abc/results on refresh."""

    async def get_response(self, path, scope):  # type: ignore[no-untyped-def]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api"):
                return await super().get_response("index.html", scope)
            raise


def create_app(container: Container | None = None) -> FastAPI:
    """Build the application. Tests pass a Container wired to a temporary database."""
    container = container or Container()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        container.config.ensure_data_dirs()
        container.startup()
        yield
        container.shutdown()

    app = FastAPI(
        title="RubricTrace API",
        description=(
            "Self-hosted teacher-in-the-loop rubric-based evaluation of scanned answer scripts."
        ),
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=container.config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for api_router in (
        health_router,
        students_router,
        scripts_router,
        grading_router,
        review_router,
        exams_router,
    ):
        app.include_router(api_router, prefix="/api")

    if WEB_DIST.exists():
        app.mount("/", SPAStaticFiles(directory=str(WEB_DIST), html=True), name="static")
    else:

        @app.get("/")
        def root() -> dict[str, str]:
            return {"status": "ok", "service": "RubricTrace API", "version": "2.0.0"}

    return app


app = create_app()
