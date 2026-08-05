"""ChibyTerm（赤壁终端）— 类 SSH 交互式 AI 运维终端。

架构：
  [Web Terminal UI (xterm.js)]
          ↕ WebSocket
  [FastAPI /api/terminal]        ← WebSocket + REST
          ↕
  [Session Manager]              ← 多会话生命周期管理
          ↕
  [LLM Shell]                    ← 自然语言 → Shell 命令
          ↕
  [SSH Executor / Local PTY]     ← 实际命令执行
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

# ── 路径配置 ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
try:
    from chibycore.repo_root import find_repo_root

    PROJECT_ROOT = find_repo_root()
except Exception:
    # 已安装 wheel：无仓库根时回落用户工作目录（data/ 可经环境变量另配）
    PROJECT_ROOT = Path.cwd()

from chibyterm.models import (
    ClosureExecuteBody,
    ClosureExecuteResponse,
    ClosureStepResponse,
    ConnType,
    Host,
    HostCreate,
    HostListResponse,
    HostTestConnectionRequest,
    HostTestConnectionResponse,
    HostUpdate,
    LLMConfigUpdate,
    PromptResult,
    SessionCreate,
    SessionStatus,
    SessionUpdate,
    TerminalSession,
)
from chibycore.llm_config import (
    default_llm_config_path,
    get_effective_llm_settings,
    load_json_config,
    save_json_config,
    settings_for_api_response,
)
from chibyterm.session_manager import get_session_manager, SessionManager
from chibyterm.llm_shell import LLMPromptProcessor, classify_command_risk
from chibyterm.command_aggregate import enrich_llm_plan_payload
from chibyterm.plan_state import PlanRuntime, new_plan_id
from chibyterm.shell_context import (
    ALLOWED_TARGET_OS,
    build_llm_runtime_hint,
    resolve_shell_profile,
    session_meta_payload,
)
from chibyterm.plan_error_nl import merge_nl_payload
from chibyterm.ai_stream import stream_llm_text_chunks, stream_plan_preview_text
from chibycore.knowledge_hub import (
    KnowledgeHubStorage,
    KnowledgeHubSearch,
    SearchQuery,
    knowledge_hub_router,
)
from chibycore.knowledge_hub.models import SearchResponse
from chibycore.doc_hub import doc_hub_router
from chibycore.winrm_oneshot import parse_ps_exit_marker_codes, strip_ps_exit_marker_lines

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── 全局单例 ────────────────────────────────────────────────────────────────
session_mgr: SessionManager = None
prompt_processor: LLMPromptProcessor = None

# ── 主机存储（内存 + 持久化）───────────────────────────────────────────────

_HOST_STORE: Dict[str, Host] = {}


_HOST_STORE_FILE = PROJECT_ROOT / "data" / "hosts.json"


def _load_hosts():
    """从 JSON 文件加载主机配置。"""
    global _HOST_STORE
    from chibycore.host_crypto import decrypt_host_dict

    if not _HOST_STORE_FILE.exists():
        return
    try:
        data = json.loads(_HOST_STORE_FILE.read_text())
        for h in data.get("hosts", []):
            try:
                host = Host(**decrypt_host_dict(h))
                _HOST_STORE[host.id] = host
            except Exception as e:
                logger.warning(f"跳过无效主机记录 {h.get('id', '?')}: {e}")
    except Exception as e:
        logger.warning(f"无法加载主机配置: {e}")


def _persist_hosts():
    """持久化主机到 JSON 文件。写入前先复制一份 .bak，避免异常覆盖后无法找回。"""
    try:
        _HOST_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _HOST_STORE_FILE.exists() and _HOST_STORE_FILE.stat().st_size > 0:
            bak = _HOST_STORE_FILE.with_suffix(".json.bak")
            try:
                shutil.copy2(_HOST_STORE_FILE, bak)
            except OSError as e:
                logger.debug(f"主机列表备份跳过: {e}")
        from chibycore.host_crypto import encrypt_host_dict

        data = {
            "hosts": [
                encrypt_host_dict(h.model_dump(mode="json"))
                for h in _HOST_STORE.values()
            ]
        }
        _HOST_STORE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning(f"无法持久化主机配置: {e}")


def _save_host(host: Host) -> Host:
    """保存主机到存储。"""
    _HOST_STORE[host.id] = host
    _persist_hosts()
    return host


def _delete_host(host_id: str):
    """删除主机。"""
    if host_id in _HOST_STORE:
        del _HOST_STORE[host_id]
        _persist_hosts()


# ── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global session_mgr, prompt_processor
    logger.info("ChibyTerm 启动中...")
    session_mgr = get_session_manager()
    prompt_processor = LLMPromptProcessor()
    _load_hosts()
    try:
        from chibycore.custom_chains import register_custom_chains

        n = register_custom_chains(PROJECT_ROOT)
        if n:
            logger.info("自定义任务链条目: %s", n)
    except Exception as e:
        logger.warning("自定义任务链加载跳过: %s", e)
    logger.info(f"已加载 {len(_HOST_STORE)} 台主机")
    # 初始化 KnowledgeHub 存储
    try:
        kh_storage = KnowledgeHubStorage.get_instance()
        stats = kh_storage.get_stats()
        logger.info(f"KnowledgeHub 就绪: {stats['kb_entries']} KB / {stats['script_entries']} 脚本 / {stats['best_practices']} 最佳实践")
    except Exception as e:
        logger.warning(f"KnowledgeHub 初始化失败: {e}")
    # Hermes Home：仅当桥路由开关打开且闭源桥已安装时引导（P1）
    try:
        from chibyterm.oss_plugin_flags import hermes_bridge_routes_enabled

        if hermes_bridge_routes_enabled():
            try:
                import importlib

                _hb_cfg = importlib.import_module("chiby_hermes_bridge.config")
                load_hermes_bridge_config = _hb_cfg.load_hermes_bridge_config
            except ImportError:
                logger.info("未安装 chiby_hermes_bridge，跳过 Hermes Home 引导")
            else:
                from chibycore.hermes_llm_sync import bootstrap_hermes_from_assistant_llm

                if load_hermes_bridge_config().enabled:
                    hs = bootstrap_hermes_from_assistant_llm()
                    if hs.get("ok"):
                        logger.info(
                            "Hermes Home 已就绪: %s (bootstrapped=%s)",
                            hs.get("hermes_home"),
                            hs.get("bootstrapped"),
                        )
                        if hs.get("warning"):
                            logger.warning("Hermes LLM 同步提示: %s", hs["warning"])
                    else:
                        logger.warning("Hermes Home 引导未完成: %s", hs)
        else:
            logger.info("Hermes 桥未启用（OPS_HERMES_BRIDGE / hermes_bridge.enabled），跳过 Home 引导")
    except Exception as e:
        logger.warning("Hermes Home 自动引导跳过: %s", e)
    sched_task = asyncio.create_task(_broadcast_schedule_loop())
    try:
        yield
    finally:
        sched_task.cancel()
        try:
            await sched_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    logger.info("ChibyTerm 关闭中...")
    # 清理所有活跃会话
    for sid in list(session_mgr.list_sessions()):
        await session_mgr._close_session_async(sid.id)


# ── FastAPI App ────────────────────────────────────────────────────────────

app = FastAPI(
    title="ChibyTerm",
    description="赤壁终端 — 类 SSH 交互式 AI 运维终端",
    version="0.1.2",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── UI 登录（默认 admin/admin；OPS_UI_AUTH=0 可关闭）─────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from chibyterm.ui_auth import (
    COOKIE_NAME as UI_AUTH_COOKIE,
    change_password as ui_change_password,
    create_session as ui_create_session,
    destroy_session as ui_destroy_session,
    session_user as ui_session_user,
    ui_auth_enabled,
    verify_login as ui_verify_login,
)

_UI_AUTH_PUBLIC_PREFIXES = (
    "/api/ui/login",
    "/api/ui/auth/status",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/web-assets/",
    "/favicon.ico",
)


class _UiAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if not ui_auth_enabled():
            return await call_next(request)
        path = request.url.path or "/"
        if request.method == "OPTIONS":
            return await call_next(request)
        if path == "/" or path.startswith("/t/") or path.startswith("/demo/"):
            return await call_next(request)
        if any(path == p or path.startswith(p) for p in _UI_AUTH_PUBLIC_PREFIXES):
            return await call_next(request)
        # WebSocket 在端点内校验 cookie（BaseHTTPMiddleware 不适合拦截 WS 升级）
        if path.startswith("/api/"):
            user = ui_session_user(request.cookies.get(UI_AUTH_COOKIE))
            if not user:
                return JSONResponse({"detail": "未登录", "error": "auth_required"}, status_code=401)
        return await call_next(request)


app.add_middleware(_UiAuthMiddleware)


@app.get("/api/ui/auth/status")
async def ui_auth_status(request: Request):
    enabled = ui_auth_enabled()
    user = ui_session_user(request.cookies.get(UI_AUTH_COOKIE)) if enabled else "admin"
    return {
        "enabled": enabled,
        "authenticated": bool(user) if enabled else True,
        "username": user or "",
    }


class _UiLoginBody(BaseModel):
    username: str = ""
    password: str = ""


class _UiChangePasswordBody(BaseModel):
    username: str = ""
    current_password: str = ""
    new_password: str = ""


@app.post("/api/ui/login")
async def ui_login(body: _UiLoginBody):
    from fastapi.responses import JSONResponse as _JR

    if not ui_auth_enabled():
        resp = _JR({"ok": True, "username": "admin", "enabled": False})
        return resp
    user = (body.username or "").strip()
    if not ui_verify_login(PROJECT_ROOT, user, body.password or ""):
        return _JR({"ok": False, "detail": "用户名或密码错误"}, status_code=401)
    token = ui_create_session(user)
    resp = _JR({"ok": True, "username": user, "enabled": True})
    resp.set_cookie(
        key=UI_AUTH_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=7 * 24 * 3600,
    )
    return resp


@app.post("/api/ui/logout")
async def ui_logout(request: Request):
    from fastapi.responses import JSONResponse as _JR

    ui_destroy_session(request.cookies.get(UI_AUTH_COOKIE))
    resp = _JR({"ok": True})
    resp.delete_cookie(UI_AUTH_COOKIE, path="/")
    return resp


@app.post("/api/ui/change-password")
async def ui_change_password_api(body: _UiChangePasswordBody):
    from fastapi.responses import JSONResponse as _JR

    if not ui_auth_enabled():
        return _JR({"ok": False, "detail": "UI 登录已关闭（OPS_UI_AUTH=0）"}, status_code=400)
    ok, msg = ui_change_password(
        PROJECT_ROOT,
        (body.username or "").strip(),
        body.current_password or "",
        body.new_password or "",
    )
    if not ok:
        return _JR({"ok": False, "detail": msg}, status_code=400)
    resp = _JR({"ok": True, "detail": msg})
    resp.delete_cookie(UI_AUTH_COOKIE, path="/")
    return resp


@app.get("/api/ui/version")
async def ui_version_api():
    """本机已装版本（无网络），供「关于」页。"""
    from chibyterm.update_check import local_version_info

    return local_version_info()


@app.get("/api/ui/update-check")
async def ui_update_check_api():
    """查询 TestPyPI/PyPI 最新版并与本机对比；不执行 pip。"""
    import asyncio

    from chibyterm.update_check import check_for_update

    return await asyncio.to_thread(check_for_update)


@app.get("/api/broadcast/settings")
async def get_broadcast_settings_api():
    """群发设置：汇报口吻等。"""
    from chibyterm.broadcast_settings import (
        list_tone_options,
        load_broadcast_settings,
    )

    cfg = load_broadcast_settings()
    return {
        **cfg,
        "tones": list_tone_options("zh-CN"),
        "tones_en": list_tone_options("en"),
        "tones_zh_tw": list_tone_options("zh-TW"),
    }


@app.put("/api/broadcast/settings")
async def put_broadcast_settings_api(body: Dict[str, Any]):
    """保存群发设置（如 report_tone）。"""
    from chibyterm.broadcast_settings import (
        list_tone_options,
        save_broadcast_settings,
    )

    cfg = save_broadcast_settings(body if isinstance(body, dict) else {})
    return {
        **cfg,
        "tones": list_tone_options("zh-CN"),
        "ok": True,
    }


class BroadcastReportBody(BaseModel):
    report_tone: Optional[str] = None


@app.post("/api/broadcast/{job_id}/report")
async def post_broadcast_report_api(job_id: str, body: Optional[BroadcastReportBody] = None):
    """按需生成总体分析报告。"""
    from fastapi import HTTPException

    tone = body.report_tone if body else None
    out = await _generate_broadcast_report_for_job(
        job_id=job_id, report_tone=tone, push_ws=True
    )
    if not out.get("ok"):
        err = out.get("error") or "failed"
        code = 404 if err == "job_not_found" else 409
        raise HTTPException(status_code=code, detail=err)
    return out


@app.get("/api/broadcast/schedules")
async def list_broadcast_schedules_api():
    from chibyterm.broadcast_schedule import load_schedules

    return {"schedules": load_schedules()}


@app.get("/api/broadcast/knowledge-hints")
async def list_broadcast_knowledge_hints_api():
    """定时任务连续异常 → 知识沉淀提示。"""
    from chibyterm.broadcast_schedule import list_knowledge_hints

    return {"ok": True, "hints": list_knowledge_hints()}


@app.post("/api/broadcast/knowledge-hints/{schedule_id}/dismiss")
async def dismiss_broadcast_knowledge_hint_api(schedule_id: str):
    from fastapi import HTTPException

    from chibyterm.broadcast_schedule import dismiss_knowledge_hint

    if not dismiss_knowledge_hint(schedule_id):
        raise HTTPException(status_code=404, detail="hint not found")
    return {"ok": True}


@app.post("/api/broadcast/schedules")
async def create_broadcast_schedule_api(body: Dict[str, Any]):
    from chibyterm.broadcast_schedule import create_schedule

    item = create_schedule(body if isinstance(body, dict) else {})
    return {"ok": True, "schedule": item}


@app.put("/api/broadcast/schedules/{schedule_id}")
async def update_broadcast_schedule_api(schedule_id: str, body: Dict[str, Any]):
    from fastapi import HTTPException

    from chibyterm.broadcast_schedule import update_schedule

    item = update_schedule(schedule_id, body if isinstance(body, dict) else {})
    if not item:
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"ok": True, "schedule": item}


@app.delete("/api/broadcast/schedules/{schedule_id}")
async def delete_broadcast_schedule_api(schedule_id: str):
    from fastapi import HTTPException

    from chibyterm.broadcast_schedule import delete_schedule

    if not delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"ok": True}


class BroadcastNlPreviewBody(BaseModel):
    """Fleet：自然语言意图 → 按已打开会话或主机目录翻译命令预览。"""

    nl_intent: str = ""
    session_ids: Optional[List[str]] = None
    # 若提供 host_ids 且未提供可用 session_ids：按主机目录 oneshot 预览（不强制开 Tab）
    host_ids: Optional[List[str]] = None
    # 默认按 host_id 去重；为 True 时同主机多 Tab 只保留一个（仅 session 模式）
    dedupe_hosts: bool = True
    # 同主机多 Tab 时优先保留该会话（通常为当前聚焦 Tab）
    preferred_session_id: Optional[str] = None
    # session | oneshot；缺省时：有 host_ids 无 session → oneshot
    execution_mode: Optional[str] = None


@app.post("/api/broadcast/nl-preview")
async def broadcast_nl_preview_api(body: BroadcastNlPreviewBody):
    """按会话或主机目录分段翻译 NL，供 Fleet 模式确认后下发。"""
    from fastapi import HTTPException

    from chibyterm.broadcast_nl import build_fleet_preview, build_fleet_preview_from_hosts

    intent = (body.nl_intent or "").strip()
    if not intent:
        raise HTTPException(status_code=400, detail="nl_intent required")

    ids = [x for x in list(body.session_ids or []) if session_mgr.get_session(x)]
    want_hosts = [str(x).strip() for x in (body.host_ids or []) if str(x).strip()]
    mode_hint = str(body.execution_mode or "").strip().lower()
    use_oneshot = mode_hint == "oneshot" or (bool(want_hosts) and not ids)

    pp = prompt_processor
    if pp is None:
        raise HTTPException(status_code=503, detail="LLM PromptProcessor 未就绪")

    def _process_nl(
        text: str,
        *,
        shell_profile: str = "unix",
        runtime_hint: str = "",
        ui_locale: str = "zh-CN",
    ):
        return pp.process(
            text,
            shell_profile=shell_profile,
            runtime_hint=runtime_hint,
            ui_locale=ui_locale,
        )

    if use_oneshot:
        if not want_hosts:
            raise HTTPException(status_code=400, detail="host_ids required for oneshot preview")
        ui_locale = "zh-CN"
        # 若有任一打开会话，取其 UI 语言
        open_sids = [s.id for s in session_mgr.list_sessions()]
        if open_sids:
            ui_locale = session_mgr.get_ui_locale(open_sids[0]) or "zh-CN"
        preview = await asyncio.to_thread(
            build_fleet_preview_from_hosts,
            nl_intent=intent,
            host_ids=want_hosts,
            host_store=_HOST_STORE,
            process_nl=_process_nl,
            ui_locale=ui_locale,
        )
        return preview.to_api_dict()

    # —— 以下：经打开终端的 session 模式（含「仅已打开 Tab」）——
    if want_hosts:
        # 兼容旧行为：若同时给了 host_ids 与 session，按 host 从已打开会话中挑
        by_host: Dict[str, List[str]] = {}
        for s in session_mgr.list_sessions():
            hid = str(getattr(s, "host_id", None) or "").strip()
            if hid:
                by_host.setdefault(hid, []).append(s.id)
        missing = [h for h in want_hosts if h not in by_host]
        if missing:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "sessions_required",
                    "missing_host_ids": missing,
                    "message": "请先为所选主机打开终端会话，或改用范围选机（oneshot，无需开 Tab）",
                },
            )
        pref = (body.preferred_session_id or "").strip()
        picked: List[str] = []
        for h in want_hosts:
            cands = by_host.get(h) or []
            if pref and pref in cands:
                picked.append(pref)
            elif ids:
                hit = next((x for x in ids if x in cands), None)
                picked.append(hit or cands[0])
            else:
                picked.append(cands[0])
        ids = picked

    if not ids:
        ids = [s.id for s in session_mgr.list_sessions()]
    ids = [x for x in ids if session_mgr.get_session(x)]
    if not ids:
        raise HTTPException(status_code=400, detail="no open sessions")

    ui_locale = session_mgr.get_ui_locale(ids[0]) if ids else "zh-CN"

    preferred = (body.preferred_session_id or "").strip() or None
    if preferred and preferred not in ids:
        preferred = None

    preview = await asyncio.to_thread(
        build_fleet_preview,
        nl_intent=intent,
        session_ids=ids,
        get_session=session_mgr.get_session,
        host_label_fn=_broadcast_host_label,
        runtime_hint_fn=_runtime_hint_for_session,
        shell_profile_fn=lambda sess: resolve_shell_profile(sess).value,
        process_nl=_process_nl,
        ui_locale=ui_locale,
        host_store=_HOST_STORE,
        dedupe_hosts=bool(body.dedupe_hosts),
        preferred_session_id=preferred,
    )
    return preview.to_api_dict()


@app.get("/api/broadcast/{job_id}")
async def get_broadcast_job_api(job_id: str):
    """调试用：查询最近一次群发 job 状态（进程内存）。"""
    from fastapi import HTTPException

    from chibyterm.broadcast_report import get_broadcast_job, job_to_api_dict

    # 避免与 schedules 等静态路径冲突（若路由顺序异常）
    if job_id in ("settings", "schedules", "nl-preview"):
        raise HTTPException(status_code=404, detail="not found")
    job = get_broadcast_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="broadcast job not found")
    return job_to_api_dict(job)


def _register_optional_proprietary_plugins(application: FastAPI) -> None:
    """委托给 proprietary_plugins，保持 main 对闭源包名零感知（P0-5）。"""
    from chibyterm.proprietary_plugins import (
        register_optional_proprietary_plugins as _register,
    )

    _register(application, host_store=_HOST_STORE)


_register_optional_proprietary_plugins(app)

from chibyterm.closure_governance_routes import register_closure_governance_routes

register_closure_governance_routes(
    app,
    host_store=_HOST_STORE,
    get_prompt_processor=lambda: prompt_processor,  # lifespan 赋值后的全局
)


# ═══════════════════════════════════════════════════════════════════════════
#  静态文件 / 前端
# ═══════════════════════════════════════════════════════════════════════════

import mimetypes

from fastapi.staticfiles import StaticFiles

# Windows 默认把 .mjs 标成 text/plain，浏览器会拒绝 ES module
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")

app.mount(
    "/web-assets",
    StaticFiles(directory=str(BASE_DIR / "web")),
    name="web_assets",
)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    svg = BASE_DIR / "web" / "favicon.svg"
    if svg.is_file():
        return FileResponse(svg, media_type="image/svg+xml")
    # 无图标文件时勿 500，返回空 204
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse(BASE_DIR / "web" / "index.html")


@app.get("/terminal", response_class=HTMLResponse)
async def terminal_page():
    return FileResponse(BASE_DIR / "web" / "index.html")


@app.get("/demo/knowledge-hub", response_class=HTMLResponse)
async def knowledge_hub_page():
    """本地知识库 CRUD 管理页（对接 /api/kb）。"""
    return FileResponse(
        BASE_DIR / "web" / "knowledge_hub.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/demo/doc-hub", response_class=HTMLResponse)
async def doc_hub_page():
    """企业文档向量库管理页（对接 /api/docs）。"""
    return FileResponse(
        BASE_DIR / "web" / "doc_hub.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/demo/tools-marketplace", response_class=HTMLResponse)
async def tools_marketplace_page():
    """工具市场预览页（官方 + tools/contrib 登记清单）。"""
    return FileResponse(
        BASE_DIR / "web" / "tools_marketplace.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/tools/catalog")
async def tools_catalog_api(
    pack: str = "",
    type: str = "",
    loaded_only: bool = False,
    q: str = "",
):
    """工具目录 JSON：供市场页与集成方读取（展示用，非运行时白名单源）。

    查询参数（Phase 6）：
    - pack: skill_pack / category
    - type: local_readonly | host_readonly | host_write | host_command …
    - loaded_only: 仅已加载插件（对 plugins 生效；official/community 无 loaded 时会被滤掉）
    - q: 关键词（id/title/summary/version…）
    """
    from chibyterm.tools_catalog import build_tools_catalog, filter_catalog

    catalog = build_tools_catalog()
    if pack or type or loaded_only or q:
        return filter_catalog(
            catalog, pack=pack, tool_type=type, loaded_only=loaded_only, q=q
        )
    return catalog


@app.get("/api/tools/packs")
async def tools_packs_api():
    """技能包聚合列表（按插件 skill_pack）。"""
    from chibyterm.tools_catalog import build_tools_catalog

    cat = build_tools_catalog()
    return {
        "ok": True,
        "phase": 6,
        "pack_count": cat.get("pack_count", 0),
        "packs": cat.get("packs") or [],
    }


@app.get("/api/tools/plugins/{tool_id}")
async def tools_plugin_detail_api(tool_id: str):
    """单插件详情（版本 / 依赖 / 参数）。"""
    from chibyterm.tools_plugin_loader import get_plugin_detail

    detail = get_plugin_detail(tool_id)
    if detail is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"plugin not found: {tool_id}")
    return {"ok": True, "phase": 6, "plugin": detail}


# ═══════════════════════════════════════════════════════════════════════════
#  主机管理 REST API
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/hosts", response_model=HostListResponse)
async def list_hosts(
    page: Optional[int] = Query(
        None,
        ge=1,
        description="页码（从 1 起）。不传则返回过滤后的全量（兼容旧调用方）",
    ),
    size: Optional[int] = Query(
        None,
        ge=1,
        le=100,
        description="每页大小，默认 20，最大 100；仅当传 page 时生效",
    ),
    q: str = Query("", description="模糊匹配 name / host / id"),
    tag: str = Query("", description="精确匹配 tags 中某一项（大小写不敏感）"),
    label: str = Query("", description="精确匹配 labels，格式 key=value"),
    status: str = Query(
        "",
        description="精确匹配 status：online|offline|busy|unknown",
    ),
    prefer_ids: str = Query(
        "",
        description="逗号分隔的主机 id；过滤后分页前将这些主机置顶（Fleet/分组已选）",
    ),
):
    """列出主机：支持 q/tag/label/status 过滤；可选 page/size 分页。"""
    from fastapi import HTTPException

    from chibyterm.host_groups import _VALID_STATUS
    from chibyterm.host_query import host_list_payload, parse_id_list, parse_label_kv

    if label.strip():
        try:
            parse_label_kv(label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if status.strip():
        st = status.strip().lower()
        if st not in _VALID_STATUS:
            raise HTTPException(
                status_code=400,
                detail=f"status 须为 {', '.join(sorted(_VALID_STATUS))} 之一",
            )

    payload = host_list_payload(
        list(_HOST_STORE.values()),
        page=page,
        size=size if page is not None else None,
        q=q,
        tag=tag,
        label=label,
        status=status,
        prefer_ids=parse_id_list(prefer_ids),
    )
    return HostListResponse(**payload)


@app.post("/api/hosts", response_model=Host)
async def create_host(host_data: HostCreate):
    """添加新主机。"""
    import uuid

    from chibyterm.host_groups import normalize_host_status, normalize_labels, normalize_tags

    ct = host_data.conn_type
    try:
        conn_type = ConnType(ct) if ct in ("ssh", "winrm") else ConnType.SSH
    except ValueError:
        conn_type = ConnType.SSH

    host = Host(
        id=str(uuid.uuid4())[:8],
        name=host_data.name,
        host=host_data.host,
        port=host_data.port,
        username=host_data.username,
        password=host_data.password,
        description=host_data.description,
        tags=normalize_tags(host_data.tags),
        labels=normalize_labels(host_data.labels),
        status=normalize_host_status(getattr(host_data, "status", None)),
        conn_type=conn_type,
        winrm_port=host_data.winrm_port,
        winrm_use_ssl=host_data.winrm_use_ssl,
        winrm_transport=host_data.winrm_transport or "ntlm",
        winrm_server_cert_validation=host_data.winrm_server_cert_validation or "ignore",
        winrm_shell_mode=host_data.winrm_shell_mode or "interactive",
        ssh_private_key_path=host_data.ssh_private_key_path,
        ssh_private_key_passphrase=host_data.ssh_private_key_passphrase,
    )
    _save_host(host)
    return host


@app.post("/api/hosts/test-connection", response_model=HostTestConnectionResponse)
async def test_host_connection(body: HostTestConnectionRequest):
    """用表单凭据（或已存主机密码）做一次轻量连通性探测，不创建会话。"""
    import asyncio
    import time

    from fastapi import HTTPException

    from chibycore.executor_contract import RunOptions
    from chibycore.unified_executor_factory import build_oneshot_from_host_kwargs

    ct = (body.conn_type or "ssh").strip().lower()
    if ct not in ("ssh", "winrm"):
        raise HTTPException(400, "conn_type 须为 ssh 或 winrm")
    addr = (body.host or "").strip()
    user = (body.username or "").strip()
    if not addr or not user:
        raise HTTPException(400, "请填写地址与用户名")

    password = body.password
    key_path = (body.ssh_private_key_path or "").strip() or None
    key_pass = body.ssh_private_key_passphrase
    hid = (body.host_id or "").strip()
    if hid and hid in _HOST_STORE:
        old = _HOST_STORE[hid]
        if password in (None, ""):
            password = old.password
        if not key_path and old.ssh_private_key_path:
            key_path = old.ssh_private_key_path
        if key_pass in (None, "") and old.ssh_private_key_passphrase:
            key_pass = old.ssh_private_key_passphrase

    winrm_port = int(body.winrm_port or 5985)
    if body.winrm_use_ssl and winrm_port in (5985, 0):
        winrm_port = 5986

    def _probe() -> HostTestConnectionResponse:
        t0 = time.perf_counter()
        ex = None
        try:
            ex = build_oneshot_from_host_kwargs(
                conn_type=ct,
                host=addr,
                port=int(body.port or 22),
                username=user,
                password=password,
                ssh_private_key_path=key_path if ct == "ssh" else None,
                ssh_private_key_passphrase=key_pass if ct == "ssh" else None,
                winrm_port=winrm_port,
                winrm_use_ssl=bool(body.winrm_use_ssl),
                winrm_transport=body.winrm_transport or "ntlm",
                winrm_server_cert_validation=body.winrm_server_cert_validation or "ignore",
            )
            ex.connect()
            cmd = "$env:COMPUTERNAME" if ct == "winrm" else "echo ok"
            result = ex.run_command(cmd, RunOptions(timeout_sec=20.0))
            ms = (time.perf_counter() - t0) * 1000.0
            ok = bool(getattr(result, "success", False))
            err = (
                (getattr(result, "error_summary", None) or "").strip()
                or (getattr(result, "stderr", None) or "").strip()
            )
            if not ok:
                return HostTestConnectionResponse(
                    ok=False,
                    message="连接失败",
                    detail=(err or f"exit_code={getattr(result, 'exit_code', None)}")[:400],
                    latency_ms=round(ms, 1),
                )
            return HostTestConnectionResponse(
                ok=True,
                message="连接成功",
                detail=(getattr(result, "stdout", None) or "").strip()[:120],
                latency_ms=round(ms, 1),
            )
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000.0
            return HostTestConnectionResponse(
                ok=False,
                message="连接失败",
                detail=str(e)[:400],
                latency_ms=round(ms, 1),
            )
        finally:
            if ex is not None:
                try:
                    ex.close()
                except Exception:
                    pass

    result = await asyncio.to_thread(_probe)
    if hid and hid in _HOST_STORE:
        try:
            st = "online" if result.ok else "offline"
            h = _HOST_STORE[hid]
            _HOST_STORE[hid] = h.model_copy(update={"status": st})
            _persist_hosts()
        except Exception as exc:
            logger.debug("update host status after probe: %s", exc)
    return result


@app.get("/api/hosts/{host_id}", response_model=Host)
async def get_host(host_id: str):
    """获取单个主机。"""
    if host_id not in _HOST_STORE:
        from fastapi import HTTPException
        raise HTTPException(404, "主机不存在")
    return _HOST_STORE[host_id]


@app.put("/api/hosts/{host_id}", response_model=Host)
async def update_host(host_id: str, body: HostUpdate):
    """更新已存在主机的配置。"""
    from fastapi import HTTPException

    from chibyterm.host_groups import normalize_host_status, normalize_labels, normalize_tags

    if host_id not in _HOST_STORE:
        raise HTTPException(404, "主机不存在")
    old = _HOST_STORE[host_id]
    patch = body.model_dump(exclude_unset=True)

    if "conn_type" in patch and isinstance(patch["conn_type"], str):
        ct = patch["conn_type"]
        patch["conn_type"] = ConnType(ct) if ct in ("ssh", "winrm") else old.conn_type

    # 未提交 password 或空字符串：保留原密码
    if "password" not in patch or patch.get("password") in (None, ""):
        patch.pop("password", None)
    if "ssh_private_key_passphrase" not in patch or patch.get("ssh_private_key_passphrase") in (None, ""):
        patch.pop("ssh_private_key_passphrase", None)
    if patch.get("ssh_private_key_path") == "":
        patch["ssh_private_key_path"] = None
    if "tags" in patch:
        patch["tags"] = normalize_tags(patch.get("tags"))
    if "labels" in patch:
        patch["labels"] = normalize_labels(patch.get("labels"))
    if "status" in patch:
        patch["status"] = normalize_host_status(patch.get("status"))

    try:
        updated = old.model_copy(update=patch)
    except Exception as e:
        raise HTTPException(400, f"更新数据无效: {e}") from e

    _HOST_STORE[host_id] = updated
    _persist_hosts()
    return updated


@app.delete("/api/hosts/{host_id}")
async def delete_host(host_id: str):
    """删除主机。"""
    if host_id not in _HOST_STORE:
        from fastapi import HTTPException
        raise HTTPException(404, "主机不存在")
    del _HOST_STORE[host_id]
    _delete_host(host_id)
    try:
        from chibyterm.host_groups import remove_host_from_all_groups

        remove_host_from_all_groups(host_id)
    except Exception as exc:
        logger.warning("级联剔除主机组失败 host=%s: %s", host_id, exc)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
#  静态主机组（Fleet 范围选机）
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/api/host-groups")
async def list_host_groups_api():
    from chibyterm.host_groups import load_groups

    return {"groups": load_groups()}


@app.post("/api/host-groups")
async def create_host_group_api(body: Dict[str, Any]):
    from fastapi import HTTPException

    from chibyterm.host_groups import create_group

    name = str((body or {}).get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    return create_group(body or {})


@app.put("/api/host-groups/{group_id}")
async def update_host_group_api(group_id: str, body: Dict[str, Any]):
    from fastapi import HTTPException

    from chibyterm.host_groups import update_group

    g = update_group(group_id, body or {})
    if not g:
        raise HTTPException(404, "group not found")
    return g


@app.delete("/api/host-groups/{group_id}")
async def delete_host_group_api(group_id: str):
    from fastapi import HTTPException

    from chibyterm.host_groups import delete_group

    if not delete_group(group_id):
        raise HTTPException(404, "group not found")
    return {"ok": True}


@app.get("/api/host-groups/{group_id}/hosts")
async def list_host_group_hosts_api(group_id: str):
    from fastapi import HTTPException

    from chibyterm.host_groups import resolve_group_hosts

    resolved = resolve_group_hosts(
        group_id,
        known_host_ids=list(_HOST_STORE.keys()),
    )
    if not resolved.get("ok"):
        raise HTTPException(404, "group not found")
    hosts_out = []
    for hid in resolved.get("host_ids") or []:
        h = _HOST_STORE.get(hid)
        if h:
            hosts_out.append(h)
    return {
        "group": resolved.get("group"),
        "hosts": hosts_out,
        "host_ids": resolved.get("host_ids") or [],
        "skipped": resolved.get("skipped") or 0,
    }


def _distro_profile_for_session(session: Optional[TerminalSession]):
    """会话绑定主机时取已落库的发行版指纹。"""
    if session is None:
        return None
    hid = (getattr(session, "host_id", None) or "").strip()
    if not hid or hid not in _HOST_STORE:
        return None
    return getattr(_HOST_STORE[hid], "distro_profile", None)


def _distro_fix_kwargs_from_host(host) -> dict:
    """闭环修复用：从 Host.distro_profile 提取 family/pkg（WinRM 空）。"""
    if host is None:
        return {}
    ct = getattr(host, "conn_type", None)
    ct_v = getattr(ct, "value", ct)
    if str(ct_v or "").lower() == "winrm":
        return {}
    dp = getattr(host, "distro_profile", None)
    if dp is None:
        return {}
    fam = (getattr(dp, "family", None) or "").strip()
    pkg = (getattr(dp, "pkg_manager", None) or "").strip()
    out = {}
    if fam and fam != "linux_generic":
        out["distro_family"] = fam
    if pkg and pkg != "unknown":
        out["pkg_manager"] = pkg
    return out


def _runtime_hint_for_session(session: Optional[TerminalSession]) -> str:
    return build_llm_runtime_hint(session, distro_profile=_distro_profile_for_session(session))


def _apply_host_distro_profile(host_id: str, profile) -> None:
    hid = (host_id or "").strip()
    if not hid or hid not in _HOST_STORE or profile is None:
        return
    h = _HOST_STORE[hid]
    _HOST_STORE[hid] = h.model_copy(update={"distro_profile": profile})
    _persist_hosts()


async def _maybe_probe_host_distro_async(
    host_id: str,
    *,
    probe_source: str = "session_connect",
    force: bool = False,
) -> None:
    """后台探测 SSH 主机发行版并落库（失败不抛到调用方）；成功则校正会话 target_os。"""
    from chibyterm.distro_profile import needs_probe, probe_host_distro

    hid = (host_id or "").strip()
    if not hid or hid not in _HOST_STORE:
        return
    host = _HOST_STORE[hid]
    if host.conn_type != ConnType.SSH:
        return
    if not force and not needs_probe(host.distro_profile):
        # 已有指纹：仍用其校正会话 OS（无需重探）
        try:
            profile = host.distro_profile
            if profile is not None:
                changed = session_mgr.apply_host_distro_to_sessions(hid, profile)
                for sid in changed:
                    await session_mgr.push_session_meta(sid)
        except Exception as e:
            logger.debug("apply cached distro to sessions skipped: %s", e)
        return

    def _run():
        return probe_host_distro(host, probe_source=probe_source)

    try:
        loop = asyncio.get_running_loop()
        profile, _raw = await loop.run_in_executor(None, _run)
        _apply_host_distro_profile(hid, profile)
        logger.info(
            "distro_probe host_id=%s family=%s pkg=%s pretty=%s",
            hid,
            profile.family,
            profile.pkg_manager,
            (profile.pretty_name or "")[:60],
        )
        try:
            changed = session_mgr.apply_host_distro_to_sessions(hid, profile)
            for sid in changed:
                await session_mgr.push_session_meta(sid)
            if changed:
                logger.info(
                    "target_os auto-refined host_id=%s sessions=%s → %s",
                    hid,
                    ",".join(changed),
                    getattr(profile, "uname_s", "") or profile.family,
                )
        except Exception as e:
            logger.debug("refine target_os after probe skipped: %s", e)
    except Exception as e:
        logger.warning("distro_probe host_id=%s failed: %s", hid, e)


@app.post("/api/hosts/{host_id}/probe-distro")
async def probe_host_distro_api(host_id: str, force: bool = Query(True)):
    """对 SSH 主机执行只读发行版探测并写入 distro_profile。"""
    from fastapi import HTTPException

    from chibyterm.distro_profile import DistroProfile, needs_probe, probe_host_distro

    if host_id not in _HOST_STORE:
        raise HTTPException(404, "主机不存在")
    host = _HOST_STORE[host_id]
    if host.conn_type != ConnType.SSH:
        raise HTTPException(400, "仅 SSH 主机支持发行版探测")
    if not force and host.distro_profile and not needs_probe(host.distro_profile):
        return {
            "ok": True,
            "skipped": True,
            "distro_profile": host.distro_profile,
        }

    def _run():
        return probe_host_distro(host, probe_source="ssh_oneshot")

    try:
        loop = asyncio.get_running_loop()
        profile, raw = await loop.run_in_executor(None, _run)
    except Exception as e:
        raise HTTPException(500, f"探测失败: {e}") from e

    if not isinstance(profile, DistroProfile):
        raise HTTPException(500, "探测结果无效")
    _apply_host_distro_profile(host_id, profile)
    return {
        "ok": True,
        "skipped": False,
        "distro_profile": profile,
        "raw_tail": (raw or "")[-1500:],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  KnowledgeHub API
# ═══════════════════════════════════════════════════════════════════════════

app.include_router(knowledge_hub_router, prefix="/api/kb", tags=["KnowledgeHub"])
app.include_router(doc_hub_router, prefix="/api/docs", tags=["DocHub"])


@app.get("/api/audit")
async def platform_audit_list(
    limit: int = 50,
    event_type: str = "",
    user_id: str = "",
    host_id: str = "",
    trace_id: str = "",
    q: str = "",
    time_from: str = "",
    time_to: str = "",
):
    """平台统一审计查询（Fleet / AI 诊断 / 定时 / 审批 / 入库等）。"""
    from chibycore.platform_audit import event_type_counts, query_platform_audit

    lim = max(1, min(int(limit or 50), 500))
    items = query_platform_audit(
        limit=lim,
        event_type=event_type,
        user_id=user_id,
        host_id=host_id,
        trace_id=trace_id,
        q=q,
        time_from=time_from,
        time_to=time_to,
    )
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "counts": event_type_counts(limit=2000),
    }


@app.get("/api/audit/trace/{trace_id}")
async def platform_audit_trace(trace_id: str, limit: int = 200):
    """按 trace_id 串联全链路事件（时间升序）。"""
    from fastapi import HTTPException

    from chibycore.platform_audit import query_trace

    tid = (trace_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="缺少 trace_id")
    items = query_trace(tid, limit=max(1, min(int(limit or 200), 500)))
    # query 默认新→旧；时间轴展示改为升序
    items_asc = list(reversed(items))
    return {
        "ok": True,
        "trace_id": tid,
        "count": len(items_asc),
        "items": items_asc,
    }


def _closure_step_title_cn(phase: str, fix_round: int) -> str:
    """右侧卡片 / JSON：中文步骤标题。"""
    if phase == "initial":
        return "首轮执行"
    if phase == "fix":
        return f"自动修复 · 第 {fix_round} 轮" if fix_round > 0 else "自动修复"
    if phase == "goal_resume":
        return "复验原目标"
    return phase


_MAX_CLOSURE_IO_CHARS = 512 * 1024  # 单流全文上限，避免单次响应过大


def _closure_step_record_to_response(st) -> ClosureStepResponse:
    """单步 ClosureStepRecord → API 模型（与完整响应中单步结构一致）。"""
    rc = st.result.exit_code if st.result else None
    raw_so = (st.result.stdout if st.result else "") or ""
    raw_se = (st.result.stderr if st.result else "") or ""
    so_tail = raw_so[-4000:]
    se_tail = raw_se[-4000:]
    so_trunc = len(raw_so) > _MAX_CLOSURE_IO_CHARS
    se_trunc = len(raw_se) > _MAX_CLOSURE_IO_CHARS
    return ClosureStepResponse(
        phase=st.phase,
        command=st.command,
        gateway_allowed=st.gateway_allowed,
        gateway_reason=st.gateway_reason,
        exit_code=rc,
        stdout_tail=so_tail,
        stderr_tail=se_tail,
        stdout_full=raw_so[:_MAX_CLOSURE_IO_CHARS],
        stderr_full=raw_se[:_MAX_CLOSURE_IO_CHARS],
        stdout_truncated=so_trunc,
        stderr_truncated=se_trunc,
        fix_round=st.fix_round,
        step_title=_closure_step_title_cn(st.phase, st.fix_round),
        exit_ok=st.exit_ok,
        llm_judge_ok=st.llm_judge_ok,
        llm_judge_reason=(st.llm_judge_reason or "")[:2000],
        outcome_detail=(st.outcome_detail or "")[:2000],
    )


def _closure_run_result_to_response(result, trace_id: str, smode: str) -> ClosureExecuteResponse:
    """将 ClosureRunResult 转为 REST 响应。"""
    step_out: list[ClosureStepResponse] = []
    for st in result.steps:
        step_out.append(_closure_step_record_to_response(st))
    fin = result.final_payload.exit_code if result.final_payload else None
    return ClosureExecuteResponse(
        ok=result.ok,
        stop_reason=result.stop_reason,
        steps=step_out,
        final_exit_code=fin,
        trace_id=trace_id,
        success_mode=smode,
    )


# ── 命令修复时间线：cancel token + WebSocket 推送（与 SSE closure-execute 并行）────────
_REPAIR_CANCEL_EVENTS: Dict[str, threading.Event] = {}


def _repair_register_cancel(job_id: str) -> threading.Event:
    ev = threading.Event()
    _REPAIR_CANCEL_EVENTS[job_id] = ev
    return ev


def _repair_unregister_cancel(job_id: str) -> None:
    _REPAIR_CANCEL_EVENTS.pop(job_id, None)


def _repair_cancel_job(job_id: str) -> bool:
    ev = _REPAIR_CANCEL_EVENTS.get(job_id)
    if not ev:
        return False
    ev.set()
    return True


def _repair_cancel_predicate(job_id: str) -> bool:
    ev = _REPAIR_CANCEL_EVENTS.get(job_id)
    return bool(ev and ev.is_set())


def _repair_attempt_index_from_step(phase: str, fix_round: int) -> int:
    if (phase or "") == "initial":
        return 1
    return int(fix_round) + 1


def _repair_thought_stage_type(stage: str) -> str:
    s = (stage or "").strip().lower()
    if s == "generate":
        return "thought"
    if s == "execute":
        return "action"
    return "decision"


def _repair_thinking_context_line(
    stage: str,
    sr: Dict[str, Any],
    cmd: str,
    gate_ok: bool,
    success_mode: str,
) -> Optional[str]:
    from chibycore.closure_labels import format_verify_message

    st = (stage or "").strip().lower()
    if st == "generate":
        if not gate_ok:
            return None
        c = (cmd or "").strip()
        if not c:
            return None
        return "候选命令：`" + c[:480] + "`"
    if st == "execute":
        tail = (sr.get("stderr_tail") or "") or (sr.get("stdout_tail") or "")
        tail = str(tail).strip()
        return ("输出摘录：" + tail[:420]) if tail else "子进程已执行完毕"
    if st == "verify":
        vp = _repair_verify_passed_payload(sr, success_mode)
        return format_verify_message(
            passed=vp,
            exit_ok=sr.get("exit_ok") if isinstance(sr.get("exit_ok"), bool) else None,
            llm_ok=sr.get("llm_judge_ok") if isinstance(sr.get("llm_judge_ok"), bool) else None,
            success_mode=success_mode,
            judge_reason=str(sr.get("llm_judge_reason") or ""),
            outcome_detail=str(sr.get("outcome_detail") or ""),
            stderr_tail=str(sr.get("stderr_tail") or ""),
            stdout_tail=str(sr.get("stdout_tail") or ""),
        )[:460]
    return None


def _repair_verify_user_message(sr: Dict[str, Any], success_mode: str) -> str:
    """时间线验证步骤对外文案（禁止直接甩内部码）。"""
    from chibycore.closure_labels import format_verify_message

    vp = _repair_verify_passed_payload(sr, success_mode)
    return format_verify_message(
        passed=vp,
        exit_ok=sr.get("exit_ok") if isinstance(sr.get("exit_ok"), bool) else None,
        llm_ok=sr.get("llm_judge_ok") if isinstance(sr.get("llm_judge_ok"), bool) else None,
        success_mode=success_mode,
        judge_reason=str(sr.get("llm_judge_reason") or ""),
        outcome_detail=str(sr.get("outcome_detail") or ""),
        stderr_tail=str(sr.get("stderr_tail") or ""),
        stdout_tail=str(sr.get("stdout_tail") or ""),
    )[:600]


def _repair_verify_passed_payload(sr: Dict[str, Any], success_mode: str) -> bool:
    if not sr.get("gateway_allowed"):
        return False
    m = (success_mode or "exit_code").strip().lower()
    ex = sr.get("exit_ok")
    lj = sr.get("llm_judge_ok")
    if m == "exit_code":
        return ex is True
    if m == "llm":
        return lj is True
    if m == "both":
        return ex is True and lj is True
    return ex is True


def _repair_timeline_template(max_attempts: int) -> List[Dict[str, Any]]:
    n = max(1, min(10, int(max_attempts)))
    steps: List[Dict[str, Any]] = []
    steps.append({"id": 1, "name": "触发修复", "status": "idle"})
    steps.append({"id": 2, "name": "分析失败根因", "status": "idle"})
    for a in range(1, n + 1):
        base = 2 + (a - 1) * 3
        steps.append({"id": base + 1, "name": f"第{a}次尝试 - 生成命令", "status": "idle"})
        steps.append({"id": base + 2, "name": f"第{a}次尝试 - 执行命令", "status": "idle"})
        steps.append({"id": base + 3, "name": f"第{a}次尝试 - 验证结果", "status": "idle"})
    return steps


async def _repair_ws_broadcast(session_id: Optional[str], msg_type: str, data: Dict[str, Any]) -> None:
    if not session_id:
        return
    await session_mgr._broadcast(
        session_id,
        {"type": msg_type, "session_id": session_id, "data": data},
    )


async def _repair_emit_started_ws(
    session_id: str, repair_job_id: str, trace_id: str, max_fix_attempts: int
) -> None:
    steps = _repair_timeline_template(max_fix_attempts)
    for s in steps:
        if s["id"] == 1:
            s["status"] = "success"
            s["description"] = "已启动受控闭环"
        elif s["id"] == 2:
            s["status"] = "success"
            s["description"] = "已根据终端输出进入自动修复序列"
        elif s["id"] == 3:
            s["status"] = "running"
            s["description"] = "…正在准备生成命令…"
    await _repair_ws_broadcast(
        session_id,
        "repair_started",
        {"repair_job_id": repair_job_id, "trace_id": trace_id, "steps": steps},
    )


async def _repair_emit_step_ws(
    session_id: str, job_id: str, sr: Dict[str, Any], success_mode: str
) -> None:
    att = _repair_attempt_index_from_step(
        str(sr.get("phase") or ""), int(sr.get("fix_round") or 0)
    )
    cmd = (sr.get("command") or "").strip()
    gate = bool(sr.get("gateway_allowed"))
    trace = job_id
    await _repair_ws_broadcast(
        session_id,
        "repair_attempt_update",
        {
            "repair_job_id": job_id,
            "trace_id": trace,
            "attempt_index": att,
            "stage": "generate",
            "status": "success",
            "message": "已生成候选命令" if gate else "命令未过网关",
            "command": cmd,
            "thought_type": _repair_thought_stage_type("generate"),
            "thinking_context": _repair_thinking_context_line(
                "generate", sr, cmd, gate, success_mode
            ),
        },
    )
    if not gate:
        gr = str(sr.get("gateway_reason") or "网关拒绝")[:520]
        await _repair_ws_broadcast(
            session_id,
            "repair_attempt_update",
            {
                "repair_job_id": job_id,
                "trace_id": trace,
                "attempt_index": att,
                "stage": "execute",
                "status": "failed",
                "message": gr[:500],
                "thought_type": _repair_thought_stage_type("execute"),
                "thinking_context": ("网关判定：" + gr) if gr else None,
            },
        )
        await _repair_ws_broadcast(
            session_id,
            "repair_attempt_update",
            {
                "repair_job_id": job_id,
                "trace_id": trace,
                "attempt_index": att,
                "stage": "verify",
                "status": "skipped",
                "message": "—",
                "thought_type": _repair_thought_stage_type("verify"),
                "thinking_context": "本尝试未进入执行阶段",
            },
        )
    else:
        tail = (sr.get("stderr_tail") or "") or (sr.get("stdout_tail") or "")
        tail = str(tail)[:400]
        await _repair_ws_broadcast(
            session_id,
            "repair_attempt_update",
            {
                "repair_job_id": job_id,
                "trace_id": trace,
                "attempt_index": att,
                "stage": "execute",
                "status": "success",
                "message": "已执行" + (f" · {tail[:200]}" if tail else ""),
                "thought_type": _repair_thought_stage_type("execute"),
                "thinking_context": _repair_thinking_context_line(
                    "execute", sr, cmd, gate, success_mode
                ),
            },
        )
        vp = _repair_verify_passed_payload(sr, success_mode)
        await _repair_ws_broadcast(
            session_id,
            "repair_attempt_update",
            {
                "repair_job_id": job_id,
                "trace_id": trace,
                "attempt_index": att,
                "stage": "verify",
                "status": "success" if vp else "failed",
                "message": _repair_verify_user_message(sr, success_mode),
                "thought_type": _repair_thought_stage_type("verify"),
                "thinking_context": _repair_thinking_context_line(
                    "verify", sr, cmd, gate, success_mode
                ),
            },
        )
    await _repair_ws_broadcast(
        session_id,
        "repair_attempt_result",
        {
            "repair_job_id": job_id,
            "trace_id": trace,
            "attempt_index": att,
            "ok": _repair_verify_passed_payload(sr, success_mode) if gate else False,
        },
    )


async def _repair_emit_done_ws(session_id: str, job_id: str, result: Any, tid: str) -> None:
    sr_raw = str(getattr(result, "stop_reason", "") or "")
    if getattr(result, "ok", False):
        await _repair_ws_broadcast(
            session_id,
            "repair_completed",
            {"repair_job_id": job_id, "trace_id": tid, "stop_reason": sr_raw},
        )
    elif sr_raw == "user_cancelled":
        await _repair_ws_broadcast(
            session_id,
            "repair_stopped",
            {
                "repair_job_id": job_id,
                "trace_id": tid,
                "message": "您已主动停止修复流程",
            },
        )
    else:
        await _repair_ws_broadcast(
            session_id,
            "repair_exhausted",
            {"repair_job_id": job_id, "trace_id": tid, "stop_reason": sr_raw},
        )


def _closure_sse_pack(event: str, payload: dict) -> bytes:
    """SSE 单帧：event + 单行 JSON data。"""
    return (
        f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


async def _closure_execute_stream_sse(
    run_worker,
    *,
    ws_broadcast_session_id: Optional[str] = None,
    repair_job_id: Optional[str] = None,
    success_mode: str = "exit_code",
):
    """run_worker(loop, q) 在线程里跑闭环：经 asyncio.Queue 推送 step，结束时推送 done。
    若提供 repair_job_id + ws_broadcast_session_id，同步向该会话 WebSocket 推送 repair_* 时间线消息。
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def thread_main():
        try:
            run_worker(loop, q)
        except Exception as e:
            logger.exception("closure-execute stream worker")
            loop.call_soon_threadsafe(q.put_nowait, ("error", str(e)))

    fut = loop.run_in_executor(None, thread_main)
    try:
        while True:
            kind, payload = await q.get()
            if kind == "step":
                sr = _closure_step_record_to_response(payload)
                dumped = sr.model_dump()
                yield _closure_sse_pack("step", dumped)
                if repair_job_id and ws_broadcast_session_id:
                    await _repair_emit_step_ws(
                        ws_broadcast_session_id,
                        repair_job_id,
                        dumped,
                        success_mode,
                    )
            elif kind == "io":
                yield _closure_sse_pack("io", payload)
            elif kind == "mirror":
                # 默认不写入左侧终端；OPS_CLOSURE_MIRROR_TERMINAL=1 时才推送
                from chibycore.closure_capture_mirror import closure_terminal_mirror_enabled

                if not closure_terminal_mirror_enabled():
                    continue
                sid_m = payload.get("sid") or ""
                data_m = payload.get("data") or ""
                if sid_m and data_m:
                    await session_mgr._broadcast(
                        sid_m,
                        {
                            "type": "output",
                            "session_id": sid_m,
                            "data": data_m,
                            "closure_mirror": True,
                        },
                    )
            elif kind == "done":
                result, tid, smode = payload
                full = _closure_run_result_to_response(result, tid, smode)
                yield _closure_sse_pack("done", full.model_dump())
                if repair_job_id and ws_broadcast_session_id:
                    await _repair_emit_done_ws(
                        ws_broadcast_session_id, repair_job_id, result, tid
                    )
                break
            elif kind == "error":
                yield _closure_sse_pack("error", {"message": payload})
                if repair_job_id and ws_broadcast_session_id:
                    await _repair_ws_broadcast(
                        ws_broadcast_session_id,
                        "repair_exhausted",
                        {
                            "repair_job_id": repair_job_id,
                            "trace_id": repair_job_id,
                            "stop_reason": "stream_error",
                            "message": str(payload)[:800],
                        },
                    )
                break
    finally:
        await asyncio.wrap_future(fut)


