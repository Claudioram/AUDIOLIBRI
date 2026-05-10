"""
FastAPI application entrypoint.
Serves the REST API on /api/* and static frontend files on /*.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import settings
from app.database import init_db
from app.routes import auth as auth_router
from app.routes import books as books_router
from app.routes import chapters as chapters_router
from app.routes import audio as audio_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting audiobook-generator backend")
    init_db()
    logger.info(f"Database initialised: {settings.db_path}")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Audiobook Generator",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"https://{settings.domain}", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(books_router.router)
app.include_router(chapters_router.router)
app.include_router(audio_router.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


# ── Static frontend (mounted last so API routes take priority) ────────────────
_FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"
if not _FRONTEND_DIR.exists():
    # Docker path fallback
    _FRONTEND_DIR = Path("/app/frontend")

if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="static")
    logger.info(f"Serving frontend from {_FRONTEND_DIR}")
else:
    logger.warning(f"Frontend directory not found at {_FRONTEND_DIR}")
