"""Blog Writer Web App — FastAPI Entrypoint.

Serves the React SPA frontend and provides API endpoints for
blog generation, AI editing, export, and persistence.
"""

import logging
import logging.handlers
import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file (local development)
load_dotenv()

# Configure logging for debugging Foundry API calls
log_level = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Set specific loggers for debugging
logging.getLogger("backend.services.blog_service").setLevel(
    logging.DEBUG if log_level == "DEBUG" else log_level
)
logging.getLogger("backend.services.ai_editor").setLevel(
    logging.DEBUG if log_level == "DEBUG" else log_level
)
logging.getLogger("backend.db.cosmos_client").setLevel(
    logging.DEBUG if log_level == "DEBUG" else log_level
)
logging.getLogger("backend.services.linkedin_service").setLevel(
    logging.DEBUG if log_level == "DEBUG" else log_level
)

logger = logging.getLogger(__name__)
logger.info(f"Blog Writer initialized with log level: {log_level}")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.routers import generate, edit, blogs, export, publish, linkedin, feeds, diagnostics

# Create FastAPI app
app = FastAPI(
    title="Blog Writer",
    description="AI-powered blog generator from GitHub repos and webpages",
    version="2.0.0",
)

# CORS — allow frontend dev server and production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(generate.router)
app.include_router(edit.router)
app.include_router(blogs.router)
app.include_router(export.router)
app.include_router(publish.router)
app.include_router(linkedin.router)
app.include_router(feeds.router)
app.include_router(diagnostics.router)


@app.on_event("startup")
async def startup_event():
    """Start the feed crawl scheduler on application startup."""
    try:
        from backend.services.scheduler import start_scheduler
        start_scheduler()
        logger.info("Feed crawl scheduler started")
    except Exception as exc:
        logger.warning(f"Scheduler startup skipped: {exc}")


@app.on_event("shutdown")
async def shutdown_event():
    """Shut down the feed crawl scheduler."""
    try:
        from backend.services.scheduler import shutdown_scheduler
        shutdown_scheduler()
    except Exception:
        pass


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "2.0.0"}


# Serve React static files in production
STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        reload=True,
    )