async def _closure_stream_with_repair_prelude(
    run_worker,
    *,
    ws_target: Optional[str],
    trace_id: str,
    success_mode: str,
    max_fix_attempts: int,
):
    """首帧 SSE meta + repair_started（WebSocket），再进入标准 closure SSE 流。"""
    _repair_register_cancel(trace_id)
    try:
        yield _closure_sse_pack(
            "meta",
            {
                "repair_job_id": trace_id,
                "trace_id": trace_id,
                "repair_ws_bound": bool(ws_target),
            },
        )
        if ws_target:
            await _repair_emit_started_ws(
                ws_target, trace_id, trace_id, max_fix_attempts
            )
        async for chunk in _closure_execute_stream_sse(
            run_worker,
            ws_broadcast_session_id=ws_target,
            repair_job_id=trace_id,
            success_mode=success_mode,
        ):
            yield chunk
    finally:
        _repair_unregister_cancel(trace_id)


_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@app.post("/api/hosts/{host_id}/closure-execute", response_model=ClosureExecuteResponse)
async def closure_execute_on_host(host_id: str, body: ClosureExecuteBody):
    """在指定主机上执行「网关 + oneshot + 闭环重试」。

    与 WebSocket 里交互式 PTY **并行**：此处新建 SSH/WinRM **exec** 会话执行 `command`，
    不占用浏览器当前终端 Tab。适合脚本、巡检、或显式「受控执行」按钮调用。

    生产环境请自行加鉴权/限流；本接口等价于远程执行能力。
    """
    import asyncio
    import uuid as uuid_mod

    from fastapi import HTTPException

    from chibycore.closure_retry_runner import run_closure_retry_loop
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

    if host_id not in _HOST_STORE:
        raise HTTPException(404, "主机不存在")
    host = _HOST_STORE[host_id]
    trace_id = "cl_" + uuid_mod.uuid4().hex[:20]
    smode = (body.success_mode or "exit_code").strip().lower()
    if smode not in ("exit_code", "llm", "both"):
        raise HTTPException(400, "success_mode 须为 exit_code | llm | both")
    mirror_sid = (body.mirror_session_id or "").strip()
    if mirror_sid and session_mgr.get_session(mirror_sid) is None:
        raise HTTPException(404, "mirror_session_id 对应的会话不存在")
    if not mirror_sid:
        logger.warning(
            "closure-execute (sync) host_id=%s trace_id=%s: mirror_session_id 未传 — "
            "无终端镜像；若需 Web 终端体验请填当前 Tab session_id。",
            host_id,
            trace_id,
        )

    from chibycore.closure_capture_mirror import mirror_closure_step_to_session

    def gateway_allow(line: str):
        out = gateway_evaluate(
            ExecutionRequest(
                trace_id=trace_id,
                session_id=f"closure_rest:{host_id}",
                command_line=line,
                source="closure_rest",
                conn_type=host.conn_type.value,
                host_id=host_id,
                plan_id=None,
            )
        )
        return out.allowed, out.reason or ""

    def run_sync():
        ex = build_oneshot_from_pydantic_host(host)
        ex.connect()
        try:

            def execute_one(cmd: str):
                return ex.run_command(cmd)

            def after_step(st):
                if mirror_sid:
                    mirror_closure_step_to_session(session_mgr, mirror_sid, st)

            return run_closure_retry_loop(
                trace_id=trace_id,
                initial_command=body.command.strip(),
                execute=execute_one,
                gateway_allow=gateway_allow,
                shell_profile="powershell"
                if host.conn_type == ConnType.WINRM
                else "unix",
                nl_intent_hint=body.nl_intent_hint,
                session_id=f"closure_rest:{host_id}",
                plan_id=None,
                max_fix_attempts=body.max_fix_attempts,
                success_mode=smode,
                archive_kb=body.archive_kb,
                on_after_step=after_step if mirror_sid else None,
                **_distro_fix_kwargs_from_host(host),
            )
        finally:
            ex.close()

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, run_sync)
    except Exception as e:
        raise HTTPException(500, f"closure 执行异常: {e}") from e

    return _closure_run_result_to_response(result, trace_id, smode)


