"""FastAPI app factory: health check, WS chat endpoint, REST routers, static frontend."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import INDEX_HTML, STATIC_DIR, ensure_dirs
from app.db.migrations import apply_migrations
from app.logging_setup import get_logger, setup_logging
from app.db.seed import seed_builtin_templates
from app.routers import agent_status as agent_status_router
from app.routers import assess as assess_router
from app.routers import attachments as attachments_router
from app.routers import config as config_router
from app.routers import conversation_stats as conversation_stats_router
from app.routers import conversations as conversations_router
from app.routers import flow_templates as flow_templates_router
from app.routers import projects as projects_router
from app.routers import search as search_router
from app.routers import teams as teams_router
from app.routers import terminal as terminal_router
from app.routers import server as server_router
from app.services import attachments_service
from app.services.process_registry import registry
from app.ws.chat_socket import handle_chat_socket

log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    removed = attachments_service.prune_orphans()
    if removed:
        log.info("Startup cleanup removed %d orphaned attachment file(s)", removed)
    log.info("GoClaudaddy backend started")
    yield
    registry.shutdown_all()
    log.info("GoClaudaddy backend shutting down")


def create_app() -> FastAPI:
    ensure_dirs()
    setup_logging()
    apply_migrations()
    seed_builtin_templates()

    app = FastAPI(title="GoClaudaddy", lifespan=lifespan)

    class _NoCacheStatic(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

    app.add_middleware(_NoCacheStatic)
    app.include_router(agent_status_router.router)
    app.include_router(assess_router.router)
    app.include_router(conversation_stats_router.router)
    app.include_router(conversations_router.router)
    app.include_router(config_router.router)
    app.include_router(flow_templates_router.router)
    app.include_router(projects_router.router)
    app.include_router(search_router.router)
    app.include_router(teams_router.router)
    app.include_router(terminal_router.router)
    app.include_router(attachments_router.router)
    app.include_router(server_router.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        log.error("Unhandled error on %s %s: %s", request.method, request.url.path, exc, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Something went wrong. Check the logs folder for details."},
        )

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.websocket("/ws/chat/{conversation_id}")
    async def ws_chat(websocket: WebSocket, conversation_id: str):
        await handle_chat_socket(websocket, conversation_id)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(INDEX_HTML)

    return app


app = create_app()
