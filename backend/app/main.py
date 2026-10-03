from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from backend.app.api.routers.exams import router as exams_router
from backend.app.api.routers.grading import router as grading_router
from backend.app.api.routers.health import router as health_router
from backend.app.api.routers.review import router as review_router
from backend.app.api.routers.rubrics import router as rubrics_router
from backend.app.api.routers.scripts import router as scripts_router
from backend.app.api.routers.students import router as students_router
from backend.app.db.database import init_db
from backend.app.db.exams import mark_interrupted_jobs
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    mark_interrupted_jobs()  # a restart kills any job that was mid-run
    yield


app = FastAPI(
    title="RubricTrace API",
    description=(
        "Self-hosted teacher-in-the-loop rubric-based evaluation of scanned answer scripts."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for api_router in (
    health_router,
    students_router,
    scripts_router,
    grading_router,
    rubrics_router,
    review_router,
    exams_router,
):
    app.include_router(api_router, prefix="/api")


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


WEB_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if WEB_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=str(WEB_DIST), html=True), name="static")
else:

    @app.get("/")
    def root() -> dict[str, str]:
        return {"status": "ok", "service": "RubricTrace API", "version": "2.0.0"}