@app.post("/api/sessions/{session_id}/closure-execute", response_model=ClosureExecuteResponse)
async def closure_execute_on_session(session_id: str, body: ClosureExecuteBody):
    """本地会话：在本机子进程中执行「网关 + subprocess + 闭环重试」（不经当前 PTY）。"""
    import asyncio
    import uuid as uuid_mod

    from fastapi import HTTPException

    from chibycore.closure_capture_mirror import mirror_closure_step_to_session
    from chibycore.closure_retry_runner import run_closure_retry_loop
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.local_oneshot import LocalSubprocessOneShotExecutor

    sess = session_mgr.get_session(session_id)
    if not sess:
        raise HTTPException(404, "会话不存在")
    if getattr(sess, "host_id", None):
        raise HTTPException(
            400,
            "本会话已绑定远程主机，请使用 POST /api/hosts/{host_id}/closure-execute",
        )
    if sess.conn_type != ConnType.LOCAL:
        raise HTTPException(400, "仅 conn_type=local 的会话可走本地闭环")

    trace_id = "cl_" + uuid_mod.uuid4().hex[:20]
    smode = (body.success_mode or "exit_code").strip().lower()
    if smode not in ("exit_code", "llm", "both"):
        raise HTTPException(400, "success_mode 须为 exit_code | llm | both")
    mirror_sid = (body.mirror_session_id or "").strip()
    if mirror_sid and session_mgr.get_session(mirror_sid) is None:
        raise HTTPException(404, "mirror_session_id 对应的会话不存在")

    raw_sp = (body.shell_profile or "").strip().lower()
    if raw_sp in ("unix", "powershell"):
        shell_profile = raw_sp
    else:
        shell_profile = resolve_shell_profile(sess).value

    def gateway_allow(line: str):
        out = gateway_evaluate(
            ExecutionRequest(
                trace_id=trace_id,
                session_id=session_id,
                command_line=line,
                source="closure_local",
                conn_type="local",
                host_id=None,
                plan_id=None,
            )
        )
        return out.allowed, out.reason or ""

    def run_sync():
        ex = LocalSubprocessOneShotExecutor(shell_profile=shell_profile)
        ex.connect()
        try:

            def execute_one(cmd: str):
                return ex.run_command(cmd)

            def after_step(st):
                if mirror_sid:
                    mirror_closure_step_to_session(session_mgr, mirror_sid, st)

            return run_closure_retry_loop(
                trace_id=trace_id,
                initial_command=body.command.strip(),
                execute=execute_one,
                gateway_allow=gateway_allow,
                shell_profile=shell_profile,
                nl_intent_hint=body.nl_intent_hint,
                session_id=session_id,
                plan_id=None,
                max_fix_attempts=body.max_fix_attempts,
                success_mode=smode,
                archive_kb=body.archive_kb,
                on_after_step=after_step if mirror_sid else None,
            )
        finally:
            ex.close()

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, run_sync)
    except Exception as e:
        raise HTTPException(500, f"closure 执行异常: {e}") from e

    return _closure_run_result_to_response(result, trace_id, smode)


@app.post("/api/hosts/{host_id}/closure-execute/stream")
async def closure_execute_on_host_stream(host_id: str, body: ClosureExecuteBody):
    """主机闭环：Server-Sent Events — io（子进程输出块）、step（步骤摘要）、done（完整 JSON）。"""
    import uuid as uuid_mod

    from fastapi import HTTPException

    from chibycore.closure_capture_mirror import (
        closure_terminal_mirror_enabled,
        format_mirror_io_fragment,
        format_mirror_step_footer_streaming,
    )
    from chibycore.closure_retry_runner import run_closure_retry_loop
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

    if host_id not in _HOST_STORE:
        raise HTTPException(404, "主机不存在")
    host = _HOST_STORE[host_id]
    trace_id = "cl_" + uuid_mod.uuid4().hex[:20]
    smode = (body.success_mode or "exit_code").strip().lower()
    if smode not in ("exit_code", "llm", "both"):
        raise HTTPException(400, "success_mode 须为 exit_code | llm | both")
    mirror_sid = (body.mirror_session_id or "").strip()
    if mirror_sid and session_mgr.get_session(mirror_sid) is None:
        raise HTTPException(404, "mirror_session_id 对应的会话不存在")
    if not mirror_sid:
        logger.warning(
            "closure-execute/stream host_id=%s trace_id=%s: mirror_session_id 未传 — "
            "右侧 WebSocket repair_* 时间线不可用；客户端请依赖 SSE io/step。",
            host_id,
            trace_id,
        )
    _term_mirror = bool(mirror_sid) and closure_terminal_mirror_enabled()

    def gateway_allow(line: str):
        out = gateway_evaluate(
            ExecutionRequest(
                trace_id=trace_id,
                session_id=f"closure_rest:{host_id}",
                command_line=line,
                source="closure_rest",
                conn_type=host.conn_type.value,
                host_id=host_id,
                plan_id=None,
            )
        )
        return out.allowed, out.reason or ""

    def run_worker(loop, q):
        from chibycore.executor_contract import RunOptions

        ex = build_oneshot_from_pydantic_host(host)
        ex.connect()
        try:
            exec_n = [0]

            def execute_one(cmd: str):
                exec_n[0] += 1
                eid = exec_n[0]

                def chunk(stream: str, text: str) -> None:
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        (
                            "io",
                            {"exec_seq": eid, "stream": stream, "text": text},
                        ),
                    )
                    if _term_mirror:
                        frag = format_mirror_io_fragment(stream, text)
                        loop.call_soon_threadsafe(
                            q.put_nowait,
                            ("mirror", {"sid": mirror_sid, "data": frag}),
                        )

                return ex.run_command(
                    cmd,
                    RunOptions(timeout_sec=120.0, stream_chunk=chunk),
                )

            def combined_after_step(st):
                if _term_mirror:
                    footer = format_mirror_step_footer_streaming(st)
                    if footer:
                        loop.call_soon_threadsafe(
                            q.put_nowait,
                            ("mirror", {"sid": mirror_sid, "data": footer}),
                        )
                loop.call_soon_threadsafe(q.put_nowait, ("step", st))

            result = run_closure_retry_loop(
                trace_id=trace_id,
                initial_command=body.command.strip(),
                execute=execute_one,
                gateway_allow=gateway_allow,
                shell_profile="powershell"
                if host.conn_type == ConnType.WINRM
                else "unix",
                nl_intent_hint=body.nl_intent_hint,
                session_id=f"closure_rest:{host_id}",
                plan_id=None,
                max_fix_attempts=body.max_fix_attempts,
                success_mode=smode,
                archive_kb=body.archive_kb,
                on_after_step=combined_after_step,
                cancel_check=lambda: _repair_cancel_predicate(trace_id),
                **_distro_fix_kwargs_from_host(host),
            )
            loop.call_soon_threadsafe(q.put_nowait, ("done", (result, trace_id, smode)))
        finally:
            ex.close()

    ws_target = mirror_sid or None
    return StreamingResponse(
        _closure_stream_with_repair_prelude(
            run_worker,
            ws_target=ws_target,
            trace_id=trace_id,
            success_mode=smode,
            max_fix_attempts=body.max_fix_attempts,
        ),
        media_type="text/event-stream",
        headers=_STREAM_HEADERS,
    )


@app.post("/api/sessions/{session_id}/closure-execute/stream")
async def closure_execute_on_session_stream(session_id: str, body: ClosureExecuteBody):
    """本地会话闭环：SSE — io / step / done。"""
    import uuid as uuid_mod

    from fastapi import HTTPException

    from chibycore.closure_capture_mirror import (
        closure_terminal_mirror_enabled,
        format_mirror_io_fragment,
        format_mirror_step_footer_streaming,
    )
    from chibycore.closure_retry_runner import run_closure_retry_loop
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.local_oneshot import LocalSubprocessOneShotExecutor

    sess = session_mgr.get_session(session_id)
    if not sess:
        raise HTTPException(404, "会话不存在")
    if getattr(sess, "host_id", None):
        raise HTTPException(
            400,
            "本会话已绑定远程主机，请使用 POST /api/hosts/{host_id}/closure-execute/stream",
        )
    if sess.conn_type != ConnType.LOCAL:
        raise HTTPException(400, "仅 conn_type=local 的会话可走本地闭环")

    trace_id = "cl_" + uuid_mod.uuid4().hex[:20]
    smode = (body.success_mode or "exit_code").strip().lower()
    if smode not in ("exit_code", "llm", "both"):
        raise HTTPException(400, "success_mode 须为 exit_code | llm | both")
    mirror_sid = (body.mirror_session_id or "").strip()
    if mirror_sid and session_mgr.get_session(mirror_sid) is None:
        raise HTTPException(404, "mirror_session_id 对应的会话不存在")
    _term_mirror = bool(mirror_sid) and closure_terminal_mirror_enabled()

    raw_sp = (body.shell_profile or "").strip().lower()
    if raw_sp in ("unix", "powershell"):
        shell_profile = raw_sp
    else:
        shell_profile = resolve_shell_profile(sess).value

    def gateway_allow(line: str):
        out = gateway_evaluate(
            ExecutionRequest(
                trace_id=trace_id,
                session_id=session_id,
                command_line=line,
                source="closure_local",
                conn_type="local",
                host_id=None,
                plan_id=None,
            )
        )
        return out.allowed, out.reason or ""

    def run_worker(loop, q):
        from chibycore.executor_contract import RunOptions

        ex = LocalSubprocessOneShotExecutor(shell_profile=shell_profile)
        ex.connect()
        try:
            exec_n = [0]

            def execute_one(cmd: str):
                exec_n[0] += 1
                eid = exec_n[0]

                def chunk(stream: str, text: str) -> None:
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        (
                            "io",
                            {"exec_seq": eid, "stream": stream, "text": text},
                        ),
                    )
                    if _term_mirror:
                        frag = format_mirror_io_fragment(stream, text)
                        loop.call_soon_threadsafe(
                            q.put_nowait,
                            ("mirror", {"sid": mirror_sid, "data": frag}),
                        )

                return ex.run_command(
                    cmd,
                    RunOptions(timeout_sec=120.0, stream_chunk=chunk),
                )

            def combined_after_step(st):
                if _term_mirror:
                    footer = format_mirror_step_footer_streaming(st)
                    if footer:
                        loop.call_soon_threadsafe(
                            q.put_nowait,
                            ("mirror", {"sid": mirror_sid, "data": footer}),
                        )
                loop.call_soon_threadsafe(q.put_nowait, ("step", st))

            result = run_closure_retry_loop(
                trace_id=trace_id,
                initial_command=body.command.strip(),
                execute=execute_one,
                gateway_allow=gateway_allow,
                shell_profile=shell_profile,
                nl_intent_hint=body.nl_intent_hint,
                session_id=session_id,
                plan_id=None,
                max_fix_attempts=body.max_fix_attempts,
                success_mode=smode,
                archive_kb=body.archive_kb,
                on_after_step=combined_after_step,
                cancel_check=lambda: _repair_cancel_predicate(trace_id),
            )
            loop.call_soon_threadsafe(q.put_nowait, ("done", (result, trace_id, smode)))
        finally:
            ex.close()

    return StreamingResponse(
        _closure_stream_with_repair_prelude(
            run_worker,
            ws_target=session_id,
            trace_id=trace_id,
            success_mode=smode,
            max_fix_attempts=body.max_fix_attempts,
        ),
        media_type="text/event-stream",
        headers=_STREAM_HEADERS,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  会话管理 REST API
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/sessions", response_model=List[TerminalSession])
async def list_sessions():
    """列出所有会话。"""
    return session_mgr.list_sessions()


class ExplainOutputBody(BaseModel):
    command: str = ""
    output_tail: str = ""
    status: str = "unknown"
    exit_code: Optional[int] = None
    user_question: Optional[str] = None


@app.post("/api/sessions/{session_id}/explain-output")
async def explain_session_output(session_id: str, body: ExplainOutputBody):
    """把命令输出梳理为 Markdown「结果说明」（开源原生解读）。"""
    from fastapi import HTTPException

    if not session_mgr.get_session(session_id):
        raise HTTPException(404, "会话不存在")
    md = await _explain_command_output_md(
        session_id=session_id,
        command=body.command or "",
        output_tail=body.output_tail or "",
        status=(body.status or "unknown").strip().lower() or "unknown",
        exit_code=body.exit_code,
        user_question=body.user_question,
    )
    return {"ok": True, "explain_md": md, "explained": bool(md)}


@app.post("/api/sessions", response_model=TerminalSession)
async def create_session(body: SessionCreate):
    """创建新会话。接收 JSON body。"""
    from fastapi import HTTPException

    if body.conn_type == "ssh" and body.host_id:
        host = _HOST_STORE.get(body.host_id)
        if not host:
            raise HTTPException(404, "主机不存在")
        session = session_mgr.create_session(
            host_id=host.id,
            title=body.title,
            conn_type=ConnType.SSH,
            host=host.host,
            port=host.port,
            username=host.username,
            password=host.password,
            ssh_private_key_path=host.ssh_private_key_path,
            ssh_private_key_passphrase=host.ssh_private_key_passphrase,
        )
        try:
            if host.distro_profile is not None:
                session_mgr.refine_session_target_os_from_profile(
                    session.id, host.distro_profile
                )
                session = session_mgr.get_session(session.id) or session
        except Exception:
            pass
    elif body.conn_type == "winrm" and body.host_id:
        host = _HOST_STORE.get(body.host_id)
        if not host:
            raise HTTPException(404, "主机不存在")
        if host.conn_type != ConnType.WINRM:
            raise HTTPException(
                400,
                "该主机未配置为 WinRM：请在「添加主机」中选择连接类型为 WinRM，并填写 WinRM 端口/传输方式",
            )
        session = session_mgr.create_session(
            host_id=host.id,
            title=body.title,
            conn_type=ConnType.WINRM,
            host=host.host,
            port=host.port,
            username=host.username,
            password=host.password,
            winrm_port=host.winrm_port,
            winrm_use_ssl=host.winrm_use_ssl,
            winrm_transport=host.winrm_transport,
            winrm_server_cert_validation=host.winrm_server_cert_validation,
            winrm_shell_mode=getattr(host, "winrm_shell_mode", "interactive") or "interactive",
        )
    else:
        session = session_mgr.create_session(
            title=body.title,
            conn_type=ConnType.LOCAL,
        )
    return session


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """关闭并删除会话。"""
    await session_mgr._close_session_async(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """获取会话详情。"""
    session = session_mgr.get_session(session_id)
    if not session:
        from fastapi import HTTPException
        raise HTTPException(404, "会话不存在")
    return session


@app.patch("/api/sessions/{session_id}", response_model=TerminalSession)
async def patch_session(session_id: str, body: SessionUpdate):
    """更新会话（如目标操作系统，供 NL→命令适配）。"""
    from fastapi import HTTPException

    session = session_mgr.get_session(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    if body.target_os is not None:
        if body.target_os not in ALLOWED_TARGET_OS:
            raise HTTPException(400, f"无效的目标系统，允许值: {sorted(ALLOWED_TARGET_OS)}")
        session_mgr.update_session(session_id, target_os=body.target_os)
    return session_mgr.get_session(session_id)


@app.get("/api/sessions/{session_id}/transcript")
async def get_session_transcript(session_id: str, max_bytes: int = 131072):
    """返回 JSONL transcript 尾部（会话关闭后仍可读取已落盘文件）。"""
    path = PROJECT_ROOT / "data" / "transcripts" / f"{session_id}.jsonl"
    if not path.exists():
        return {"session_id": session_id, "lines": []}
    raw = path.read_text(encoding="utf-8", errors="replace")[-max_bytes:]
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    return {"session_id": session_id, "lines": lines[-800:]}


@app.get("/api/sessions/{session_id}/ai_stream")
async def get_session_ai_stream(session_id: str):
    """回放：返回会话 AI 流式审计事件序列（与 WS 帧字段一致）。"""
    from chibycore.ai_stream_audit import read_ai_stream_events

    events = read_ai_stream_events(session_id)
    return {"session_id": session_id, "events": events}


def _build_plan_steps_from_result(result: PromptResult) -> List[dict]:
    """将 PromptResult.command 拆成计划步骤，并做逐行风险分级。"""
    if not result.command:
        return []
    force_high = bool(result.is_dangerous)
    steps: List[dict] = []
    for line in result.command.split("\n"):
        line = line.strip()
        if not line:
            continue
        if _looks_like_markdown_analysis(line):
            logger.info("skip markdown-like plan step: %r", line[:80])
            continue
        level, w = classify_command_risk(line)
        if force_high:
            level = "HIGH"
        dangerous = level == "HIGH"
        confirm_required = level in ("MEDIUM", "HIGH")
        title = line if len(line) <= 56 else line[:53] + "..."
        steps.append(
            {
                "index": len(steps),
                "title": title,
                "command": line,
                "dangerous": dangerous,
                "confirm_required": confirm_required,
                "risk": level,
                "warning": w or (result.warning if dangerous or confirm_required else "") or "",
            }
        )
    return steps


async def _ws_shell_line(
    session_id: str,
    cmd: str,
    output_buffer: List[str],
    websocket: WebSocket,
    trace_id: str,
    plan_id: Optional[str] = None,
) -> bool:
    """计划步骤下发：经执行网关（策略+审计）。成功返回 True。"""
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate

    sess = session_mgr.get_session(session_id)
    if not sess:
        return False
    line = (cmd or "").strip()
    if not line:
        return True
    out = gateway_evaluate(
        ExecutionRequest(
            trace_id=trace_id,
            session_id=session_id,
            command_line=line,
            source="ws_plan",
            conn_type=sess.conn_type.value,
            host_id=sess.host_id,
            plan_id=plan_id,
        )
    )
    if not out.allowed:
        await websocket.send_json(
            {"type": "error", "session_id": session_id, "data": out.reason or "策略拒绝执行"}
        )
        return False
    await session_mgr.shell_input(session_id, line + "\n", echo_psrp_line=True)
    output_buffer.append(line + "\n")
    while len(output_buffer) > 50:
        output_buffer.pop(0)
    return True


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _plan_step_use_closure_env() -> bool:
    """远端计划步骤是否走 oneshot 闭环镜像（与 PTY 并行）；OPS_PLAN_RETRY_USE_CLOSURE 为兼容旧名。"""
    return _env_truthy("OPS_PLAN_STEP_USE_CLOSURE") or _env_truthy("OPS_PLAN_RETRY_USE_CLOSURE")


def _intent_checklist_enabled() -> bool:
    """意图级闭环（默认开启）；OPS_INTENT_CHECKLIST=0 关闭并回退旧计划派发。"""
    raw = (os.environ.get("OPS_INTENT_CHECKLIST") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _plan_prior_steps_summary(plan: PlanRuntime, before_index: int) -> str:
    """本步之前已完成步骤的一行摘要，供 refine 防偏离计划。"""
    lines: List[str] = []
    for i in range(0, min(max(before_index, 0), len(plan.steps))):
        st = plan.steps[i]
        one = (st.get("title") or st.get("command") or "").strip()
        if one:
            lines.append(f"{i + 1}. {one[:120]}")
    return "\n".join(lines[:24])[:2000]


async def _execute_plan_step_via_closure(
    session_id: str,
    host_id: str,
    cmd: str,
    output_buffer: List[str],
    websocket: WebSocket,
    trace_id: str,
    plan_id: Optional[str],
    nl_intent_hint: Optional[str],
) -> bool:
    """在已绑定主机上走 oneshot + 网关 + 镜像 capture（无自动修复轮次）。"""
    import uuid as uuid_mod

    from chibycore.closure_capture_mirror import (
        mirror_closure_io_to_terminal,
        mirror_closure_step_after_streaming,
    )
    from chibycore.closure_retry_runner import run_closure_retry_loop
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.executor_contract import RunOptions
    from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

    host = _HOST_STORE.get(host_id)
    if not host:
        return await _ws_shell_line(
            session_id, cmd, output_buffer, websocket, trace_id, plan_id=plan_id
        )
    line = (cmd or "").strip()
    if not line:
        return True
    tid = "pl_" + uuid_mod.uuid4().hex[:18]

    def gateway_allow(clcmd: str):
        out = gateway_evaluate(
            ExecutionRequest(
                trace_id=tid,
                session_id=session_id,
                command_line=clcmd.strip(),
                source="ws_plan_closure",
                conn_type=host.conn_type.value,
                host_id=host_id,
                plan_id=plan_id,
            )
        )
        return out.allowed, out.reason or ""

    loop = asyncio.get_running_loop()

    def run_sync():
        ex = build_oneshot_from_pydantic_host(host)
        ex.connect()
        try:

            def chunk(stream: str, text: str) -> None:
                mirror_closure_io_to_terminal(
                    session_mgr, loop, session_id, stream, text
                )

            def execute_one(c: str):
                return ex.run_command(
                    c,
                    RunOptions(timeout_sec=120.0, stream_chunk=chunk),
                )

            def after_step(st):
                mirror_closure_step_after_streaming(
                    session_mgr, loop, session_id, st
                )

            return run_closure_retry_loop(
                trace_id=tid,
                initial_command=line,
                execute=execute_one,
                gateway_allow=gateway_allow,
                shell_profile="powershell"
                if host.conn_type == ConnType.WINRM
                else "unix",
                nl_intent_hint=nl_intent_hint,
                session_id=session_id,
                plan_id=plan_id,
                max_fix_attempts=0,
                success_mode="exit_code",
                archive_kb=False,
                on_after_step=after_step,
                **_distro_fix_kwargs_from_host(host),
            )
        finally:
            ex.close()

    try:
        result = await loop.run_in_executor(None, run_sync)
    except Exception as e:
        logger.warning("plan closure 执行异常，回退 PTY: %s", e)
        return await _ws_shell_line(
            session_id, cmd, output_buffer, websocket, trace_id, plan_id=plan_id
        )

    output_buffer.append(line + "\n")
    while len(output_buffer) > 50:
        output_buffer.pop(0)
    if not result.steps:
        return True
    st0 = result.steps[0]
    if not st0.gateway_allowed:
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "data": st0.gateway_reason or "策略拒绝执行",
            }
        )
        return False
    return True


async def _execute_plan_step_via_local_closure(
    session_id: str,
    cmd: str,
    output_buffer: List[str],
    websocket: WebSocket,
    trace_id: str,
    plan_id: Optional[str],
    nl_intent_hint: Optional[str],
) -> bool:
    """本地会话：oneshot subprocess + 网关 + 镜像 capture（无自动修复轮次）。"""
    import uuid as uuid_mod

    from chibycore.closure_capture_mirror import (
        mirror_closure_io_to_terminal,
        mirror_closure_step_after_streaming,
    )
    from chibycore.closure_retry_runner import run_closure_retry_loop
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.executor_contract import RunOptions
    from chibycore.local_oneshot import LocalSubprocessOneShotExecutor

    sess = session_mgr.get_session(session_id)
    line = (cmd or "").strip()
    if not line:
        return True
    tid = "pl_" + uuid_mod.uuid4().hex[:18]
    shell_profile = resolve_shell_profile(sess).value if sess else "unix"

    def gateway_allow(clcmd: str):
        out = gateway_evaluate(
            ExecutionRequest(
                trace_id=tid,
                session_id=session_id,
                command_line=clcmd.strip(),
                source="ws_plan_closure_local",
                conn_type="local",
                host_id=None,
                plan_id=plan_id,
            )
        )
        return out.allowed, out.reason or ""

    loop = asyncio.get_running_loop()

    def run_sync():
        ex = LocalSubprocessOneShotExecutor(shell_profile=shell_profile)
        ex.connect()
        try:

            def chunk(stream: str, text: str) -> None:
                mirror_closure_io_to_terminal(
                    session_mgr, loop, session_id, stream, text
                )

            def execute_one(c: str):
                return ex.run_command(
                    c,
                    RunOptions(timeout_sec=120.0, stream_chunk=chunk),
                )

            def after_step(st):
                mirror_closure_step_after_streaming(
                    session_mgr, loop, session_id, st
                )

            return run_closure_retry_loop(
                trace_id=tid,
                initial_command=line,
                execute=execute_one,
                gateway_allow=gateway_allow,
                shell_profile=shell_profile,
                nl_intent_hint=nl_intent_hint,
                session_id=session_id,
                plan_id=plan_id,
                max_fix_attempts=0,
                success_mode="exit_code",
                archive_kb=False,
                on_after_step=after_step,
            )
        finally:
            ex.close()

    try:
        result = await loop.run_in_executor(None, run_sync)
    except Exception as e:
        logger.warning("plan local closure 执行异常，回退 PTY: %s", e)
        return await _ws_shell_line(
            session_id, cmd, output_buffer, websocket, trace_id, plan_id=plan_id
        )

    output_buffer.append(line + "\n")
    while len(output_buffer) > 50:
        output_buffer.pop(0)
    if not result.steps:
        return True
    st0 = result.steps[0]
    if not st0.gateway_allowed:
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "data": st0.gateway_reason or "策略拒绝执行",
            }
        )
        return False
    return True


