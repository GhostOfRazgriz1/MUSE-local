"""FastAPI application for Agent OS web UI."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent_os.api.auth import init_auth, require_token
from agent_os.config import Config

logger = logging.getLogger(__name__)

# Global reference to orchestrator (set during startup)
_orchestrator = None


def get_orchestrator():
    return _orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — start and stop the orchestrator."""
    global _orchestrator
    from agent_os.main import create_orchestrator
    from agent_os.debug import DebugTracer, set_tracer

    config = Config()

    # Initialize authentication — generates or loads the bearer token
    init_auth(config.data_dir)

    # Initialize debug tracer (works whether started via main() or uvicorn)
    tracer = DebugTracer(enabled=config.debug, logs_dir=config.logs_dir)
    set_tracer(tracer)
    if config.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("Debug mode ON — logs at %s", config.logs_dir)

    _orchestrator = await create_orchestrator(config)
    await _orchestrator.start()
    yield
    await _orchestrator.stop()
    tracer.close()


# Auth dependency list — applied to every router that needs protection.
_AUTH = [Depends(require_token)]


def create_app(orchestrator=None) -> FastAPI:
    """Create the FastAPI application."""
    global _orchestrator
    if orchestrator:
        _orchestrator = orchestrator

    app = FastAPI(
        title="Agent OS",
        version="0.1.0",
        lifespan=lifespan if not orchestrator else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # Register routes — all protected by bearer-token auth
    from agent_os.api.routes import chat, tasks, permissions, settings, skills, sessions, oauth, files
    app.include_router(chat.router, prefix="/api", dependencies=_AUTH)
    # WebSocket router — no header-based auth (browsers can't send
    # Authorization headers on WS upgrades).  Auth is done via query
    # param inside the handler.
    app.include_router(chat.ws_router, prefix="/api")
    app.include_router(tasks.router, prefix="/api", dependencies=_AUTH)
    app.include_router(permissions.router, prefix="/api", dependencies=_AUTH)
    app.include_router(settings.router, prefix="/api", dependencies=_AUTH)
    app.include_router(skills.router, prefix="/api", dependencies=_AUTH)
    app.include_router(sessions.router, prefix="/api", dependencies=_AUTH)
    app.include_router(files.router, prefix="/api", dependencies=_AUTH)

    # OAuth router — the /callback endpoint is a browser redirect target
    # from external providers and can't carry a bearer token, so the
    # entire OAuth router is unauthenticated.  The routes only perform
    # server-side operations (token exchange) gated by a CSRF-safe
    # state parameter.
    app.include_router(oauth.router, prefix="/api")

    # Health check — unauthenticated so the frontend can verify connectivity
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    # Token bootstrap — the frontend fetches this to get the bearer token.
    # Unauthenticated by design (chicken-and-egg: you need the token to
    # authenticate, but you need this endpoint to get the token).
    # Restricted to localhost connections only.
    from starlette.requests import Request
    from agent_os.api.auth import get_token

    _LOCALHOST = {"127.0.0.1", "::1", "localhost"}

    @app.get("/api/auth/token")
    async def auth_token(request: Request):
        client_host = request.client.host if request.client else ""
        if client_host not in _LOCALHOST:
            raise HTTPException(403, "Token endpoint is only available from localhost")
        return {"token": get_token()}

    return app
