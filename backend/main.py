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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.routers import generate, edit, blogs, export, publish, linkedin, twitter, medium, feeds, diagnostics, prompts, keywords, user, dashboard

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

# Auth middleware — validate Entra ID tokens on /api/* routes (except health)
AUTH_SKIP_PATHS = {"/api/health", "/api/linkedin/callback", "/api/twitter/oauth/callback"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require valid Entra ID bearer token for API routes."""
    path = request.url.path

    # Skip auth for non-API routes (static files), health, and LinkedIn callback
    if not path.startswith("/api/") or path in AUTH_SKIP_PATHS:
        return await call_next(request)

    # Skip auth if Entra ID is not configured (local dev without auth)
    entra_client_id = os.environ.get("ENTRA_CLIENT_ID", "")
    if not entra_client_id:
        return await call_next(request)

    from backend.auth import validate_token

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid Authorization header"},
        )

    token = auth_header[7:]
    try:
        claims = validate_token(token)
        # Store user claims on request state for downstream use
        request.state.user_claims = claims
    except Exception as e:
        return JSONResponse(
            status_code=401,
            content={"detail": str(e.detail) if hasattr(e, "detail") else "Authentication failed"},
        )

    return await call_next(request)


# Register API routers
app.include_router(generate.router)
app.include_router(edit.router)
app.include_router(blogs.router)
app.include_router(export.router)
app.include_router(publish.router)
app.include_router(linkedin.router)
app.include_router(twitter.router)
app.include_router(medium.router)
app.include_router(feeds.router)
app.include_router(diagnostics.router)
app.include_router(prompts.router)
app.include_router(keywords.router)
app.include_router(user.router)
app.include_router(dashboard.router)


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