async def _execute_plan_step_line(
    session_id: str,
    cmd: str,
    output_buffer: List[str],
    websocket: WebSocket,
    trace_id: str,
    plan_id: Optional[str] = None,
    *,
    nl_intent_hint: Optional[str] = None,
) -> bool:
    """计划步骤下发：远端或本地 + 环境变量时可走 closure oneshot 镜像，否则 PTY。"""
    sess = session_mgr.get_session(session_id)
    hid = getattr(sess, "host_id", None) if sess else None
    if _plan_step_use_closure_env() and sess:
        if hid and str(hid) in _HOST_STORE:
            try:
                return await _execute_plan_step_via_closure(
                    session_id,
                    str(hid),
                    cmd,
                    output_buffer,
                    websocket,
                    trace_id,
                    plan_id,
                    nl_intent_hint,
                )
            except Exception as e:
                logger.warning("plan closure 路径失败，回退 PTY: %s", e)
        elif sess.conn_type == ConnType.LOCAL and not hid:
            try:
                return await _execute_plan_step_via_local_closure(
                    session_id,
                    cmd,
                    output_buffer,
                    websocket,
                    trace_id,
                    plan_id,
                    nl_intent_hint,
                )
            except Exception as e:
                logger.warning("plan local closure 路径失败，回退 PTY: %s", e)
    return await _ws_shell_line(
        session_id, cmd, output_buffer, websocket, trace_id, plan_id=plan_id
    )


def _looks_like_markdown_analysis(text: str) -> bool:
    """检测误把 LLM 说明/Markdown 当 Shell 命令下发的情况。"""
    from chibyterm.llm_shell import looks_like_markdown_analysis

    return looks_like_markdown_analysis(text)


def _sanitize_llm_prompt_result(result: PromptResult) -> PromptResult:
    from chibyterm.llm_shell import sanitize_prompt_result_command

    return sanitize_prompt_result_command(result)


async def _guarded_shell_input_line(
    session_id: str,
    line: str,
    output_buffer: List[str],
    websocket: WebSocket,
    trace_id: str,
    source: str,
) -> bool:
    """exec / LLM auto / confirm 等经网关的单行执行。"""
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate

    sess = session_mgr.get_session(session_id)
    if not sess:
        return False
    s = (line or "").strip()
    if not s:
        return True
    # 禁止把结果说明 / Markdown 分析注入交互 Shell（会导致 command not found）
    if _looks_like_markdown_analysis(s):
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "data": "已拦截：内容像是分析结果/Markdown，而非可执行命令（不会写入终端）。",
            }
        )
        logger.warning(
            "blocked markdown-like shell inject session=%s source=%s preview=%r",
            session_id,
            source,
            s[:160],
        )
        return False
    out = gateway_evaluate(
        ExecutionRequest(
            trace_id=trace_id,
            session_id=session_id,
            command_line=s,
            source=source,
            conn_type=sess.conn_type.value,
            host_id=sess.host_id,
            plan_id=None,
        )
    )
    if not out.allowed:
        await websocket.send_json(
            {"type": "error", "session_id": session_id, "data": out.reason or "策略拒绝执行"}
        )
        return False
    # 仅下发命令本身；若混入多段文本，只取首段可执行行，避免后续说明进 PTY
    first = s.splitlines()[0].strip()
    if len(s.splitlines()) > 1 and first and not _looks_like_markdown_analysis(first):
        # 后续行若像说明则丢弃，只执行首行/整段纯命令
        rest = "\n".join(s.splitlines()[1:]).strip()
        if rest and _looks_like_markdown_analysis(rest):
            s = first
            logger.info(
                "stripped trailing analysis from shell inject session=%s source=%s",
                session_id,
                source,
            )
    await session_mgr.shell_input(session_id, s + "\n", echo_psrp_line=True)
    output_buffer.append(s + "\n")
    while len(output_buffer) > 50:
        output_buffer.pop(0)
    return True


def _collect_suggested_rollbacks(plan: PlanRuntime, last_completed_exclusive: int) -> List[str]:
    """收集 index < last_completed_exclusive 且带 rollback_command 的步骤（逆序）。"""
    out: List[str] = []
    upper = min(last_completed_exclusive, len(plan.steps)) - 1
    for i in range(upper, -1, -1):
        rb = (plan.steps[i].get("rollback_command") or "").strip()
        if rb:
            out.append(rb)
    return out


async def _guarded_verify_line(
    session_id: str,
    line: str,
    output_buffer: List[str],
    websocket: WebSocket,
    trace_id: str,
    plan_id: Optional[str],
) -> bool:
    """计划步骤后的验证命令，经网关（source=ws_verify）。"""
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate

    sess = session_mgr.get_session(session_id)
    if not sess:
        return False
    s = (line or "").strip()
    if not s:
        return True
    out = gateway_evaluate(
        ExecutionRequest(
            trace_id=trace_id,
            session_id=session_id,
            command_line=s,
            source="ws_verify",
            conn_type=sess.conn_type.value,
            host_id=sess.host_id,
            plan_id=plan_id,
        )
    )
    if not out.allowed:
        await websocket.send_json(
            {"type": "error", "session_id": session_id, "data": out.reason or "策略拒绝验证命令"}
        )
        return False
    await session_mgr.shell_input(session_id, s + "\n", echo_psrp_line=True)
    output_buffer.append(s + "\n")
    while len(output_buffer) > 50:
        output_buffer.pop(0)
    return True


def _heuristic_step_shell_status(tail: str) -> str:
    """根据本步命令后的捕获尾部做启发式成败（非退出码，PTY 无可靠 exit code）。"""
    if not (tail or "").strip():
        return "unknown"
    low = tail.lower()
    bad = (
        "error:",
        "fatal:",
        "not found",
        "command not found",
        "失败",
        "cannot access",
        "无法将",
        "is not recognized",
        "denied",
        "permission denied",
        "segmentation fault",
        "syntax error",
        "unexpected token",
        "errno",
        "traceback",
        "exception:",
        "cannot find path",
        "no such file",
        # WinRM / PSRP 按行：会话侧用中文括号包一层 Python/pypsrp 异常（不含英文 error: 子串）
        "执行错误",
        "winrm 错误",
        "bad http response",
        "wsman fault",
        "soap fault",
        "access denied",  # WinRM 英文常见
        # PowerShell 错误流（Remove-Item 等）：无 error: 子串，需单独匹配
        "categoryinfo",
        "fullyqualifiederrorid",
        "itemnotfoundexception",
        "objectnotfound",
        "pathnotfound",
        "itemnotfound",
        "找不到路径",
        "因为该路径不存在",
        "找不到接受实际参数",
        "无法处理命令",
        "终止错误",
        "terminating error",
    )
    if any(b in low for b in bad):
        return "fail"
    return "pass"


def _output_capture_delta_since(
    session_id: str,
    cap_mark_before: int,
    *,
    max_chars: int = 8000,
) -> str:
    """从累积终端捕获中取 cap_mark 之后的增量（用于卡片摘要）。"""
    cap = session_mgr.get_output_capture(session_id)
    delta = cap[cap_mark_before:] if len(cap) >= cap_mark_before else cap
    if max_chars > 0 and len(delta) > max_chars:
        return delta[-max_chars:]
    return delta


