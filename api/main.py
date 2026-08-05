"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import ops, rollout
from api import terminal_ws

# 终端 HTML 页面路由（xterm.js）
from fastapi.responses import HTMLResponse
from pathlib import Path as _Path


@terminal_ws.router.get("/terminal", response_class=HTMLResponse, include_in_schema=False)
async def terminal_page():
    """返回 xterm.js 终端 HTML 页面（独立浏览器标签打开）。"""
    html_path = _Path(__file__).parent / "terminal_page.html"
    return HTMLResponse(content=html_path.read_text(), status_code=200)



@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[启动] AI Ops Assistant API 已就绪")
    yield
    print("[关闭] 服务已退出")


app = FastAPI(
    title="AI Ops Assistant",
    description="自然语言运维助手 API — 解析 → 生成脚本 → SSH 执行 → 验证",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ops.router)
app.include_router(rollout.router)
app.include_router(terminal_ws.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-ops-assistant"}
