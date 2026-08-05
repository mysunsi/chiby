"""FastAPI 应用工厂（可被 uvicorn 加载）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from remediator.api.endpoints import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Remediation API",
        version="1.0.0",
        description="AI-powered command auto-remediation service（封装 remediator.core.executor_wrapper）",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "remediator-remediation-api"}

    return app


app = create_app()