def _looks_like_shell_back_at_prompt(text: str) -> bool:
    """粗判捕获尾部是否已回到交互提示符（命令基本结束）。"""
    if not text:
        return False
    # 去掉常见 CSI/OSC 后再看末行
    plain = sanitize_terminal_text_for_ui(text[-400:])
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", plain)
    lines = [ln.strip() for ln in plain.replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return False
    last = lines[-1]
    # bash/zsh/fish 常见：user@host:path$  /  PS>  /  #
    if re.search(r"[\$#>]\s*$", last):
        return True
    if re.search(r"PS\s+[A-Z]:\\.*>\s*$", last, re.I):
        return True
    return False


def _sample_output_for_explain(text: str, *, max_chars: int = 14000) -> str:
    """长输出：保留头尾与 === 分段标题附近，避免只剩进程 Top 导致说明偏科。"""
    t = (text or "").strip()
    if not t or len(t) <= max_chars:
        return t
    # 预留中间分段，避免头尾把预算吃光
    mid_reserve = min(3600, max(800, max_chars // 4))
    head_n = max(2800, (max_chars - mid_reserve) // 3)
    tail_n = max(4000, max_chars - mid_reserve - head_n - 80)
    if head_n + tail_n + mid_reserve > max_chars:
        overflow = head_n + tail_n + mid_reserve - max_chars
        tail_n = max(2500, tail_n - overflow)
    head = t[:head_n]
    tail = t[-tail_n:]
    mid_bits: List[str] = []
    mid_budget = mid_reserve
    for m in re.finditer(r"(?m)^=== .+ ===\s*$", t):
        if m.start() < head_n or m.start() > len(t) - tail_n:
            continue
        take = min(900, mid_budget)
        if take < 120:
            break
        frag = t[m.start() : m.start() + take]
        if frag and frag not in mid_bits:
            mid_bits.append(frag)
            mid_budget -= len(frag) + 8
            if mid_budget < 120 or len(mid_bits) >= 8:
                break
    mid = "\n\n".join(mid_bits)
    parts = [head, "\n…(中间已抽样省略)…\n"]
    if mid:
        parts.append(mid)
        parts.append("\n…\n")
    parts.append(tail)
    out = "".join(parts)
    return out if len(out) <= max_chars + 400 else out[:max_chars] + "…"


# OSC（标题等）与 bash readline 括号粘贴模式 ?2004h/l 等：右侧卡片展示前剥离，避免乱码；SGR 颜色序列保留给前端 ansi-to-html
_OSC_SEQ_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CURSOR_PRIV_CSI_RE = re.compile(r"\x1b\[\?[0-9;]*[hlHL]")


def sanitize_terminal_text_for_ui(text: str) -> str:
    if not text:
        return ""
    t = _OSC_SEQ_RE.sub("", text)
    t = _CURSOR_PRIV_CSI_RE.sub("", t)
    return t


def _tail_stripped_and_local_ps_codes(tail: str) -> Tuple[str, List[int]]:
    """本地 PowerShell 包装脚本会在输出末行写入 __OPS_EXIT_CODE__；解析后剥离以免污染展示与启发式。"""
    codes = parse_ps_exit_marker_codes(tail)
    stripped = strip_ps_exit_marker_lines(tail)
    return stripped, codes


async def _emit_step_command_result(
    ws: WebSocket,
    session_id: str,
    plan_id: str,
    step_index: int,
    command: str,
    cap_mark_before: int,
) -> None:
    """在 shell 发送后等待回显进入 capture，再推送本步终端增量供右侧卡片展示。"""
    await asyncio.sleep(0.45)
    tail = _output_capture_delta_since(session_id, cap_mark_before)
    tail_stripped, codes = _tail_stripped_and_local_ps_codes(tail)
    status = _heuristic_step_shell_status(tail_stripped)
    tail_ui = sanitize_terminal_text_for_ui(tail_stripped)
    pair = session_mgr.consume_psrp_inject_batch_outcome(session_id)
    extra_ws: Dict[str, Any] = {}
    if pair is not None:
        st, code = pair
        status = st
        extra_ws["status_source"] = "psrp"
        extra_ws["psrp_exit_code"] = code
        if code is not None:
            extra_ws["shell_exit_code"] = int(code)
    elif codes:
        status = "fail" if any(c != 0 for c in codes) else "pass"
        bad = next((c for c in codes if c != 0), None)
        ec = int(bad) if bad is not None else int(codes[-1])
        extra_ws["status_source"] = "local_ps_exit"
        extra_ws["shell_exit_code"] = ec
        extra_ws["psrp_exit_code"] = ec
    payload = {
        "type": "step_command_result",
        "session_id": session_id,
        "plan_id": plan_id,
        "step_index": step_index,
        "command": command,
        "output_tail": tail_ui,
        "status": status,
        **extra_ws,
    }
    merge_nl_payload(
        payload,
        command=command,
        output_tail=tail_ui,
        status=status,
        kind="step_command_result",
    )
    await ws.send_json(payload)


async def _explain_command_output_md(
    *,
    session_id: str,
    command: str,
    output_tail: str,
    status: str = "unknown",
    exit_code: Optional[int] = None,
    user_question: Optional[str] = None,
) -> str:
    """命令输出 → Markdown 结果说明（开源 ``terminal.llm_explain``）。"""
    from chibyterm.llm_explain import explain_command_output_md

    try:
        sess = session_mgr.get_session(session_id)
        host_id = (getattr(sess, "host_id", None) or "") if sess else ""
        host_label = host_id or "当前终端"
        if sess is not None:
            hname = (getattr(sess, "title", None) or "").strip()
            haddr = (getattr(sess, "host", None) or "").strip()
            if hname and haddr:
                host_label = f"{hname} ({haddr})"
            elif hname:
                host_label = hname
            elif haddr:
                host_label = haddr
            if host_id and host_id in _HOST_STORE:
                ho = _HOST_STORE[host_id]
                host_label = (
                    f"{ho.name} ({ho.host})" if getattr(ho, "host", None) else ho.name
                )

        user_q = (user_question or "").strip() or session_mgr.get_last_nl_text(session_id)
        ui_locale = session_mgr.get_ui_locale(session_id)
        return await explain_command_output_md(
            command=command,
            output_tail=output_tail,
            status=status,
            exit_code=exit_code,
            user_question=user_q or "",
            host_label=host_label,
            host_id=host_id or "",
            ui_locale=ui_locale,
        )
    except Exception as exc:
        logger.warning("terminal llm explain failed: %s", exc)
        loc = session_mgr.get_ui_locale(session_id) if session_mgr else "zh-CN"
        if loc == "en":
            return (
                "**Conclusion: the command finished.**\n\n"
                "- Result summary is temporarily unavailable; expand “Command output” above for raw text."
            )
        if loc == "zh-TW":
            return (
                "**結論：命令已執行完畢。**\n\n"
                "- 結果梳理暫不可用，請展開上方「命令輸出」查看原始內容。"
            )
        return (
            "**结论：命令已执行完毕。**\n\n"
            "- 结果梳理暂不可用，请展开上方「命令输出」查看原始内容。"
        )


async def _emit_llm_command_result(
    ws: WebSocket,
    session_id: str,
    ai_card_id: str,
    command: str,
    cap_mark_before: int,
) -> None:
    """LLM 单次确认执行（exec + llm_capture）后的终端增量 + 高效型同款 Markdown 梳理。"""
    await asyncio.sleep(0.45)
    tail = _output_capture_delta_since(session_id, cap_mark_before)
    tail_stripped, codes = _tail_stripped_and_local_ps_codes(tail)
    status = _heuristic_step_shell_status(tail_stripped)
    tail_ui = sanitize_terminal_text_for_ui(tail_stripped)
    pair = session_mgr.consume_psrp_inject_batch_outcome(session_id)
    extra_ws: Dict[str, Any] = {}
    if pair is not None:
        st, code = pair
        status = st
        extra_ws["status_source"] = "psrp"
        extra_ws["psrp_exit_code"] = code
        if code is not None:
            extra_ws["shell_exit_code"] = int(code)
    elif codes:
        status = "fail" if any(c != 0 for c in codes) else "pass"
        bad = next((c for c in codes if c != 0), None)
        ec = int(bad) if bad is not None else int(codes[-1])
        extra_ws["status_source"] = "local_ps_exit"
        extra_ws["shell_exit_code"] = ec
        extra_ws["psrp_exit_code"] = ec

    # 先推原始输出，前端可立刻展示；再异步整理 Markdown 结论
    await ws.send_json(
        {
            "type": "llm_command_result",
            "session_id": session_id,
            "ai_card_id": ai_card_id,
            "command": command,
            "output_tail": tail_ui,
            "status": status,
            "explain_pending": True,
            **extra_ws,
        }
    )

    try:
        await ws.send_json(
            {
                "type": "llm_explain_phase",
                "session_id": session_id,
                "ai_card_id": ai_card_id,
                "text": "正在把执行结果整理成通俗说明",
            }
        )
    except Exception:
        pass

    exit_code = None
    if extra_ws.get("shell_exit_code") is not None:
        try:
            exit_code = int(extra_ws["shell_exit_code"])
        except (TypeError, ValueError):
            exit_code = None

    explain_md = await _explain_command_output_md(
        session_id=session_id,
        command=command,
        output_tail=tail_ui,
        status=status,
        exit_code=exit_code,
    )

    await ws.send_json(
        {
            "type": "llm_command_result",
            "session_id": session_id,
            "ai_card_id": ai_card_id,
            "command": command,
            "output_tail": tail_ui,
            "status": status,
            "explain_md": explain_md,
            "explained": bool((explain_md or "").strip()),
            "explain_pending": False,
            **extra_ws,
        }
    )


async def _run_step_verification(
    ws: WebSocket,
    session_id: str,
    plan: PlanRuntime,
    step_index: int,
    trace_id: str,
    output_buffer: List[str],
) -> None:
    if step_index < 0 or step_index >= len(plan.steps):
        return
    step = plan.steps[step_index]
    step_cmd = (step.get("command") or "").strip()
    vc = (step.get("verify_command") or "").strip()
    if not vc:
        return
    cap0 = session_mgr.get_output_capture(session_id)
    mark = len(cap0)
    ok = await _guarded_verify_line(
        session_id, vc, output_buffer, ws, trace_id, plan.plan_id
    )
    if not ok:
        pld = {
            "type": "verification",
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "step_index": step_index,
            "command": step_cmd,
            "verify_command": vc,
            "status": "policy_denied",
            "output_tail": "",
        }
        merge_nl_payload(
            pld,
            command=vc,
            output_tail="",
            status="policy_denied",
            kind="verification",
        )
        await ws.send_json(pld)
        return
    await asyncio.sleep(0.45)
    cap1 = session_mgr.get_output_capture(session_id)
    tail = cap1[mark:] if len(cap1) >= mark else cap1[-4000:]
    exp = (step.get("verify_expect_substring") or "").strip()
    if exp:
        status = "pass" if exp in tail else "fail"
    else:
        low = tail.lower()
        bad = (
            "error:",
            "fatal:",
            "not found",
            "command not found",
            "失败",
            "cannot access",
            "无法将",
            "is not recognized",
        )
        status = "fail" if any(b in low for b in bad) else "pass"
    pld2 = {
        "type": "verification",
        "session_id": session_id,
        "plan_id": plan.plan_id,
        "step_index": step_index,
        "command": step_cmd,
        "verify_command": vc,
        "status": status,
        "output_tail": tail[-4000:],
    }
    merge_nl_payload(
        pld2,
        command=vc,
        output_tail=tail[-8000:],
        status=status,
        kind="verification",
    )
    await ws.send_json(pld2)


async def _plan_send_finished(ws: WebSocket, session_id: str, plan: PlanRuntime, reason: str) -> None:
    await ws.send_json(
        {
            "type": "plan_finished",
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "reason": reason,
            "total_steps": plan.total_steps(),
            "intent_status": (plan.checklist or {}).get("status") if plan.checklist else None,
            "intent_completed": (plan.checklist or {}).get("completed") if plan.checklist else None,
            "intent_total": (plan.checklist or {}).get("total") if plan.checklist else None,
        }
    )
    session_mgr.clear_terminal_plan(session_id)


async def _emit_intent_checklist_progress(
    ws: WebSocket,
    session_id: str,
    plan: PlanRuntime,
    checklist_dict: dict,
) -> None:
    plan.checklist = checklist_dict
    await ws.send_json(
        {
            "type": "intent_checklist_progress",
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "intent": checklist_dict.get("intent") or plan.intent or "",
            "completed": checklist_dict.get("completed") or 0,
            "total": checklist_dict.get("total") or 0,
            "status": checklist_dict.get("status") or "pending",
            "items": checklist_dict.get("items") or [],
        }
    )


async def _run_plan_as_intent_checklist(
    ws: WebSocket,
    session_id: str,
    output_buffer: List[str],
    trace_id: str,
) -> None:
    """批准后：意图清单逐项执行（项内命令级闭环含修复/复验），并推送进度。"""
    import uuid as uuid_mod

    from chibycore.closure_capture_mirror import (
        mirror_closure_io_to_terminal,
    )
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.executor_contract import RunOptions
    from chibycore.intent_checklist import checklist_from_plan_steps, run_intent_checklist

    plan = session_mgr.get_terminal_plan(session_id)
    if not plan or plan.phase != "running":
        return

    intent = (plan.intent or plan.explanation or "").strip() or "执行计划"
    plan.intent = intent
    cl = checklist_from_plan_steps(intent, plan.steps, split_compound=True)
    if not cl.items:
        await _plan_send_aborted(ws, session_id, plan, "empty_intent_checklist")
        return

    # 同步拆分后的步骤到 plan（危险闸门按项检测）
    new_steps: List[dict] = []
    for it in cl.items:
        new_steps.append(
            {
                "index": len(new_steps),
                "title": it.description,
                "command": it.command,
                "dangerous": False,
                "confirm_required": False,
                "risk": "LOW",
                "warning": "",
            }
        )
    plan.steps = new_steps
    plan.current_index = 0
    await _emit_intent_checklist_progress(ws, session_id, plan, cl.to_dict())

    # 高危项：仍暂停等待确认（零人工：仅高危打断）
    for i, step in enumerate(plan.steps):
        cmd = (step.get("command") or "").strip()
        if step.get("confirm_required") or step.get("dangerous"):
            plan.phase = "awaiting_danger_confirm"
            plan.current_index = i
            plan.danger_line = cmd
            await ws.send_json(
                {
                    "type": "plan_danger",
                    "session_id": session_id,
                    "plan_id": plan.plan_id,
                    "step_index": i,
                    "total": plan.total_steps(),
                    "command": cmd,
                    "warning": step.get("warning") or "危险操作需确认后才会发往终端",
                }
            )
            return

    sess = session_mgr.get_session(session_id)
    hid = getattr(sess, "host_id", None) if sess else None
    host = _HOST_STORE.get(str(hid)) if hid else None
    shell_profile = "unix"
    if host and host.conn_type == ConnType.WINRM:
        shell_profile = "powershell"
    elif sess:
        try:
            shell_profile = resolve_shell_profile(sess).value
        except Exception:
            shell_profile = "unix"

    loop = asyncio.get_running_loop()
    progress_q: asyncio.Queue = asyncio.Queue()

    def _on_progress(checklist, _item) -> None:
        try:
            loop.call_soon_threadsafe(progress_q.put_nowait, checklist.to_dict())
        except Exception:
            pass

    def run_sync():
        from chibycore.local_oneshot import LocalSubprocessOneShotExecutor
        from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

        tid = "ic_" + uuid_mod.uuid4().hex[:16]

        def gateway_allow(clcmd: str):
            out = gateway_evaluate(
                ExecutionRequest(
                    trace_id=tid,
                    session_id=session_id,
                    command_line=clcmd.strip(),
                    source="ws_intent_checklist",
                    conn_type=(host.conn_type.value if host else "local"),
                    host_id=str(hid) if hid else None,
                    plan_id=plan.plan_id,
                )
            )
            return out.allowed, out.reason or ""

        if host:
            ex = build_oneshot_from_pydantic_host(host)
        else:
            ex = LocalSubprocessOneShotExecutor(shell_profile=shell_profile)
        ex.connect()
        try:

            def chunk(stream: str, text: str) -> None:
                mirror_closure_io_to_terminal(
                    session_mgr, loop, session_id, stream, text
                )

            def execute_one(c: str):
                line = (c or "").strip()
                if line:
                    output_buffer.append(line + "\n")
                    while len(output_buffer) > 50:
                        output_buffer.pop(0)
                return ex.run_command(
                    c,
                    RunOptions(timeout_sec=120.0, stream_chunk=chunk),
                )

            kw = {}
            if host:
                kw.update(_distro_fix_kwargs_from_host(host))
            return run_intent_checklist(
                checklist=cl,
                execute=execute_one,
                gateway_allow=gateway_allow,
                shell_profile=shell_profile,
                session_id=session_id,
                plan_id=plan.plan_id,
                max_fix_attempts=3,
                success_mode="exit_code",
                verify_original_after_fix=True,
                on_item_progress=_on_progress,
                stop_on_item_failure=True,
                **kw,
            )
        finally:
            ex.close()

    worker = loop.run_in_executor(None, run_sync)
    final_cl = None
    while True:
        if worker.done():
            # drain remaining progress
            while not progress_q.empty():
                try:
                    d = progress_q.get_nowait()
                    await _emit_intent_checklist_progress(ws, session_id, plan, d)
                except Exception:
                    break
            try:
                final_cl = await worker
            except Exception as e:
                logger.warning("intent checklist 执行失败: %s", e)
                await _plan_send_aborted(ws, session_id, plan, "intent_checklist_error")
                return
            break
        try:
            d = await asyncio.wait_for(progress_q.get(), timeout=0.25)
            await _emit_intent_checklist_progress(ws, session_id, plan, d)
        except asyncio.TimeoutError:
            continue

    if final_cl is not None:
        await _emit_intent_checklist_progress(ws, session_id, plan, final_cl.to_dict())
        plan.checklist = final_cl.to_dict()
        # 对齐 plan.current_index
        plan.current_index = final_cl.completed_count
        if final_cl.status == "completed":
            plan.phase = "done"
            await _plan_send_finished(ws, session_id, plan, "intent_completed")
        elif final_cl.status == "partial":
            plan.phase = "done"
            await _plan_send_finished(ws, session_id, plan, "intent_partial")
        else:
            await _plan_send_aborted(ws, session_id, plan, "intent_failed")


async def _emit_plan_progress_running(
    ws: WebSocket,
    session_id: str,
    plan: PlanRuntime,
    step_index: int,
    command: str,
) -> None:
    """步骤命令即将发往 PTY/闭环前推送，便于前端识别「执行中」并做无输出超时提示。"""
    await ws.send_json(
        {
            "type": "plan_progress",
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "step_index": step_index,
            "total": plan.total_steps(),
            "command": command,
            "phase": "running",
        }
    )


async def _plan_send_aborted(ws: WebSocket, session_id: str, plan: PlanRuntime, reason: str) -> None:
    roll = _collect_suggested_rollbacks(plan, plan.current_index)
    await ws.send_json(
        {
            "type": "plan_aborted",
            "session_id": session_id,
            "plan_id": plan.plan_id,
            "reason": reason,
            "suggested_rollbacks": roll,
        }
    )
    session_mgr.clear_terminal_plan(session_id)


async def _dispatch_plan_core(
    ws: WebSocket, session_id: str, output_buffer: List[str], trace_id: str
) -> None:
    """在 plan.phase == running 时，从 current_index 起派发步骤（含危险闸门与 batch/gated）。"""
    plan = session_mgr.get_terminal_plan(session_id)
    if not plan or plan.phase != "running":
        return
    while plan.current_index < plan.total_steps():
        step = plan.steps[plan.current_index]
        cmd = step["command"]
        if step.get("confirm_required") or step.get("dangerous"):
            plan.phase = "awaiting_danger_confirm"
            plan.danger_line = cmd
            await ws.send_json(
                {
                    "type": "plan_danger",
                    "session_id": session_id,
                    "plan_id": plan.plan_id,
                    "step_index": plan.current_index,
                    "total": plan.total_steps(),
                    "command": cmd,
                    "warning": step.get("warning") or "危险操作需确认后才会发往终端",
                }
            )
            return
        await _emit_plan_progress_running(ws, session_id, plan, plan.current_index, cmd)
        session_mgr.reset_psrp_inject_batch(session_id)
        cap_mark = len(session_mgr.get_output_capture(session_id))
        ok = await _execute_plan_step_line(
            session_id, cmd, output_buffer, ws, trace_id, plan_id=plan.plan_id
        )
        if not ok:
            await _plan_send_aborted(ws, session_id, plan, "policy_denied")
            return
        if plan.style == "batch":
            await _emit_step_command_result(
                ws, session_id, plan.plan_id, plan.current_index, cmd, cap_mark
            )
            await ws.send_json(
                {
                    "type": "plan_progress",
                    "session_id": session_id,
                    "plan_id": plan.plan_id,
                    "step_index": plan.current_index,
                    "total": plan.total_steps(),
                    "command": cmd,
                    "phase": "executed",
                }
            )
            await _run_step_verification(
                ws, session_id, plan, plan.current_index, trace_id, output_buffer
            )
            plan.current_index += 1
            await asyncio.sleep(0.12)
            continue
        plan.phase = "awaiting_step_ok"
        await ws.send_json(
            {
                "type": "plan_step",
                "session_id": session_id,
                "plan_id": plan.plan_id,
                "step_index": plan.current_index,
                "total": plan.total_steps(),
                "command": cmd,
                "phase": "awaiting_user",
            }
        )
        await _emit_step_command_result(
            ws, session_id, plan.plan_id, plan.current_index, cmd, cap_mark
        )
        return
    await _plan_send_finished(ws, session_id, plan, "completed")


async def _on_plan_danger_confirmed(
    ws: WebSocket, session_id: str, output_buffer: List[str], trace_id: str
) -> None:
    plan = session_mgr.get_terminal_plan(session_id)
    if not plan or plan.phase != "awaiting_danger_confirm" or not plan.danger_line:
        return
    cmd = plan.danger_line
    plan.danger_line = None
    await _emit_plan_progress_running(ws, session_id, plan, plan.current_index, cmd)
    session_mgr.reset_psrp_inject_batch(session_id)
    cap_mark = len(session_mgr.get_output_capture(session_id))
    ok = await _execute_plan_step_line(
        session_id, cmd, output_buffer, ws, trace_id, plan_id=plan.plan_id
    )
    if not ok:
        await _plan_send_aborted(ws, session_id, plan, "policy_denied")
        return
    if plan.style == "batch":
        await _emit_step_command_result(
            ws, session_id, plan.plan_id, plan.current_index, cmd, cap_mark
        )
        await ws.send_json(
            {
                "type": "plan_progress",
                "session_id": session_id,
                "plan_id": plan.plan_id,
                "step_index": plan.current_index,
                "total": plan.total_steps(),
                "command": cmd,
                "phase": "executed",
            }
        )
        await _run_step_verification(
            ws, session_id, plan, plan.current_index, trace_id, output_buffer
        )
        plan.current_index += 1
        plan.phase = "running"
        await _dispatch_plan_core(ws, session_id, output_buffer, trace_id)
    else:
        plan.phase = "awaiting_step_ok"
        await ws.send_json(
            {
                "type": "plan_step",
                "session_id": session_id,
                "plan_id": plan.plan_id,
                "step_index": plan.current_index,
                "total": plan.total_steps(),
                "command": cmd,
                "phase": "awaiting_user",
            }
        )
        await _emit_step_command_result(
            ws, session_id, plan.plan_id, plan.current_index, cmd, cap_mark
        )


# ═══════════════════════════════════════════════════════════════════════════
#  自然语言：知识库 / 脚本库（仅检索，不走 Agent 计划链）
# ═══════════════════════════════════════════════════════════════════════════


def _format_hub_search_plain(resp: SearchResponse, *, hub_mode: str) -> str:
    """检索结果 → 纯文本说明（右侧流式展示）。"""
    label = "知识库" if hub_mode == "kb" else "脚本库"
    lines: List[str] = [
        f"【{label}】查询：{resp.query}",
        f"命中 {resp.total} 条 · 耗时 {resp.took_ms} ms",
        "",
    ]
    if not resp.results:
        if hub_mode == "script":
            lines.append(
                "（脚本库暂无匹配；可在「知识库管理」录入脚本，或通过闭环沉淀脚本条目。）"
            )
        else:
            lines.append(
                "（无匹配条目；可在右侧输入栏选择「知识库 / 脚本库」模式检索，或通过闭环沉淀。）"
            )
        return "\n".join(lines)

    def _clean(s: Any) -> str:
        if s is None:
            return ""
        t = str(s).replace("\r", " ").strip()
        if not t or t.lower() == "none":
            return ""
        # 历史脏数据：字面量 None / None; 连续出现
        t = re.sub(r"(?:None\s*[;,]?\s*){2,}", " ", t)
        t = re.sub(r"\bNone\b", "", t)
        t = re.sub(r"\s*\|\s*", " · ", t)
        t = re.sub(r"[ \t]{2,}", " ", t).strip(" ·;\n\t ")
        return t

    for i, r in enumerate(resp.results, 1):
        title = _clean(r.title) or (r.entry_id or "").strip() or "(无标题)"
        snip = _clean(r.snippet)
        if len(snip) > 420:
            snip = snip[:417] + "…"
        lines.append(f"{i}. {title}  (score={r.score:.3f})")
        if snip:
            lines.append(f"   {snip}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def _ws_nl_hub_search_response(
    websocket: WebSocket,
    session_id: str,
    query: str,
    *,
    hub_mode: str,
) -> None:
    """hub_mode: kb | script → 流式下发说明 + llm_resp（脚本首条可一键采纳）。"""
    ai_card_id = f"aic_hub_{uuid.uuid4().hex[:16]}"
    first_command = ""
    try:
        storage = KnowledgeHubStorage.get_instance()
        search = KnowledgeHubSearch(storage)
        resp = search.search(SearchQuery(q=query.strip(), mode=hub_mode, limit=15))  # type: ignore[arg-type]
        explanation = _format_hub_search_plain(resp, hub_mode=hub_mode)
        if hub_mode == "script" and resp.results:
            ent = storage.get_script_entry(resp.results[0].entry_id)
            if ent and (ent.content or "").strip():
                first_command = (ent.content or "").strip()
    except Exception as e:
        logger.warning("KnowledgeHub 检索失败: %s", e)
        label = "知识库" if hub_mode == "kb" else "脚本库"
        explanation = f"【{label}】检索失败：{e}\n"

    _hub_risk, warn = (
        classify_command_risk(first_command) if first_command else ("LOW", "")
    )
    llm_resp = {
        "type": "llm_resp",
        "session_id": session_id,
        "explanation": explanation,
        "command": first_command,
        "dangerous": _hub_risk == "HIGH",
        "warning": warn,
        "confirm_required": _hub_risk in ("MEDIUM", "HIGH"),
        "should_execute": bool(first_command),
        "risk": _hub_risk,
        "ai_card_id": ai_card_id,
        "auto_executed": False,
        "nl_mode": hub_mode,
    }
    await stream_llm_text_chunks(
        websocket,
        session_id,
        explanation=explanation,
        llm_resp=llm_resp,
        stream_kind="hub_search",
    )


async def _ws_command_set_execute(
    websocket: WebSocket,
    session_id: str,
    msg: Dict[str, Any],
    output_buffer: List[str],
    trace_id: str,
) -> None:
    """合并命令一条下发 PTY；流式推送 capture 增量，结束时启发式 exit。"""
    raw = msg.get("data")
    data: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    combined = (data.get("combined_command") or "").strip()
    plan_id = (data.get("plan_id") or "").strip()
    if not combined:
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "data": "缺少 combined_command",
            }
        )
        return

    plan = session_mgr.get_terminal_plan(session_id)
    if plan_id:
        if not plan or plan.plan_id != plan_id:
            await websocket.send_json(
                {
                    "type": "error",
                    "session_id": session_id,
                    "data": "plan_id 与当前待批准计划不一致",
                }
            )
            return
        if plan.phase != "pending_approval":
            await websocket.send_json(
                {
                    "type": "error",
                    "session_id": session_id,
                    "data": "计划已开始或已结束，无法使用命令集执行",
                }
            )
            return

    await websocket.send_json(
        {
            "type": "command_set_started",
            "session_id": session_id,
            "plan_id": plan_id or (plan.plan_id if plan else ""),
        }
    )

    cap_mark = len(session_mgr.get_output_capture(session_id))
    session_mgr.reset_psrp_inject_batch(session_id)
    ok = await _guarded_shell_input_line(
        session_id,
        combined,
        output_buffer,
        websocket,
        trace_id,
        "ws_command_set",
    )
    if not ok:
        await websocket.send_json(
            {
                "type": "command_set_finished",
                "session_id": session_id,
                "plan_id": plan_id,
                "data": {
                    "exit_code": 1,
                    "stdout_summary": "",
                    "stderr_summary": None,
                    "status_heuristic": "fail",
                },
            }
        )
        await _repair_ws_broadcast(
            session_id,
            "repair_decision_prompt",
            {
                "countdown_sec": 8,
                "plan_id": plan_id,
                "hint": "执行未成功完成",
                "command": combined,
                "stdout_tail": "",
            },
        )
        return

    last = ""
    idle = 0
    # 预置资源监控等长链：放宽轮询窗口，避免空闲过早结束导致输出不全
    cmd_len = len(combined)
    max_rounds = 220 if cmd_len > 400 else 120
    # 输出停更后尽快结束等待（长链约 2.1s，普通约 1.4s），提示符出现可更早退出
    idle_limit = 6 if cmd_len > 400 else 4
    from chibycore.output_budget import (
        TERMINAL_CAPTURE_RING_MAX_CHARS,
        UI_WS_CHUNK_PREVIEW_CHARS,
        UI_WS_SUMMARY_TAIL_CHARS,
    )

    capture_cap = min(TERMINAL_CAPTURE_RING_MAX_CHARS, max(UI_WS_CHUNK_PREVIEW_CHARS, 32000))
    for _ in range(max_rounds):
        await asyncio.sleep(0.35)
        chunk = _output_capture_delta_since(
            session_id, cap_mark, max_chars=capture_cap
        )
        if chunk != last:
            last = chunk
            idle = 0
            await websocket.send_json(
                {
                    "type": "command_set_output",
                    "session_id": session_id,
                    "plan_id": plan_id,
                    "data": sanitize_terminal_text_for_ui(chunk[-UI_WS_CHUNK_PREVIEW_CHARS:]),
                }
            )
        else:
            idle += 1
            if idle >= 2 and _looks_like_shell_back_at_prompt(chunk):
                break
            if idle >= idle_limit:
                break

    last_stripped, codes = _tail_stripped_and_local_ps_codes(last)
    status = _heuristic_step_shell_status(last_stripped)
    exit_code: Optional[int]
    pair = session_mgr.consume_psrp_inject_batch_outcome(session_id)
    status_source = "heuristic"
    if pair is not None:
        st, code = pair
        status = st
        status_source = "psrp"
        if code is not None:
            exit_code = int(code)
        else:
            exit_code = 1 if st == "fail" else 0
    elif codes:
        status = "fail" if any(c != 0 for c in codes) else "pass"
        status_source = "local_ps_exit"
        bad = next((c for c in codes if c != 0), None)
        exit_code = int(bad) if bad is not None else 0
    elif status == "pass":
        exit_code = 0
    elif status == "fail":
        exit_code = 1
    else:
        exit_code = None
    # UI 折叠区：尽量给足；explain 异步推送，避免卡住「执行完成」
    ui_summary = sanitize_terminal_text_for_ui(
        last_stripped[-max(UI_WS_SUMMARY_TAIL_CHARS, 16000) :] if last_stripped else ""
    )
    explain_src = sanitize_terminal_text_for_ui(
        _sample_output_for_explain(last_stripped, max_chars=10000)
    )
    await websocket.send_json(
        {
            "type": "command_set_finished",
            "session_id": session_id,
            "plan_id": plan_id,
            "data": {
                "exit_code": exit_code,
                "stdout_summary": ui_summary,
                "stderr_summary": None,
                "status_heuristic": status,
                "status_source": status_source,
                "explain_pending": True,
                "explain_md": "",
                "explained": False,
            },
        }
    )
    if exit_code == 1 or status == "fail":
        await _repair_ws_broadcast(
            session_id,
            "repair_decision_prompt",
            {
                "countdown_sec": 8,
                "plan_id": plan_id,
                "hint": f"exit {exit_code} · {status}",
                "command": combined,
                "stdout_tail": sanitize_terminal_text_for_ui(last_stripped[-800:] if last_stripped else ""),
            },
        )

    if plan and plan_id and plan.plan_id == plan_id:
        session_mgr.clear_terminal_plan(session_id)

    # 结果说明后台生成：不阻塞「执行完成」卡片刷新
    asyncio.create_task(
        _push_command_set_explain_md(
            websocket,
            session_id=session_id,
            plan_id=plan_id,
            command=combined,
            output_tail=explain_src,
            status=status,
            exit_code=exit_code,
        )
    )


async def _push_command_set_explain_md(
    websocket: WebSocket,
    *,
    session_id: str,
    plan_id: str,
    command: str,
    output_tail: str,
    status: str,
    exit_code: Optional[int],
) -> None:
    """command_set 执行完成后异步推送「结果说明」。"""
    explain_md = ""
    try:
        explain_md = await _explain_command_output_md(
            session_id=session_id,
            command=command,
            output_tail=output_tail,
            status=status,
            exit_code=exit_code,
        )
    except Exception as exc:
        logger.warning("command_set explain failed: %s", exc)
        explain_md = (
            "**结论：命令已执行完毕。**\n\n"
            "- 结果梳理暂不可用，请展开上方「命令输出」查看原始内容。"
        )
    try:
        await websocket.send_json(
            {
                "type": "command_set_explain",
                "session_id": session_id,
                "plan_id": plan_id,
                "data": {
                    "explain_md": explain_md,
                    "explained": bool((explain_md or "").strip()),
                    "explain_pending": False,
                },
            }
        )
    except Exception as exc:
        logger.debug("command_set_explain push failed: %s", exc)


def _broadcast_host_label(session_id: str) -> str:
    sess = session_mgr.get_session(session_id)
    if not sess:
        return session_id
    host_id = getattr(sess, "host_id", None) or ""
    if host_id and host_id in _HOST_STORE:
        ho = _HOST_STORE[host_id]
        return f"{ho.name} ({ho.host})" if getattr(ho, "host", None) else ho.name
    title = (getattr(sess, "title", None) or "").strip()
    host = (getattr(sess, "host", None) or "").strip()
    if title and host:
        return f"{title} ({host})"
    return title or host or session_id


async def _wait_broadcast_output(
    session_id: str,
    cap_mark: int,
    *,
    max_wait_sec: float = 20.0,
) -> str:
    """等待终端回显稳定或回到提示符，返回增量文本。

    未见到任何输出前给予较长宽限（WinRM/慢 SSH 首包常 >1.5s），
    避免空闲计数过早结案导致「结果收不上来」。
    """
    last = ""
    idle = 0
    saw_any = False
    step = 0.35
    rounds = max(8, int(max_wait_sec / step))
    # 无输出时至少等约 4s 再判空闲超时
    min_rounds_before_empty_abort = max(10, int(4.0 / step))
    for i in range(rounds):
        await asyncio.sleep(step)
        chunk = _output_capture_delta_since(session_id, cap_mark, max_chars=12000)
        if chunk != last:
            last = chunk
            idle = 0
            if (chunk or "").strip():
                saw_any = True
        else:
            idle += 1
            if saw_any and idle >= 2 and _looks_like_shell_back_at_prompt(chunk):
                break
            if saw_any and idle >= 5:
                break
            if (
                not saw_any
                and (i + 1) >= min_rounds_before_empty_abort
                and idle >= 5
            ):
                break
    return last


async def _broadcast_one_session(
    *,
    initiator_session_id: str,
    target_session_id: str,
    command: str,
    websocket: WebSocket,
    trace_id: str,
    user_question: str,
    with_explain: bool = False,
):
    """注入命令并采集回显。默认不做 LLM 说明（由调用方先推进度再异步补 explain）。"""
    from chibyterm.broadcast_report import BroadcastHostResult

    _ = initiator_session_id
    _ = user_question

    label = _broadcast_host_label(target_session_id)
    result = BroadcastHostResult(
        session_id=target_session_id,
        host_label=label,
        command=(command or "").strip(),
    )
    sess = session_mgr.get_session(target_session_id)
    if not sess:
        result.status = "error"
        result.error = "会话不存在"
        return result
    if not session_mgr.has_active_shell(target_session_id):
        result.status = "error"
        result.error = "终端 Shell 未就绪（请先打开该主机 Tab 并确认已连接）"
        return result

    dummy_buf: List[str] = []
    session_mgr.reset_psrp_inject_batch(target_session_id)
    cap_mark = len(session_mgr.get_output_capture(target_session_id))
    injected = False
    for line in (command or "").split("\n"):
        sline = line.strip()
        if not sline:
            continue
        ok = await _guarded_shell_input_line(
            target_session_id,
            sline,
            dummy_buf,
            websocket,
            trace_id,
            "ws_broadcast",
        )
        if not ok:
            result.status = "blocked"
            result.error = "策略拒绝或注入失败"
            return result
        injected = True
    if not injected:
        result.status = "error"
        result.error = "命令为空"
        return result

    raw = await _wait_broadcast_output(target_session_id, cap_mark, max_wait_sec=20.0)
    stripped, codes = _tail_stripped_and_local_ps_codes(raw)
    status = _heuristic_step_shell_status(stripped)
    pair = session_mgr.consume_psrp_inject_batch_outcome(target_session_id)
    exit_code: Optional[int] = None
    if pair is not None:
        status, code = pair
        exit_code = int(code) if code is not None else (1 if status == "fail" else 0)
    elif codes:
        status = "fail" if any(c != 0 for c in codes) else "pass"
        exit_code = int(next((c for c in codes if c != 0), codes[-1]))

    ui_tail = sanitize_terminal_text_for_ui(
        _sample_output_for_explain(stripped, max_chars=8000)
    )
    result.stdout_tail = ui_tail
    # 未采到回显：不得标 pass，避免前端误开「生成报告」
    if not (ui_tail or "").strip():
        if status in ("pass", "unknown", ""):
            status = "error"
        result.error = (
            result.error
            or "未采集到终端输出（命令可能未执行、Shell 忙或回显超时）"
        )
        result.ok = False
    else:
        result.ok = status == "pass"
    result.status = status
    # 供后续 explain 使用（非序列化字段时挂在对象上）
    setattr(result, "_exit_code", exit_code)

    if with_explain:
        try:
            result.explain_md = await asyncio.wait_for(
                _explain_command_output_md(
                    session_id=target_session_id,
                    command=command,
                    output_tail=ui_tail,
                    status=status,
                    exit_code=exit_code,
                    user_question=user_question or command,
                ),
                timeout=45.0,
            )
        except Exception as exc:
            logger.warning("broadcast explain failed sid=%s: %s", target_session_id, exc)
            result.explain_md = ""
            if not result.error:
                result.error = f"结果说明失败: {exc}"[:200]
    return result


async def _run_broadcast_job(
    *,
    job_id: str,
    initiator_session_id: str,
    session_ids: List[str],
    command: str,
    websocket: WebSocket,
    trace_id: str,
    report_tone: Optional[str] = None,
    commands_by_session: Optional[Dict[str, str]] = None,
    nl_intent: str = "",
) -> None:
    """后台：并行采集各机会话 → 推送逐机结果 → 停在 exec_done（报告按需生成）。"""
    from chibyterm.broadcast_report import (
        BroadcastJob,
        compute_stats,
        store_broadcast_job,
    )
    from chibyterm.broadcast_settings import (
        load_broadcast_settings,
        normalize_report_tone,
    )

    user_q = (nl_intent or "").strip() or session_mgr.get_last_nl_text(
        initiator_session_id
    ) or ""
    tone = normalize_report_tone(
        report_tone
        if report_tone is not None
        else load_broadcast_settings().get("report_tone")
    )
    cmd_map = {
        str(k): str(v).strip()
        for k, v in (commands_by_session or {}).items()
        if str(v or "").strip()
    }
    display_cmd = (command or "").strip()
    if not display_cmd and cmd_map:
        uniq = list(dict.fromkeys(cmd_map.values()))
        display_cmd = " | ".join(uniq[:4]) + (" …" if len(uniq) > 4 else "")
    if not display_cmd and user_q:
        display_cmd = f"[fleet] {user_q[:120]}"

    host_ids: List[str] = []
    seen_hid: set = set()
    for tid in session_ids:
        sess = session_mgr.get_session(tid)
        hid = getattr(sess, "host_id", None) if sess else None
        if hid:
            hs = str(hid)
            if hs not in seen_hid:
                seen_hid.add(hs)
                host_ids.append(hs)

    job = BroadcastJob(
        job_id=job_id,
        command=display_cmd,
        initiator_session_id=initiator_session_id,
        session_ids=list(session_ids),
        phase="running",
        report_tone=tone,
        nl_intent=user_q,
        commands_by_session=dict(cmd_map),
        host_ids=host_ids,
    )
    store_broadcast_job(job)

    total = len(session_ids)
    done_count = 0
    done_lock = asyncio.Lock()
    push_lock = asyncio.Lock()  # 同一 initiator WS 串行推送，避免并行 send_json 丢消息

    def _cmd_for(tid: str) -> str:
        if tid in cmd_map:
            return cmd_map[tid]
        return (command or "").strip()

    async def _push_host(r, *, increment: bool = True) -> None:
        nonlocal done_count
        if increment:
            async with done_lock:
                done_count += 1
                progress = {"done": done_count, "total": total}
        else:
            async with done_lock:
                progress = {"done": done_count, "total": total}
        payload = {
            "type": "broadcast_host_result",
            "session_id": initiator_session_id,
            "job_id": job_id,
            "data": {
                "target_session_id": r.session_id,
                "host_label": r.host_label,
                "status": r.status,
                "ok": r.ok,
                "stdout_tail": (r.stdout_tail or "")[:6000],
                "explain_md": r.explain_md or "",
                "error": r.error or "",
                "command": r.command or _cmd_for(r.session_id),
                "progress": progress,
            },
        }
        async with push_lock:
            # 与 broadcast_started 一致：优先直推发起方 WS（并行 gather 下已由 push_lock 串行）
            try:
                await websocket.send_json(payload)
            except Exception as ex:
                logger.warning("broadcast_host_result direct ws push failed: %s", ex)
                try:
                    await session_mgr._broadcast(initiator_session_id, payload)
                except Exception as ex2:
                    logger.warning(
                        "broadcast_host_result _broadcast fallback failed: %s", ex2
                    )

    async def _explain_and_patch(r, one_cmd: str) -> None:
        """执行结果已推送后，再补 LLM 说明（超时不挡进度）。"""
        if (r.status or "") in ("error", "blocked") and not (r.stdout_tail or "").strip():
            return
        try:
            exit_code = getattr(r, "_exit_code", None)
            r.explain_md = await asyncio.wait_for(
                _explain_command_output_md(
                    session_id=r.session_id,
                    command=one_cmd or r.command,
                    output_tail=r.stdout_tail or "",
                    status=r.status or "unknown",
                    exit_code=exit_code,
                    user_question=user_q or one_cmd or r.command,
                ),
                timeout=45.0,
            )
        except Exception as exc:
            logger.warning("broadcast explain failed sid=%s: %s", r.session_id, exc)
            return
        if (r.explain_md or "").strip():
            await _push_host(r, increment=False)

    async def _one(tid: str):
        one_cmd = _cmd_for(tid)
        if not one_cmd:
            from chibyterm.broadcast_report import BroadcastHostResult

            r = BroadcastHostResult(
                session_id=tid,
                host_label=_broadcast_host_label(tid),
                status="error",
                error="无可用命令（该 OS 分段翻译失败）",
                command="",
            )
            await _push_host(r)
            return r
        try:
            r = await _broadcast_one_session(
                initiator_session_id=initiator_session_id,
                target_session_id=tid,
                command=one_cmd,
                websocket=websocket,
                trace_id=trace_id,
                user_question=user_q or one_cmd,
                with_explain=False,
            )
        except Exception as exc:
            from chibyterm.broadcast_report import BroadcastHostResult

            logger.warning("broadcast host task failed sid=%s: %s", tid, exc)
            r = BroadcastHostResult(
                session_id=tid,
                host_label=_broadcast_host_label(tid),
                status="error",
                error=str(exc)[:240],
                command=one_cmd,
            )
        # 先推进度/输出，再补 explain（避免 LLM 拖死「0/N」）
        await _push_host(r)
        try:
            await _explain_and_patch(r, one_cmd)
        except Exception as exc:
            logger.warning("broadcast explain patch failed sid=%s: %s", tid, exc)
        return r

    gathered = await asyncio.gather(*[_one(tid) for tid in session_ids])
    job.results = list(gathered)
    job.stats = compute_stats(job.results)
    job.phase = "exec_done"
    store_broadcast_job(job)

    try:
        from chibycore.platform_audit import append_platform_audit

        stats = job.stats if isinstance(job.stats, dict) else {}
        ok_n = int(stats.get("ok") or stats.get("success") or 0)
        fail_n = int(stats.get("fail") or stats.get("failed") or 0)
        total_n = int(stats.get("total") or len(job.results) or 0)
        if fail_n <= 0 and ok_n > 0:
            outcome = "success"
        elif ok_n <= 0 and fail_n > 0:
            outcome = "failure"
        elif ok_n > 0 and fail_n > 0:
            outcome = "partial"
        else:
            outcome = "success"
        append_platform_audit(
            "fleet_execute",
            trace_id=str(trace_id or job_id or ""),
            host_ids=host_ids,
            command=display_cmd,
            result_summary=(
                f"Fleet 完成 {ok_n}/{total_n} 成功"
                + (f" · 失败 {fail_n}" if fail_n else "")
                + (f" · {user_q[:80]}" if user_q else "")
            ),
            outcome=outcome,
            metadata={
                "job_id": job_id,
                "session_ids": list(session_ids),
                "stats": stats,
                "nl_intent": (user_q or "")[:200],
                "report_tone": tone,
            },
            mirror_mobile=True,
        )
        try:
            from chibycore.usage_metrics import refresh_usage_metrics

            refresh_usage_metrics()
        except Exception:
            logger.debug("usage metrics refresh skipped", exc_info=True)
    except Exception:
        logger.debug("fleet_execute audit skipped", exc_info=True)

    done_payload = {
        "type": "broadcast_exec_done",
        "session_id": initiator_session_id,
        "job_id": job_id,
        "data": {
            "stats": job.stats,
            "command": display_cmd,
            "nl_intent": user_q,
            "report_tone": tone,
            "commands_by_session": dict(cmd_map),
            "host_ids": host_ids,
            "session_ids": list(session_ids),
        },
    }
    async with push_lock:
        try:
            await websocket.send_json(done_payload)
        except Exception as ex:
            logger.warning("broadcast_exec_done direct ws push failed: %s", ex)
            try:
                await session_mgr._broadcast(initiator_session_id, done_payload)
            except Exception as ex2:
                logger.warning(
                    "broadcast_exec_done _broadcast fallback failed: %s", ex2
                )


async def _generate_broadcast_report_for_job(
    *,
    job_id: str,
    report_tone: Optional[str] = None,
    push_ws: bool = True,
) -> Dict[str, Any]:
    """按需生成总体分析报告；可选经 WS 推送 broadcast_report。"""
    from chibyterm.broadcast_report import (
        comparative_report_md,
        get_broadcast_job,
        rule_comparative_report,
        store_broadcast_job,
    )
    from chibyterm.broadcast_settings import (
        load_broadcast_settings,
        normalize_report_tone,
        tone_label,
    )

    job = get_broadcast_job(job_id)
    if not job:
        err_payload = {"ok": False, "error": "job_not_found", "job_id": job_id}
        if push_ws:
            try:
                # initiator 未知时无法推送；尽量用 job_id 无匹配
                pass
            except Exception:
                pass
        return err_payload
    if job.phase == "running":
        err_payload = {
            "ok": False,
            "error": "still_running",
            "job_id": job_id,
        }
        return err_payload

    ui_locale = session_mgr.get_ui_locale(job.initiator_session_id) or "zh-CN"
    tone = normalize_report_tone(
        report_tone
        if report_tone is not None
        else (job.report_tone or load_broadcast_settings().get("report_tone"))
    )
    user_q = (job.nl_intent or "").strip() or job.command
    display_cmd = job.command or user_q

    job.phase = "report_pending"
    job.report_tone = tone
    store_broadcast_job(job)

    if push_ws:
        try:
            await session_mgr._broadcast(
                job.initiator_session_id,
                {
                    "type": "broadcast_report_pending",
                    "session_id": job.initiator_session_id,
                    "job_id": job_id,
                    "data": {"pending": True},
                },
            )
        except Exception:
            pass

    try:
        report = await asyncio.to_thread(
            comparative_report_md,
            command=display_cmd,
            user_question=user_q or display_cmd,
            results=job.results,
            ui_locale=ui_locale,
            report_tone=tone,
        )
    except Exception as exc:
        logger.warning("broadcast comparative report failed: %s", exc)
        report = rule_comparative_report(
            command=display_cmd,
            results=job.results,
            ui_locale=ui_locale,
            report_tone=tone,
        )

    job.report_md = report or ""
    job.phase = "done"
    job.report_tone = tone
    store_broadcast_job(job)

    related_cases: List[Dict[str, Any]] = []
    kb_prefill: Dict[str, Any] = {}
    try:
        from chibyterm.fleet_knowledge import (
            prefill_fleet_kb_template,
            search_fleet_related_cases,
        )

        host_ids = [
            str(x) for x in (getattr(job, "host_ids", None) or []) if str(x).strip()
        ]
        scope_name = f"已选 {len(host_ids)} 台" if host_ids else ""
        related_cases = search_fleet_related_cases(
            nl_intent=user_q,
            command=display_cmd,
            report_md=job.report_md or "",
            host_scope=scope_name,
            limit=3,
        )
        kb_prefill = prefill_fleet_kb_template(
            nl_intent=user_q,
            command=display_cmd,
            report_md=job.report_md or "",
            host_scope=scope_name,
            stats=job.stats if isinstance(job.stats, dict) else {},
            job_id=job_id,
            report_tone=tone,
        )
    except Exception:
        logger.debug("fleet related_cases skipped", exc_info=True)

    payload = {
        "ok": True,
        "report_md": job.report_md,
        "stats": job.stats,
        "command": display_cmd,
        "nl_intent": user_q,
        "report_tone": tone,
        "report_tone_label": tone_label(tone, ui_locale),
        "job_id": job_id,
        "related_cases": related_cases,
        "kb_prefill": kb_prefill,
    }
    if push_ws:
        try:
            await session_mgr._broadcast(
                job.initiator_session_id,
                {
                    "type": "broadcast_report",
                    "session_id": job.initiator_session_id,
                    "job_id": job_id,
                    "data": {
                        "report_md": job.report_md,
                        "stats": job.stats,
                        "command": display_cmd,
                        "nl_intent": user_q,
                        "report_tone": tone,
                        "report_tone_label": tone_label(tone, ui_locale),
                        "related_cases": related_cases,
                        "kb_prefill": kb_prefill,
                    },
                },
            )
        except Exception as ex:
            logger.debug("broadcast_report push: %s", ex)
    return payload


async def _run_broadcast_job_oneshot(
    *,
    job_id: str,
    initiator_session_id: str,
    host_ids: List[str],
    commands_by_host: Dict[str, str],
    websocket: WebSocket,
    trace_id: str,
    report_tone: Optional[str] = None,
    nl_intent: str = "",
) -> None:
    """Fleet 范围选机：按 host_ids oneshot 执行，进度推到发起方会话（不打开目标 Tab）。"""
    from chibyterm.broadcast_report import (
        BroadcastHostResult,
        BroadcastJob,
        compute_stats,
        store_broadcast_job,
    )
    from chibyterm.broadcast_settings import (
        load_broadcast_settings,
        normalize_report_tone,
    )
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

    user_q = (nl_intent or "").strip()
    tone = normalize_report_tone(
        report_tone
        if report_tone is not None
        else load_broadcast_settings().get("report_tone")
    )
    cmd_map = {
        str(k): str(v).strip()
        for k, v in (commands_by_host or {}).items()
        if str(v or "").strip()
    }
    ids = [str(x).strip() for x in host_ids if str(x).strip()]
    display_cmd = ""
    if cmd_map:
        uniq = list(dict.fromkeys(cmd_map.values()))
        display_cmd = " | ".join(uniq[:4]) + (" …" if len(uniq) > 4 else "")
    if not display_cmd and user_q:
        display_cmd = f"[fleet] {user_q[:120]}"

    job = BroadcastJob(
        job_id=job_id,
        command=display_cmd,
        initiator_session_id=initiator_session_id,
        session_ids=list(ids),  # oneshot：槽位存 host_id
        phase="running",
        report_tone=tone,
        nl_intent=user_q,
        commands_by_session=dict(cmd_map),
        host_ids=list(ids),
    )
    store_broadcast_job(job)

    total = len(ids)
    done_count = 0
    done_lock = asyncio.Lock()
    push_lock = asyncio.Lock()

    def _label(hid: str) -> str:
        h = _HOST_STORE.get(hid)
        if not h:
            return hid
        name = str(getattr(h, "name", "") or "").strip()
        addr = str(getattr(h, "host", "") or "").strip()
        if name and addr:
            return f"{name} ({addr})"
        return name or addr or hid

    def _exec_host(hid: str, cmd: str) -> BroadcastHostResult:
        r = BroadcastHostResult(
            session_id=hid,
            host_label=_label(hid),
            command=cmd,
        )
        h = _HOST_STORE.get(hid)
        if not h:
            r.status = "error"
            r.error = "主机不存在"
            return r
        if not cmd:
            r.status = "error"
            r.error = "无可用命令"
            return r
        try:
            gate = gateway_evaluate(
                ExecutionRequest(
                    trace_id=f"{trace_id}_{hid[:8]}",
                    session_id=f"broadcast_oneshot:{job_id}",
                    command_line=cmd,
                    source="broadcast_oneshot",
                    conn_type=str(
                        getattr(
                            getattr(h, "conn_type", None),
                            "value",
                            getattr(h, "conn_type", ""),
                        )
                        or "ssh"
                    ),
                    host_id=hid,
                    plan_id=None,
                )
            )
            if not gate.allowed:
                r.status = "blocked"
                r.error = str(getattr(gate, "reason", "") or "blocked")[:240]
                return r
            ex = build_oneshot_from_pydantic_host(h)
            ex.connect()
            try:
                out = ex.run_command(cmd)
            finally:
                ex.close()
            code = getattr(out, "exit_code", None)
            stdout = (getattr(out, "stdout", None) or "")[-4000:]
            stderr = (getattr(out, "stderr", None) or "")[-1500:]
            r.stdout_tail = (stdout + ("\n" + stderr if stderr else "")).strip()
            setattr(r, "_exit_code", code)
            if code in (0, None):
                r.status = "pass"
                r.ok = True
            else:
                r.status = "fail"
                r.error = f"exit_code={code}"
            return r
        except Exception as exc:
            r.status = "error"
            r.error = str(exc)[:240]
            return r

    async def _push_host(r: Any, *, increment: bool = True) -> None:
        nonlocal done_count
        if increment:
            async with done_lock:
                done_count += 1
                progress = {"done": done_count, "total": total}
        else:
            async with done_lock:
                progress = {"done": done_count, "total": total}
        payload = {
            "type": "broadcast_host_result",
            "session_id": initiator_session_id,
            "job_id": job_id,
            "data": {
                "target_session_id": r.session_id,
                "host_label": r.host_label,
                "status": r.status,
                "ok": r.ok,
                "stdout_tail": (r.stdout_tail or "")[:6000],
                "explain_md": r.explain_md or "",
                "error": r.error or "",
                "command": r.command or cmd_map.get(str(r.session_id), ""),
                "progress": progress,
            },
        }
        async with push_lock:
            try:
                await websocket.send_json(payload)
            except Exception as ex:
                logger.warning("oneshot broadcast_host_result ws push failed: %s", ex)
                try:
                    await session_mgr._broadcast(initiator_session_id, payload)
                except Exception as ex2:
                    logger.warning("oneshot broadcast_host_result fallback failed: %s", ex2)

    async def _one(hid: str):
        cmd = cmd_map.get(hid, "")
        r = await asyncio.to_thread(_exec_host, hid, cmd)
        await _push_host(r)
        return r

    gathered = await asyncio.gather(*[_one(hid) for hid in ids])
    job.results = list(gathered)
    job.stats = compute_stats(job.results)
    job.phase = "exec_done"
    store_broadcast_job(job)

    try:
        await websocket.send_json(
            {
                "type": "broadcast_exec_done",
                "session_id": initiator_session_id,
                "job_id": job_id,
                "data": {
                    "stats": job.stats,
                    "command": display_cmd,
                    "nl_intent": user_q,
                    "execution_mode": "oneshot",
                },
            }
        )
    except Exception as ex:
        logger.debug("broadcast_exec_done oneshot push: %s", ex)
        try:
            await session_mgr._broadcast(
                initiator_session_id,
                {
                    "type": "broadcast_exec_done",
                    "session_id": initiator_session_id,
                    "job_id": job_id,
                    "data": {"stats": job.stats, "command": display_cmd},
                },
            )
        except Exception:
            pass


async def _run_one_broadcast_schedule(schedule: Dict[str, Any]) -> None:
    """到期任务：按 host_ids oneshot 执行 → 可选生成报告 → 更新 schedule 状态。"""
    from chibyterm.broadcast_report import (
        BroadcastHostResult,
        comparative_report_md,
        compute_stats,
        rule_comparative_report,
    )
    from chibyterm.broadcast_schedule import mark_schedule_ran, notify_stub
    from chibyterm.broadcast_settings import normalize_report_tone
    from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
    from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

    sch_id = str(schedule.get("id") or "")
    host_ids = [str(x) for x in (schedule.get("host_ids") or []) if str(x).strip()]
    nl_intent = str(schedule.get("nl_intent") or "").strip()
    cmds_seg = dict(schedule.get("commands_by_segment") or {})
    tone = normalize_report_tone(schedule.get("report_tone"))
    fail_policy = str(schedule.get("fail_policy") or "continue")
    name = str(schedule.get("name") or sch_id)

    if not host_ids:
        note = notify_stub(schedule, status="no_hosts")
        mark_schedule_ran(sch_id, status="error: no host_ids", notify_note=note)
        return

    # 分段：winrm → powershell，其它 → unix
    segments: Dict[str, List[Any]] = {"windows_powershell": [], "unix_linux": []}
    for hid in host_ids:
        h = _HOST_STORE.get(hid)
        if not h:
            continue
        ct = getattr(h, "conn_type", None)
        conn = str(getattr(ct, "value", ct) or "ssh").lower()
        key = "windows_powershell" if conn == "winrm" else "unix_linux"
        segments[key].append(h)

    # 每段一条命令
    seg_cmds: Dict[str, str] = {}
    for sk, hosts in segments.items():
        if not hosts:
            continue
        if sk in cmds_seg and str(cmds_seg[sk]).strip():
            seg_cmds[sk] = str(cmds_seg[sk]).strip()
            continue
        if not nl_intent or prompt_processor is None:
            continue
        sp = "powershell" if sk.startswith("windows") else "unix"
        try:
            pr = await asyncio.to_thread(
                prompt_processor.process,
                f"用户运维意图：{nl_intent}\n请输出一条适合 {sp} 的可执行命令。",
                shell_profile=sp,
                runtime_hint=f"Fleet schedule segment={sk}",
                ui_locale="zh-CN",
            )
            cmd = str(getattr(pr, "command", None) or "").strip()
            if cmd:
                seg_cmds[sk] = cmd
        except Exception as exc:
            logger.warning("schedule translate failed %s: %s", sk, exc)

    results: List[BroadcastHostResult] = []

    def _exec_host(h: Any, cmd: str, sk: str) -> BroadcastHostResult:
        hid = str(getattr(h, "id", "") or "")
        label = f"{getattr(h, 'name', '')} ({getattr(h, 'host', '')})".strip()
        r = BroadcastHostResult(
            session_id=hid,
            host_label=label or hid,
            command=cmd,
        )
        if not cmd:
            r.status = "error"
            r.error = "无可用命令"
            return r
        try:
            gate = gateway_evaluate(
                ExecutionRequest(
                    trace_id=f"sch_{sch_id}_{hid[:8]}",
                    session_id=f"broadcast_schedule:{sch_id}",
                    command_line=cmd,
                    source="broadcast_schedule",
                    conn_type=str(
                        getattr(getattr(h, "conn_type", None), "value", getattr(h, "conn_type", ""))
                        or "ssh"
                    ),
                    host_id=hid,
                    plan_id=None,
                )
            )
            if not gate.allowed:
                r.status = "blocked"
                r.error = str(getattr(gate, "reason", "") or "blocked")[:240]
                return r
            ex = build_oneshot_from_pydantic_host(h)
            ex.connect()
            try:
                out = ex.run_command(cmd)
            finally:
                ex.close()
            code = getattr(out, "exit_code", None)
            stdout = (getattr(out, "stdout", None) or "")[-4000:]
            stderr = (getattr(out, "stderr", None) or "")[-1500:]
            r.stdout_tail = (stdout + ("\n" + stderr if stderr else "")).strip()
            if code in (0, None):
                r.status = "pass"
                r.ok = True
            else:
                r.status = "fail"
                r.error = f"exit_code={code}"
            return r
        except Exception as exc:
            r.status = "error"
            r.error = str(exc)[:240]
            return r

    loop = asyncio.get_event_loop()
    for sk, hosts in segments.items():
        cmd = seg_cmds.get(sk, "")
        for h in hosts:
            r = await loop.run_in_executor(None, _exec_host, h, cmd, sk)
            results.append(r)

    stats = compute_stats(results)
    status_line = f"ok={stats['ok']} fail={stats['fail']} total={stats['total']}"

    skip_report = fail_policy == "all_ok_only" and stats["fail"] > 0
    report_md = ""
    if not skip_report and results:
        display = nl_intent or " | ".join(seg_cmds.values()) or name
        try:
            report_md = await asyncio.to_thread(
                comparative_report_md,
                command=display,
                user_question=nl_intent or display,
                results=results,
                ui_locale="zh-CN",
                report_tone=tone,
            )
        except Exception:
            report_md = rule_comparative_report(
                command=display,
                results=results,
                ui_locale="zh-CN",
                report_tone=tone,
            )
    elif skip_report:
        status_line = "skipped_report:" + status_line

    note = notify_stub(schedule, status=status_line)
    knowledge_hint = None
    try:
        from chibycore.platform_audit import append_platform_audit, query_platform_audit
        from chibyterm.fleet_knowledge import detect_repeat_failure_pattern

        st_lower = (status_line or "").lower()
        if "error" in st_lower or "fail" in st_lower:
            outcome = "failure"
        elif "partial" in st_lower or "skipped" in st_lower:
            outcome = "partial"
        else:
            outcome = "success"
        append_platform_audit(
            "scheduled_task_run",
            trace_id=f"sch_{sch_id}",
            host_ids=host_ids,
            command=(nl_intent or name or "")[:200],
            result_summary=f"定时任务 {name}: {status_line}"[:400],
            outcome=outcome,
            metadata={
                "schedule_id": sch_id,
                "name": name,
                "status": status_line,
                "has_report": bool(report_md),
            },
            mirror_mobile=True,
        )
        if outcome in ("failure", "partial"):
            recent = query_platform_audit(
                limit=80,
                event_type="scheduled_task_run",
            )
            # 仅看本 schedule 的失败序列
            scoped = []
            for ev in recent:
                meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
                if str(meta.get("schedule_id") or "") == sch_id:
                    scoped.append(ev)
            knowledge_hint = detect_repeat_failure_pattern(scoped, min_repeats=3)
    except Exception:
        logger.debug("scheduled_task_run audit/hint skipped", exc_info=True)
    mark_schedule_ran(
        sch_id,
        status=status_line,
        report_md=report_md or "",
        notify_note=note,
        knowledge_hint=knowledge_hint,
    )
    logger.info("broadcast schedule ran id=%s name=%s %s", sch_id, name, status_line)


async def _broadcast_schedule_loop() -> None:
    """进程内定时扫描（约 30s）。"""
    from chibyterm.broadcast_schedule import list_due_schedules

    while True:
        try:
            due = list_due_schedules()
            for sch in due:
                try:
                    await _run_one_broadcast_schedule(sch)
                except Exception as exc:
                    logger.warning("schedule run failed %s: %s", sch.get("id"), exc)
                    try:
                        from chibyterm.broadcast_schedule import mark_schedule_ran, notify_stub

                        note = notify_stub(sch, status=f"error:{exc}")
                        mark_schedule_ran(
                            str(sch.get("id")),
                            status=f"error:{exc}"[:240],
                            notify_note=note,
                        )
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("schedule loop tick: %s", exc)
        await asyncio.sleep(30)


# ═══════════════════════════════════════════════════════════════════════════
#  WebSocket 终端
# ═══════════════════════════════════════════════════════════════════════════

@app.websocket("/ws/terminal/{session_id}")
async def ws_terminal(websocket: WebSocket, session_id: str):
    """WebSocket 终端交互。

    客户端 → 服务端：
      {"type": "set_target_os", "target_os": "linux"}   # 与底部栏一致，可选同步 PATCH 结果
      {"type": "input",   "data": "ls -la\\n"}
      {"type": "resize",  "width": 120, "height": 30}
      {"type": "llm",     "data": "查看内存", "mode": "auto"|"plan",
       "nl_mode": "agent"|"shell"|"knowledge"|"script",   # 可选；shell+auto=直接下发（前端）；script/knowledge 仅检索/脚本库；agent+plan=计划链
       "model": "gpt-4o",   # 可选；与下拉同步，当前仍以服务端 llm_config 为准
       "params": {"temperature":0.1,"max_tokens":2048}}   # 可选；单次 Agent 请求覆盖采样参数
      {"type": "command_set_execute", "data": {"plan_id":"plt_...","combined_command":"..."}}
      {"type": "cancel_repair", "data": {"repair_job_id": "cl_..."}}   # 中止当前 closure-execute 流（与 SSE meta 中 repair_job_id 一致）
      {"type": "approve_plan", "plan_id": "plt_...", "style": "gated"|"batch"}
      # 批准后默认走意图清单（OPS_INTENT_CHECKLIST=0 可关）→ intent_checklist_progress / plan_finished
      {"type": "intent_checklist_progress", "intent":"...", "completed":1, "total":3, "status":"running", "items":[...]}
      {"type": "step_ok", "plan_id": "plt_...", "step_index": 0, "verdict": "continue"|"retry"|"abort",
       "retry_kind": "ai|repeat（仅 verdict=retry；repeat=原命令再执行；ai=合并 retry_user_note 后让 AI 重算本步，可无 retry_user_note）",
       "retry_user_note": "可选，配合 retry_kind=ai"}
      {"type": "terminate_step", "plan_id": "plt_...", "step_index": 0}  # 请求终止本步远端命令（当前占位，服务端应答 terminate_step_ack）
      {"type": "cancel_plan", "plan_id": "plt_..."}   # plan_id 可省略则取消当前会话计划
      {"type": "confirm", "data": "yes", "plan_id": "plt_..."}   # 计划内危险确认（可选 plan_id）
      {"type": "confirm", "data": "yes"}                        # 兼容 auto 模式危险命令
      {"type": "exec",    "command": "..."}
      {"type": "exec_broadcast", "session_ids": ["sid1","sid2"], "command": "uptime", "job_id": "可选"}
          # 群发后异步推送 broadcast_started / broadcast_host_result / broadcast_report
      {"type": "plan_edit", "plan_id": "plt_...", "step_index": 0, "new_command": "ls -la"}
      {"type": "plan_edit_batch", "plan_id": "plt_...", "edits": [{"step_index":0,"new_command":"ls"}]}
      {"type": "ping"}

    服务端 → 客户端：
      {"type": "session_meta", "target_os": "...", "os_options": [{"id","label"}, ...]}  # 连接后立即下发
      {"type": "ai_stream_start"|"ai_stream_delta"|"ai_stream_end"}  # AI 文本流式（字段含 message_id/stream_id/node_id/seq；结束帧带 llm_resp）
      {"type": "output"|"status"|"error"|"llm_resp"|"llm_plan"|"plan_status"|"plan_step"|"plan_progress"}
          # llm_plan 可含 command_set：合并命令、风险等级等
      {"type": "command_set_started"|"command_set_output"|"command_set_finished", ...}
      {"type": "plan_danger"|"plan_finished"|"plan_aborted"|"plan_cancelled"|"verification"|"pong"|"terminate_step_ack"}
      {"type": "ping", "session_id": "..."}   # 服务端定时心跳（JSON）；客户端应回复 {"type":"pong"}
      {"type": "session_error", "reason": "shell_exited"|..., "detail": "..."}  # Shell 失效或服务端主动关闭
      {"type": "step_command_result", "plan_id", "step_index", "command", "output_tail", "status": "pass"|"fail"|"unknown"}
          # 计划内每步真实命令执行后的终端捕获摘要 + 启发式成败（与 verification 独立）
      {"type": "plan_retry_notice", "plan_id", "step_index", "refined", "message", "command_preview"}
          # 本步重试：AI 重算 / 重复执行 / 失败回退等说明（右侧聊天）
      {"type": "llm_command_result", "ai_card_id", "command", "output_tail", "status"}
          # 单次 LLM：右侧 exec（llm_capture）或 auto 直连执行后的捕获摘要
    # llm_resp 另含 ai_card_id（与右侧卡 data-ai-card 一致）、auto_executed（为 true 表示已服务端直跑命令）
    """
    if ui_auth_enabled():
        user = ui_session_user(websocket.cookies.get(UI_AUTH_COOKIE))
        if not user:
            await websocket.close(code=4401)
            return
    await websocket.accept()
    session = session_mgr.get_session(session_id)
    if not session:
        await websocket.send_json({"type": "error", "session_id": session_id, "data": "会话不存在"})
        await websocket.close()
        return

    session_mgr.register_ws(session_id, websocket)

    await websocket.send_json(session_meta_payload(session_id, session))

    shutdown = asyncio.Event()
    ping_task: Optional[asyncio.Task] = None
    health_task: Optional[asyncio.Task] = None

    try:
        # 自动启动 shell
        if session.status in (SessionStatus.PENDING, SessionStatus.DISCONNECTED):
            started = await session_mgr.start_shell(session_id)
            if not started:
                err_text = (session.last_error or "").strip() or "Shell 启动失败，请检查系统环境"
                await websocket.send_json({
                    "type": "error", "session_id": session_id,
                    "data": err_text,
                })
                return
            # SSH：后台探测发行版命令族（不堵欢迎语 / 首屏输入）
            try:
                hid = (getattr(session, "host_id", None) or "").strip()
                if hid and session.conn_type == ConnType.SSH:
                    asyncio.create_task(
                        _maybe_probe_host_distro_async(
                            hid, probe_source="session_connect", force=False
                        )
                    )
            except Exception as _probe_ex:
                logger.debug("schedule distro probe skipped: %s", _probe_ex)

            # 不再向终端写入 ChibyTerm 欢迎横幅 / 剪贴板提示（直出远端 MOTD / 提示符）

        # 无论新启还是重连已有 Shell：统一告知前端「可交互」
        if session.status == SessionStatus.CONNECTED:
            await websocket.send_json(
                {
                    "type": "status",
                    "session_id": session_id,
                    "status": "connected",
                }
            )

        # 键盘输入缓冲（供 LLM 辅助）；终端回显另见 session_mgr.get_output_capture
        output_buffer: List[str] = []
        ws_trace_id = str(uuid.uuid4())

        async def _ws_periodic_ping() -> None:
            """定时 JSON ping，便于 NAT 保活并配合客户端 pong。"""
            try:
                while not shutdown.is_set():
                    await asyncio.sleep(25)
                    try:
                        await websocket.send_json(
                            {"type": "ping", "session_id": session_id}
                        )
                    except Exception:
                        shutdown.set()
                        return
            except asyncio.CancelledError:
                raise

        async def _ws_shell_health() -> None:
            """检测 Shell 进程是否仍存活；异常退出时推送 session_error 并关闭连接。"""
            try:
                while not shutdown.is_set():
                    await asyncio.sleep(2)
                    sess = session_mgr.get_session(session_id)
                    if not sess:
                        return
                    if sess.status != SessionStatus.CONNECTED:
                        continue
                    if not session_mgr.has_active_shell(session_id):
                        continue
                    if session_mgr.shell_is_alive(session_id):
                        continue
                    try:
                        await session_mgr.broadcast_session_error(
                            session_id,
                            "shell_exited",
                            "Shell 进程已结束或后端连接已失效",
                        )
                    except Exception as ex:
                        logger.debug("broadcast_session_error: %s", ex)
                    try:
                        await session_mgr.detach_dead_shell(session_id)
                    except Exception:
                        pass
                    shutdown.set()
                    try:
                        await websocket.close(code=4000)
                    except Exception:
                        pass
                    return
            except asyncio.CancelledError:
                raise

        ping_task = asyncio.create_task(_ws_periodic_ping())
        health_task = asyncio.create_task(_ws_shell_health())

        # 等待用户消息
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=300)
            except asyncio.TimeoutError:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "session_id": session_id})
                continue

            if msg_type == "set_target_os":
                os_id = (msg.get("target_os") or "").strip()
                if os_id in ALLOWED_TARGET_OS:
                    session_mgr.update_session(session_id, target_os=os_id)
                    sess = session_mgr.get_session(session_id)
                    if sess:
                        await websocket.send_json(session_meta_payload(session_id, sess))
                continue

            if msg_type == "cancel_repair":
                raw_d = msg.get("data")
                d = raw_d if isinstance(raw_d, dict) else {}
                rid = (d.get("repair_job_id") or "").strip()
                if rid:
                    _repair_cancel_job(rid)
                continue

            if msg_type == "interrupt_think":
                scope = str(msg.get("scope") or "llm").strip() or "llm"
                await websocket.send_json(
                    {
                        "type": "interrupt_think_ack",
                        "session_id": session_id,
                        "scope": scope,
                        "accepted": False,
                        "detail": "占位帧：服务端未联动取消流水线；前端应已停止打字机并发送新 NL。",
                    }
                )
                continue

            if msg_type in ("set_ui_locale", "ui_locale"):
                from chibyterm.ui_locale import normalize_ui_locale

                loc = normalize_ui_locale(
                    msg.get("ui_locale") or msg.get("locale") or msg.get("data")
                )
                session_mgr.set_ui_locale(session_id, loc)
                await websocket.send_json(
                    {
                        "type": "ui_locale_ack",
                        "session_id": session_id,
                        "ui_locale": loc,
                    }
                )
                continue

            if msg_type == "input":
                # JSON 里若显式 "data": null，msg.get("data","") 仍为 None，会导致丢按键
                raw_in = msg.get("data")
                if raw_in is None:
                    data = ""
                elif isinstance(raw_in, str):
                    data = raw_in
                else:
                    data = str(raw_in)
                if len(data) > 0:
                    output_buffer.append(data)
                    if len(output_buffer) > 50:
                        output_buffer.pop(0)
                    await session_mgr.shell_input(session_id, data)

            elif msg_type == "resize":
                w = msg.get("width", 80)
                h = msg.get("height", 24)
                await session_mgr.shell_resize(session_id, w, h)

            elif msg_type == "llm":
                user_text = (msg.get("data") or "").strip()
                if user_text:
                    session_mgr.set_last_nl_text(session_id, user_text)
                from chibyterm.ui_locale import normalize_ui_locale

                ui_locale = normalize_ui_locale(
                    msg.get("ui_locale") or msg.get("locale") or session_mgr.get_ui_locale(session_id)
                )
                session_mgr.set_ui_locale(session_id, ui_locale)
                cap = session_mgr.get_output_capture(session_id)
                context = (cap[-6000:] + "\n---\n" + "".join(output_buffer[-20:]))[-8000:]
                mode = (msg.get("mode") or "auto").lower().strip()
                nl_mode = (msg.get("nl_mode") or "agent").lower().strip()
                if nl_mode in ("shell", "direct_shell", "direct"):
                    mode = "auto"
                raw_params = msg.get("params")
                llm_params: Optional[Dict[str, Any]] = None
                if isinstance(raw_params, dict):
                    llm_params = raw_params

                if nl_mode in ("knowledge", "kb", "知识库", "知识"):
                    await _ws_nl_hub_search_response(
                        websocket, session_id, user_text, hub_mode="kb"
                    )
                    continue
                if nl_mode in ("script", "scripts", "脚本库", "脚本"):
                    await _ws_nl_hub_search_response(
                        websocket, session_id, user_text, hub_mode="script"
                    )
                    continue

                if mode == "plan":
                    from chibyterm.chain_bridge import try_build_chain_plan

                    try:
                        pack = try_build_chain_plan(session, user_text)
                    except Exception as e:
                        logger.warning("任务链匹配/展开失败，回退 LLM: %s", e)
                        pack = None
                    if pack:
                        steps, expl, chain_id = pack
                        # 计划模式只下发 llm_plan，不再先发 llm_resp，避免「执行」与「计划预览」两次确认框
                        ex = session_mgr.get_terminal_plan(session_id)
                        if ex and ex.phase in ("running", "awaiting_step_ok", "awaiting_danger_confirm"):
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "session_id": session_id,
                                    "data": "当前有进行中的执行计划，请先完成、退出计划或取消后再发起新的自然语言计划。",
                                }
                            )
                            continue
                        if ex and ex.phase == "pending_approval":
                            session_mgr.clear_terminal_plan(session_id)
                        pid = new_plan_id()
                        await stream_plan_preview_text(
                            websocket,
                            session_id,
                            explanation=expl,
                            plan_id=pid,
                        )
                        plan = PlanRuntime(
                            plan_id=pid,
                            explanation=expl,
                            source="chain",
                            steps=steps,
                            chain_id=chain_id,
                            phase="pending_approval",
                            intent=user_text or expl or "",
                        )
                        session_mgr.set_terminal_plan(session_id, plan)
                        sess = session_mgr.get_session(session_id)
                        await websocket.send_json(
                            enrich_llm_plan_payload(
                                sess,
                                {
                                    "type": "llm_plan",
                                    "session_id": session_id,
                                    "plan_id": pid,
                                    "explanation": expl,
                                    "steps": steps,
                                    "warning": "",
                                    "source": "chain",
                                    "chain_id": chain_id,
                                },
                            )
                        )
                        continue

                _term_session = session_mgr.get_session(session_id)
                result: PromptResult = prompt_processor.process(
                    user_text,
                    context,
                    runtime_hint=_runtime_hint_for_session(_term_session),
                    shell_profile=resolve_shell_profile(_term_session).value,
                    llm_params=llm_params,
                    ui_locale=ui_locale,
                )
                result = _sanitize_llm_prompt_result(result)

                if mode == "plan":
                    ex = session_mgr.get_terminal_plan(session_id)
                    if ex and ex.phase in ("running", "awaiting_step_ok", "awaiting_danger_confirm"):
                        await websocket.send_json(
                            {
                                "type": "error",
                                "session_id": session_id,
                                "data": "当前有进行中的执行计划，请先完成、退出计划或取消后再发起新的自然语言计划。",
                            }
                        )
                        continue
                    if ex and ex.phase == "pending_approval":
                        session_mgr.clear_terminal_plan(session_id)
                    steps = _build_plan_steps_from_result(result)
                    if not steps:
                        bad_card = f"aic_srv_{uuid.uuid4().hex[:16]}"
                        empty_resp = {
                            "type": "llm_resp",
                            "session_id": session_id,
                            "explanation": result.explanation or "无法从描述生成可执行步骤",
                            "command": "",
                            "dangerous": False,
                            "warning": result.warning or "",
                            "confirm_required": False,
                            "should_execute": False,
                            "ai_card_id": bad_card,
                            "auto_executed": False,
                        }
                        await stream_llm_text_chunks(
                            websocket,
                            session_id,
                            explanation=result.explanation or "无法从描述生成可执行步骤",
                            llm_resp=empty_resp,
                        )
                        continue
                    pid = new_plan_id()
                    await stream_plan_preview_text(
                        websocket,
                        session_id,
                        explanation=result.explanation or "",
                        plan_id=pid,
                    )
                    plan = PlanRuntime(
                        plan_id=pid,
                        explanation=result.explanation or "",
                        source="llm",
                        steps=steps,
                        phase="pending_approval",
                        intent=user_text or (result.explanation or ""),
                    )
                    session_mgr.set_terminal_plan(session_id, plan)
                    sess = session_mgr.get_session(session_id)
                    await websocket.send_json(
                        enrich_llm_plan_payload(
                            sess,
                            {
                                "type": "llm_plan",
                                "session_id": session_id,
                                "plan_id": pid,
                                "explanation": result.explanation or "",
                                "steps": steps,
                                "warning": result.warning or "",
                                "source": "llm",
                            },
                        )
                    )
                    continue

                ai_card_id = f"aic_srv_{uuid.uuid4().hex[:16]}"
                auto_will_run = bool(
                    result.should_execute
                    and (result.command or "").strip()
                    and not result.confirm_required
                )
                _risk = (
                    "HIGH"
                    if result.is_dangerous
                    else ("MEDIUM" if result.confirm_required else "LOW")
                )
                llm_resp = {
                    "type": "llm_resp",
                    "session_id": session_id,
                    "explanation": result.explanation,
                    "command": result.command or "",
                    "dangerous": result.is_dangerous,
                    "warning": result.warning,
                    "confirm_required": result.confirm_required,
                    "should_execute": result.should_execute,
                    "risk": _risk,
                    "ai_card_id": ai_card_id,
                    "auto_executed": auto_will_run,
                }
                await stream_llm_text_chunks(
                    websocket,
                    session_id,
                    explanation=result.explanation or "",
                    llm_resp=llm_resp,
                )

                # auto：与历史行为一致，危险命令按会话暂存供 confirm 使用（多终端并行互不覆盖）
                if result.confirm_required and result.command:
                    session_mgr.set_pending_llm_confirm_command(session_id, result.command)
                else:
                    session_mgr.set_pending_llm_confirm_command(session_id, None)

                if auto_will_run:
                    session_mgr.reset_psrp_inject_batch(session_id)
                    cap_mark = len(session_mgr.get_output_capture(session_id))
                    cmd_full = (result.command or "").strip()
                    lines_ok = True
                    for cmd in cmd_full.split("\n"):
                        if cmd.strip():
                            ok = await _guarded_shell_input_line(
                                session_id,
                                cmd,
                                output_buffer,
                                websocket,
                                ws_trace_id,
                                "ws_llm_auto",
                            )
                            if not ok:
                                lines_ok = False
                                break
                    if lines_ok:
                        await _emit_llm_command_result(
                            websocket,
                            session_id,
                            ai_card_id,
                            cmd_full,
                            cap_mark,
                        )

            elif msg_type == "approve_plan":
                pid = msg.get("plan_id") or ""
                style = (msg.get("style") or "gated").lower().strip()
                if style not in ("gated", "batch"):
                    style = "gated"
                plan = session_mgr.get_terminal_plan(session_id)
                if not plan or plan.plan_id != pid:
                    await websocket.send_json(
                        {"type": "error", "session_id": session_id, "data": "计划无效或已过期"}
                    )
                    continue
                if plan.phase != "pending_approval":
                    await websocket.send_json(
                        {"type": "error", "session_id": session_id, "data": "当前计划状态不可批准执行"}
                    )
                    continue
                plan.style = style
                plan.phase = "running"
                plan.current_index = 0
                if not (plan.intent or "").strip():
                    plan.intent = (plan.explanation or "").strip()
                await websocket.send_json(
                    {
                        "type": "plan_status",
                        "session_id": session_id,
                        "plan_id": plan.plan_id,
                        "phase": "approved",
                        "style": plan.style,
                        "total": plan.total_steps(),
                    }
                )
                if _intent_checklist_enabled():
                    await _run_plan_as_intent_checklist(
                        websocket, session_id, output_buffer, ws_trace_id
                    )
                else:
                    await _dispatch_plan_core(websocket, session_id, output_buffer, ws_trace_id)

            elif msg_type == "command_set_execute":
                await _ws_command_set_execute(
                    websocket, session_id, msg, output_buffer, ws_trace_id
                )

            elif msg_type == "step_ok":
                pid = msg.get("plan_id") or ""
                verdict = (msg.get("verdict") or "").lower().strip()
                plan = session_mgr.get_terminal_plan(session_id)
                if not plan or plan.plan_id != pid:
                    continue
                if plan.phase != "awaiting_step_ok":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": session_id,
                            "data": "当前不在等待步骤确认状态",
                        }
                    )
                    continue
                idx_msg = msg.get("step_index")
                if idx_msg is not None and int(idx_msg) != plan.current_index:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": session_id,
                            "data": "步骤序号不匹配，请刷新界面后重试",
                        }
                    )
                    continue
                if verdict in ("abort", "cancel"):
                    await _plan_send_aborted(websocket, session_id, plan, "user_abort")
                elif verdict == "retry":
                    step = plan.steps[plan.current_index]
                    cmd = step["command"]
                    retry_kind = (msg.get("retry_kind") or "").lower().strip()
                    retry_note = (msg.get("retry_user_note") or msg.get("user_note") or "").strip()
                    _ai_regen_default = (
                        "请根据计划整体目标、本步标题与当前命令，结合终端最近输出，"
                        "重新斟酌并给出一条更合适的本步可执行命令（单行）；"
                        "若原命令已合适可给出等价且更稳妥的写法。"
                    )
                    use_ai = False
                    effective_note = ""
                    if retry_kind == "ai":
                        use_ai = True
                        effective_note = retry_note or _ai_regen_default
                    elif retry_kind == "repeat":
                        use_ai = False
                    else:
                        # 旧客户端：仅有 retry_user_note 时走 AI，否则重复执行
                        if retry_note:
                            use_ai = True
                            effective_note = retry_note
                        else:
                            use_ai = False

                    sess_retry = session_mgr.get_session(session_id)
                    logger.info(
                        "plan_retry event=submit session_id=%s host_id=%s plan_id=%s step=%s "
                        "retry_kind=%s use_ai=%s note_len=%s closure_step=%s",
                        session_id,
                        getattr(sess_retry, "host_id", "") or "",
                        plan.plan_id,
                        plan.current_index,
                        retry_kind or "legacy",
                        use_ai,
                        len(retry_note),
                        _plan_step_use_closure_env(),
                    )

                    if use_ai:
                        sess = session_mgr.get_session(session_id)
                        refined = None
                        if prompt_processor and getattr(
                            prompt_processor, "_llm_available", False
                        ) and sess:
                            cap = session_mgr.get_output_capture(session_id)
                            ctx = (
                                cap[-6000:] + "\n---\n" + "".join(output_buffer[-20:])
                            )[-8000:]
                            refined = prompt_processor.refine_plan_step_command(
                                plan_explanation=plan.explanation or "",
                                step_title=(step.get("title") or "")[:400],
                                prior_command=cmd,
                                user_note=effective_note,
                                session_context=ctx,
                                runtime_hint=_runtime_hint_for_session(sess),
                                shell_profile=resolve_shell_profile(sess).value,
                                prior_steps_summary=_plan_prior_steps_summary(
                                    plan, plan.current_index
                                ),
                                ui_locale=session_mgr.get_ui_locale(session_id),
                            )
                            new_line = ""
                            if refined and refined.command:
                                raw_c = refined.command.strip().split("\n")[0].strip()
                                _dp = "⚠️DANGEROUS:"
                                if raw_c.startswith(_dp):
                                    raw_c = raw_c[len(_dp):].strip()
                                ul = raw_c.upper()
                                if ul and ul != "UNSUPPORTED" and not ul.startswith(
                                    "UNSUPPORTED:"
                                ):
                                    new_line = raw_c
                            if new_line:
                                level, w = classify_command_risk(new_line)
                                title = (
                                    new_line
                                    if len(new_line) <= 56
                                    else new_line[:53] + "..."
                                )
                                ix = plan.current_index
                                plan.steps[ix]["command"] = new_line
                                plan.steps[ix]["title"] = title
                                plan.steps[ix]["dangerous"] = level == "HIGH"
                                plan.steps[ix]["confirm_required"] = level in (
                                    "MEDIUM",
                                    "HIGH",
                                )
                                plan.steps[ix]["risk"] = level
                                plan.steps[ix]["warning"] = w or (
                                    refined.warning if refined else ""
                                )
                                for k in (
                                    "verify_command",
                                    "verify_expect_substring",
                                    "rollback_command",
                                ):
                                    plan.steps[ix].pop(k, None)
                                cmd = new_line
                                await websocket.send_json(
                                    {
                                        "type": "plan_retry_notice",
                                        "session_id": session_id,
                                        "plan_id": plan.plan_id,
                                        "step_index": plan.current_index,
                                        "refined": True,
                                        "message": (
                                            (refined.explanation if refined else "")
                                            or "已根据上下文重新生成本步命令。"
                                        )[:800],
                                        "command_preview": new_line[:800],
                                    }
                                )
                            else:
                                await websocket.send_json(
                                    {
                                        "type": "plan_retry_notice",
                                        "session_id": session_id,
                                        "plan_id": plan.plan_id,
                                        "step_index": plan.current_index,
                                        "refined": False,
                                        "message": (
                                            (
                                                (refined.explanation if refined else "")
                                                or "AI 未能生成新命令"
                                            )[:800]
                                            + "；将按原计划命令执行。"
                                        ),
                                        "command_preview": cmd[:800],
                                    }
                                )
                        else:
                            await websocket.send_json(
                                {
                                    "type": "plan_retry_notice",
                                    "session_id": session_id,
                                    "plan_id": plan.plan_id,
                                    "step_index": plan.current_index,
                                    "refined": False,
                                    "message": "LLM 未配置，无法重算；将按原计划命令执行。",
                                    "command_preview": cmd[:800],
                                }
                            )
                    else:
                        await websocket.send_json(
                            {
                                "type": "plan_retry_notice",
                                "session_id": session_id,
                                "plan_id": plan.plan_id,
                                "step_index": plan.current_index,
                                "refined": False,
                                "message": "已选择「重复执行」，直接再次下发本步命令。",
                                "command_preview": cmd[:800],
                            }
                        )
                    step_run = plan.steps[plan.current_index]
                    need_danger = bool(
                        step_run.get("dangerous") or step_run.get("confirm_required")
                    )
                    if not need_danger:
                        level_chk, w_chk = classify_command_risk(cmd)
                        if level_chk in ("MEDIUM", "HIGH"):
                            need_danger = True
                            plan.steps[plan.current_index]["risk"] = level_chk
                            plan.steps[plan.current_index]["dangerous"] = (
                                level_chk == "HIGH"
                            )
                            plan.steps[plan.current_index]["confirm_required"] = True
                            if not (step_run.get("warning") or "").strip():
                                plan.steps[plan.current_index]["warning"] = w_chk
                    if need_danger:
                        plan.phase = "awaiting_danger_confirm"
                        plan.danger_line = cmd
                        await websocket.send_json(
                            {
                                "type": "plan_danger",
                                "session_id": session_id,
                                "plan_id": plan.plan_id,
                                "step_index": plan.current_index,
                                "total": plan.total_steps(),
                                "command": cmd,
                                "risk": step_run.get("risk")
                                or (
                                    "HIGH"
                                    if step_run.get("dangerous")
                                    else "MEDIUM"
                                ),
                                "warning": step_run.get("warning")
                                or "变更操作需确认后才会发往终端",
                            }
                        )
                        continue
                    nl_closure = (
                        (effective_note or _ai_regen_default)[:2000]
                        if use_ai
                        else "plan_retry_repeat"
                    )
                    await _emit_plan_progress_running(
                        websocket, session_id, plan, plan.current_index, cmd
                    )
                    session_mgr.reset_psrp_inject_batch(session_id)
                    cap_mark = len(session_mgr.get_output_capture(session_id))
                    ok = await _execute_plan_step_line(
                        session_id,
                        cmd,
                        output_buffer,
                        websocket,
                        ws_trace_id,
                        plan_id=plan.plan_id,
                        nl_intent_hint=nl_closure,
                    )
                    if not ok:
                        await _plan_send_aborted(websocket, session_id, plan, "policy_denied")
                        continue
                    await websocket.send_json(
                        {
                            "type": "plan_step",
                            "session_id": session_id,
                            "plan_id": plan.plan_id,
                            "step_index": plan.current_index,
                            "total": plan.total_steps(),
                            "command": cmd,
                            "phase": "awaiting_user",
                        }
                    )
                    await _emit_step_command_result(
                        websocket,
                        session_id,
                        plan.plan_id,
                        plan.current_index,
                        cmd,
                        cap_mark,
                    )
                elif verdict in ("continue", "ok", "yes", "next"):
                    await _run_step_verification(
                        websocket,
                        session_id,
                        plan,
                        plan.current_index,
                        ws_trace_id,
                        output_buffer,
                    )
                    plan.current_index += 1
                    plan.phase = "running"
                    await _dispatch_plan_core(websocket, session_id, output_buffer, ws_trace_id)
                else:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": session_id,
                            "data": "未知的 verdict，请使用 continue / retry / abort",
                        }
                    )

            elif msg_type == "terminate_step":
                # 计划步骤：请求终止仍在执行的命令（占位：不实际杀进程）
                pid = (msg.get("plan_id") or "").strip()
                try:
                    step_ix = int(msg.get("step_index", -1))
                except (TypeError, ValueError):
                    step_ix = -1
                logger.info(
                    "terminate_step（占位，未实现 SIGINT/杀进程） session=%s plan=%s step=%s",
                    session_id,
                    pid,
                    step_ix,
                )
                await websocket.send_json(
                    {
                        "type": "terminate_step_ack",
                        "session_id": session_id,
                        "plan_id": pid,
                        "step_index": step_ix,
                        "ignored": True,
                        "detail": "服务端尚未实现向 PTY 发 Ctrl+C/杀进程，事件已记录；请优先在左侧终端人工中断。",
                    }
                )

            elif msg_type == "plan_edit":
                pid = (msg.get("plan_id") or "").strip()
                si = msg.get("step_index")
                new_c = (msg.get("new_command") or msg.get("command") or "").strip()
                plan = session_mgr.get_terminal_plan(session_id)
                if not plan or plan.plan_id != pid or plan.phase != "pending_approval":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": session_id,
                            "data": "仅可在待批准状态下编辑计划",
                        }
                    )
                    continue
                try:
                    idx = int(si) if si is not None else -1
                except (TypeError, ValueError):
                    idx = -1
                if idx < 0 or idx >= len(plan.steps) or not new_c:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": session_id,
                            "data": "无效的 step_index 或命令",
                        }
                    )
                    continue
                level, w = classify_command_risk(new_c)
                title = new_c if len(new_c) <= 56 else new_c[:53] + "..."
                plan.steps[idx]["command"] = new_c
                plan.steps[idx]["title"] = title
                plan.steps[idx]["dangerous"] = level == "HIGH"
                plan.steps[idx]["confirm_required"] = level in ("MEDIUM", "HIGH")
                plan.steps[idx]["risk"] = level
                plan.steps[idx]["warning"] = w or ""
                for k in ("verify_command", "verify_expect_substring", "rollback_command"):
                    plan.steps[idx].pop(k, None)
                payload = {
                    "type": "llm_plan",
                    "session_id": session_id,
                    "plan_id": plan.plan_id,
                    "explanation": plan.explanation,
                    "steps": plan.steps,
                    "warning": "",
                    "source": plan.source,
                }
                if plan.chain_id:
                    payload["chain_id"] = plan.chain_id
                sess = session_mgr.get_session(session_id)
                await websocket.send_json(enrich_llm_plan_payload(sess, payload))

            elif msg_type == "plan_edit_batch":
                pid = (msg.get("plan_id") or "").strip()
                edits = msg.get("edits") or []
                plan = session_mgr.get_terminal_plan(session_id)
                if not plan or plan.plan_id != pid or plan.phase != "pending_approval":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": session_id,
                            "data": "仅可在待批准状态下编辑计划",
                        }
                    )
                    continue
                if not isinstance(edits, list) or not edits:
                    await websocket.send_json(
                        {"type": "error", "session_id": session_id, "data": "edits 不能为空"}
                    )
                    continue
                normalized: List[Tuple[int, str]] = []
                bad = False
                for e in edits:
                    try:
                        idx = int(e.get("step_index", -1))
                    except (TypeError, ValueError):
                        idx = -1
                    new_c = (e.get("new_command") or e.get("command") or "").strip()
                    if idx < 0 or idx >= len(plan.steps) or not new_c:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "session_id": session_id,
                                "data": f"无效编辑项: step_index={e.get('step_index')}",
                            }
                        )
                        bad = True
                        break
                    normalized.append((idx, new_c))
                if bad:
                    continue
                for idx, new_c in normalized:
                    level, w = classify_command_risk(new_c)
                    title = new_c if len(new_c) <= 56 else new_c[:53] + "..."
                    plan.steps[idx]["command"] = new_c
                    plan.steps[idx]["title"] = title
                    plan.steps[idx]["dangerous"] = level == "HIGH"
                    plan.steps[idx]["confirm_required"] = level in ("MEDIUM", "HIGH")
                    plan.steps[idx]["risk"] = level
                    plan.steps[idx]["warning"] = w or ""
                    for k in ("verify_command", "verify_expect_substring", "rollback_command"):
                        plan.steps[idx].pop(k, None)
                payload = {
                    "type": "llm_plan",
                    "session_id": session_id,
                    "plan_id": plan.plan_id,
                    "explanation": plan.explanation,
                    "steps": plan.steps,
                    "warning": "",
                    "source": plan.source,
                }
                if plan.chain_id:
                    payload["chain_id"] = plan.chain_id
                sess = session_mgr.get_session(session_id)
                await websocket.send_json(enrich_llm_plan_payload(sess, payload))

            elif msg_type == "exec_broadcast":
                cmd = msg.get("command") or ""
                nl_intent = str(msg.get("nl_intent") or "").strip()
                exec_mode = str(msg.get("execution_mode") or "").strip().lower()
                raw_map = msg.get("commands_by_session") or msg.get("commands_by_host") or {}
                cmd_map: Dict[str, str] = {}
                if isinstance(raw_map, dict):
                    for k, v in raw_map.items():
                        if str(v or "").strip():
                            cmd_map[str(k)] = str(v).strip()
                host_ids_msg = [
                    str(x).strip()
                    for x in (msg.get("host_ids") or [])
                    if str(x).strip()
                ]
                raw_ids: List[str] = list(msg.get("session_ids") or [])
                if not raw_ids and cmd_map and exec_mode != "oneshot" and not host_ids_msg:
                    raw_ids = list(cmd_map.keys())
                tag = (msg.get("host_tag") or "").strip()
                if tag and not raw_ids and exec_mode != "oneshot":
                    for s in session_mgr.list_sessions():
                        if s.host_id and s.host_id in _HOST_STORE:
                            h = _HOST_STORE[s.host_id]
                            if tag in (h.tags or []):
                                raw_ids.append(s.id)
                use_oneshot = exec_mode == "oneshot" or (
                    bool(host_ids_msg) and not raw_ids
                )
                if use_oneshot:
                    if not host_ids_msg and cmd_map:
                        host_ids_msg = list(cmd_map.keys())
                    host_ids_msg = [h for h in host_ids_msg if h in _HOST_STORE]
                    job_id = (msg.get("job_id") or "").strip() or f"bcast_{uuid.uuid4().hex[:12]}"
                    report_tone_msg = msg.get("report_tone")
                    display_cmd = str(cmd).strip()
                    if not display_cmd and cmd_map:
                        uniq = list(dict.fromkeys(cmd_map.values()))
                        display_cmd = " | ".join(uniq[:4]) + (" …" if len(uniq) > 4 else "")
                    if not display_cmd and nl_intent:
                        display_cmd = f"[fleet] {nl_intent[:120]}"
                    hosts_meta = [
                        {
                            "session_id": hid,
                            "host_label": (
                                f"{getattr(_HOST_STORE[hid], 'name', '')} "
                                f"({getattr(_HOST_STORE[hid], 'host', '')})"
                            ).strip()
                            or hid,
                            "command": cmd_map.get(hid) or display_cmd,
                        }
                        for hid in host_ids_msg
                    ]
                    await websocket.send_json(
                        {
                            "type": "broadcast_started",
                            "session_id": session_id,
                            "job_id": job_id,
                            "data": {
                                "command": display_cmd,
                                "nl_intent": nl_intent,
                                "session_ids": host_ids_msg,
                                "host_ids": host_ids_msg,
                                "hosts": hosts_meta,
                                "report_tone": report_tone_msg,
                                "fleet": True,
                                "execution_mode": "oneshot",
                            },
                        }
                    )
                    has_work = bool(host_ids_msg) and bool(cmd_map)
                    if not has_work:
                        await websocket.send_json(
                            {
                                "type": "broadcast_report",
                                "session_id": session_id,
                                "job_id": job_id,
                                "data": {
                                    "report_md": "**总览：** 无可用主机或命令为空。",
                                    "stats": {"total": 0, "ok": 0, "fail": 0, "unknown": 0},
                                    "command": display_cmd,
                                },
                            }
                        )
                    else:
                        trace_b = str(uuid.uuid4())
                        asyncio.create_task(
                            _run_broadcast_job_oneshot(
                                job_id=job_id,
                                initiator_session_id=session_id,
                                host_ids=host_ids_msg,
                                commands_by_host=cmd_map,
                                websocket=websocket,
                                trace_id=trace_b,
                                report_tone=report_tone_msg,
                                nl_intent=nl_intent,
                            )
                        )
                else:
                    raw_ids = [x for x in raw_ids if session_mgr.get_session(x)]
                    job_id = (msg.get("job_id") or "").strip() or f"bcast_{uuid.uuid4().hex[:12]}"
                    report_tone_msg = msg.get("report_tone")
                    display_cmd = str(cmd).strip()
                    if not display_cmd and cmd_map:
                        uniq = list(dict.fromkeys(cmd_map.values()))
                        display_cmd = " | ".join(uniq[:4]) + (" …" if len(uniq) > 4 else "")
                    if not display_cmd and nl_intent:
                        display_cmd = f"[fleet] {nl_intent[:120]}"
                    hosts_meta = [
                        {
                            "session_id": tid,
                            "host_label": _broadcast_host_label(tid),
                            "command": cmd_map.get(tid) or display_cmd,
                        }
                        for tid in raw_ids
                    ]
                    await websocket.send_json(
                        {
                            "type": "broadcast_started",
                            "session_id": session_id,
                            "job_id": job_id,
                            "data": {
                                "command": display_cmd,
                                "nl_intent": nl_intent,
                                "session_ids": raw_ids,
                                "hosts": hosts_meta,
                                "report_tone": report_tone_msg,
                                "fleet": bool(cmd_map or nl_intent),
                            },
                        }
                    )
                    has_work = bool(raw_ids) and (
                        bool(str(cmd).strip()) or bool(cmd_map)
                    )
                    if not has_work:
                        await websocket.send_json(
                            {
                                "type": "broadcast_report",
                                "session_id": session_id,
                                "job_id": job_id,
                                "data": {
                                    "report_md": "**总览：** 无可用会话或命令为空。",
                                    "stats": {"total": 0, "ok": 0, "fail": 0, "unknown": 0},
                                    "command": display_cmd,
                                },
                            }
                        )
                    else:
                        trace_b = str(uuid.uuid4())
                        asyncio.create_task(
                            _run_broadcast_job(
                                job_id=job_id,
                                initiator_session_id=session_id,
                                session_ids=raw_ids,
                                command=str(cmd).strip(),
                                websocket=websocket,
                                trace_id=trace_b,
                                report_tone=report_tone_msg,
                                commands_by_session=cmd_map or None,
                                nl_intent=nl_intent,
                            )
                        )

            elif msg_type == "generate_broadcast_report":
                job_id = (msg.get("job_id") or "").strip()
                tone = msg.get("report_tone")
                if not job_id:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": session_id,
                            "data": {"message": "job_id required"},
                        }
                    )
                else:

                    async def _gen_report_task(
                        _jid: str = job_id,
                        _tone=tone,
                        _sid: str = session_id,
                    ):
                        out = await _generate_broadcast_report_for_job(
                            job_id=_jid,
                            report_tone=_tone,
                            push_ws=True,
                        )
                        if out and not out.get("ok"):
                            try:
                                await session_mgr._broadcast(
                                    _sid,
                                    {
                                        "type": "broadcast_report_error",
                                        "session_id": _sid,
                                        "job_id": _jid,
                                        "data": out,
                                    },
                                )
                            except Exception:
                                pass

                    asyncio.create_task(_gen_report_task())

            elif msg_type == "cancel_plan":
                pid = msg.get("plan_id")
                plan = session_mgr.get_terminal_plan(session_id)
                if not plan:
                    continue
                if pid and plan.plan_id != pid:
                    continue
                if plan.phase == "pending_approval":
                    await websocket.send_json(
                        {
                            "type": "plan_cancelled",
                            "session_id": session_id,
                            "plan_id": plan.plan_id,
                        }
                    )
                    session_mgr.clear_terminal_plan(session_id)
                elif plan.phase in ("running", "awaiting_step_ok", "awaiting_danger_confirm"):
                    await _plan_send_aborted(websocket, session_id, plan, "user_cancel")

            elif msg_type == "confirm":
                answer = (msg.get("data") or "").lower()
                if answer not in ("yes", "y", "确认", "是", "ok"):
                    continue
                plan_id_c = msg.get("plan_id")
                if plan_id_c:
                    plan = session_mgr.get_terminal_plan(session_id)
                    if (
                        plan
                        and plan.plan_id == plan_id_c
                        and plan.phase == "awaiting_danger_confirm"
                    ):
                        await _on_plan_danger_confirmed(
                            websocket, session_id, output_buffer, ws_trace_id
                        )
                    continue
                last_cmd = session_mgr.get_pending_llm_confirm_command(session_id)
                if last_cmd:
                    for cmd in last_cmd.split("\n"):
                        if cmd.strip():
                            ok = await _guarded_shell_input_line(
                                session_id,
                                cmd,
                                output_buffer,
                                websocket,
                                ws_trace_id,
                                "ws_confirm",
                            )
                            if not ok:
                                break

            elif msg_type == "exec":
                cmd = msg.get("command", "")
                llm_capture = bool(msg.get("llm_capture"))
                ai_card_id = (msg.get("ai_card_id") or "").strip()
                if cmd:
                    cap_mark = None
                    if llm_capture and ai_card_id:
                        session_mgr.reset_psrp_inject_batch(session_id)
                        cap_mark = len(session_mgr.get_output_capture(session_id))
                    lines_ok = True
                    for line in cmd.split("\n"):
                        if line.strip():
                            ok = await _guarded_shell_input_line(
                                session_id,
                                line,
                                output_buffer,
                                websocket,
                                ws_trace_id,
                                "ws_exec",
                            )
                            if not ok:
                                lines_ok = False
                                break
                    if (
                        lines_ok
                        and llm_capture
                        and ai_card_id
                        and cap_mark is not None
                    ):
                        await _emit_llm_command_result(
                            websocket,
                            session_id,
                            ai_card_id,
                            cmd.strip(),
                            cap_mark,
                        )

    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket 异常 {session_id}: {e}")
    finally:
        shutdown.set()
        if ping_task:
            ping_task.cancel()
        if health_task:
            health_task.cancel()
        if ping_task or health_task:
            try:
                await asyncio.gather(
                    *[t for t in (ping_task, health_task) if t],
                    return_exceptions=True,
                )
            except Exception:
                pass
        session_mgr.unregister_ws(session_id, websocket)
        # WinRM 交互 Shell 不随 WS 断开自动关会堆积远端 wsmprovhost；延迟释放便于短时重连
        session_mgr.schedule_winrm_shell_release(session_id)


# ═══════════════════════════════════════════════════════════════════════════
#  健康检查
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/integrations/rdp")
async def rdp_integration_stub():
    """Guacamole / noVNC 等远程桌面为独立组件；此处返回占位说明。"""
    return {
        "guacamole": "not_configured",
        "hint": "将 Guacamole 或 noVNC 作为反向代理旁挂；本终端仅负责 Shell/WinRM/SSH。",
    }


@app.get("/api/llm/config")
async def get_llm_config_api():
    """返回当前 LLM 配置（API Key 已脱敏）。"""
    return settings_for_api_response()


@app.put("/api/llm/config")
async def put_llm_config_api(body: LLMConfigUpdate):
    """保存 data/llm_config.json 并热重载 LLM。"""
    from fastapi import HTTPException

    mode = (body.mode or "").strip().lower()
    if mode not in ("custom", "builtin"):
        raise HTTPException(status_code=400, detail="mode 须为 custom 或 builtin")

    path = default_llm_config_path()
    current = load_json_config(path)
    merged = {
        "mode": mode,
        "display_name": body.display_name,
        "base_url": body.base_url.strip(),
        "model": body.llm_model,
        "builtin_provider": body.builtin_provider,
    }
    if body.no_think is not None:
        merged["no_think"] = body.no_think
    else:
        merged["no_think"] = current.get("no_think", True)
    if body.http_timeout_sec is not None:
        try:
            merged["http_timeout_sec"] = max(
                15.0, min(600.0, float(body.http_timeout_sec))
            )
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="http_timeout_sec 须为 15～600 之间的数字"
            ) from None
    else:
        merged["http_timeout_sec"] = current.get("http_timeout_sec")
    if body.temperature is not None:
        merged["temperature"] = max(0.0, min(2.0, float(body.temperature)))
    else:
        merged["temperature"] = current.get("temperature", 0.1)
    if body.max_tokens is not None:
        merged["max_tokens"] = max(256, min(128000, int(body.max_tokens)))
    else:
        merged["max_tokens"] = current.get("max_tokens", 2048)
    if body.api_key is not None:
        merged["api_key"] = body.api_key
    else:
        merged["api_key"] = current.get("api_key", "")

    bp = merged.get("builtin_provider")
    if bp is not None and str(bp).strip():
        bpl = str(bp).strip().lower()
        merged["builtin_provider"] = (
            bpl if bpl in ("deepseek", "openai", "minimax") else None
        )
    else:
        merged["builtin_provider"] = None

    save_json_config({**current, **merged}, path)
    # GET 在存在 llm_models.json 时优先读该文件；扁平 PUT 须同步写回，否则重开弹窗仍是旧值
    try:
        from chibycore.llm_models_store import apply_flat_llm_put_to_models_document

        apply_flat_llm_put_to_models_document(
            mode=str(merged.get("mode") or mode),
            display_name=str(merged.get("display_name") or ""),
            base_url=str(merged.get("base_url") or ""),
            model=str(merged.get("model") or ""),
            api_key=body.api_key,
            no_think=bool(merged.get("no_think", True)),
            temperature=float(merged.get("temperature") or 0.1),
            max_tokens=int(merged.get("max_tokens") or 2048),
            http_timeout_sec=merged.get("http_timeout_sec"),
        )
    except Exception as exc:
        logger.warning("同步 llm_models.json 失败: %s", exc)
    if prompt_processor:
        prompt_processor.refresh_llm()
    out = settings_for_api_response()
    try:
        from chibycore.hermes_llm_sync import sync_assistant_llm_to_hermes
        from chibycore.llm_config import get_effective_llm_settings

        # 用刚写入的合并结果 + 环境覆盖，与运行时生效值一致
        out["hermes_sync"] = sync_assistant_llm_to_hermes(get_effective_llm_settings())
    except Exception as exc:
        logger.warning("hermes_sync 调用失败: %s", exc)
        out["hermes_sync"] = {
            "ok": False,
            "skipped": False,
            "reason": "sync_error",
            "error": str(exc)[:200],
        }
    return out


@app.get("/api/health")
async def health():
    eff = get_effective_llm_settings()
    out = {
        "status": "ok",
        "sessions": len(session_mgr.list_sessions()),
        "llm_available": prompt_processor._llm_available if prompt_processor else False,
        "llm_provider": prompt_processor._llm.active_name if prompt_processor and prompt_processor._llm else "none",
        "llm_display_name": eff.get("display_name") or "",
        "llm_model": (eff.get("model") or "").strip(),
    }
    try:
        from chibycore.metrics import get_gateway_metrics
        from chibycore.policy_engine import policy_enabled

        out["policy_enabled"] = policy_enabled()
        out["gateway_metrics"] = get_gateway_metrics().snapshot()
    except Exception:
        pass
    return out


# ── 启动入口 ───────────────────────────────────────────────────────────────

@app.get("/t/{session_id}", response_class=HTMLResponse)
async def terminal_only(session_id: str):
    """独立的单会话终端页面（无侧边栏管理界面）。"""
    return FileResponse(BASE_DIR / "web" / "standalone_terminal.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("OPS_SHELL_PORT", "8022"))
    uvicorn.run(
        "terminal.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
