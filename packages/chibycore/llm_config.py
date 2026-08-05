"""LLM 配置：合并 data/llm_config.json 与环境变量。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()


def default_llm_config_path() -> Path:
    return _project_root() / "data" / "llm_config.json"


def _load_dotenv_if_present() -> None:
    """加载项目根 `.env`（不覆盖已有环境变量），与 llm_providers 行为一致。"""
    try:
        env_path = _project_root() / ".env"
        if not env_path.is_file():
            return
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError as e:
        logger.debug("跳过 .env 读取: %s", e)


def load_json_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取 JSON 配置文件；不存在或无效则返回空 dict。"""
    p = path or default_llm_config_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取 LLM 配置失败 %s: %s", p, e)
        return {}


def save_json_config(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    """写入 JSON 配置文件（自动创建目录）。"""
    p = path or default_llm_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_bool_loose(v: Any, default: bool = True) -> bool:
    """解析 JSON / 环境变量中的布尔值。"""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("0", "false", "no", "off", ""):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return default


def mask_api_key_for_response(api_key: Optional[str]) -> str:
    """API Key 脱敏：空则空串；否则保留末尾至多 4 位。"""
    if not api_key or not str(api_key).strip():
        return ""
    s = str(api_key).strip()
    if len(s) <= 4:
        return "****"
    return "****" + s[-4:]


def _env_strip(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def get_effective_llm_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    """
    合并配置与环境变量。
    若存在 ``data/llm_models.json`` 且含 ``models`` 列表：以当前选中模型为准（OpenAI 兼容端点）。
    否则回退 ``data/llm_config.json`` 单文件格式。
    环境变量优先级更高（已设置且非空时覆盖上述文件）。
    """
    _load_dotenv_if_present()
    from chibycore.llm_models_store import load_models_document, merge_active_into_effective

    p = path or default_llm_config_path()
    file_data = load_json_config(p)

    defaults: Dict[str, Any] = {
        "mode": "builtin",
        "base_url": "",
        "api_key": "",
        "model": "",
        "display_name": "",
        "builtin_provider": None,
        # Ollama/Qwen 等：请求 think:false，并剥离回复中的 think 块（默认关闭思考模式）
        "no_think": True,
        # 当前选中模型是否允许开启思考（仅多模型配置）；扁平字段供 API 使用
        "allow_thinking": False,
        # LLM HTTP 读超时（秒）；None 表示由各 Provider 内置默认 + 环境 LLM_HTTP_TIMEOUT
        "http_timeout_sec": None,
        # 自然语言对话采样参数（可由 /api/llm/config 或 WS params 覆盖单次请求）
        "temperature": 0.1,
        "max_tokens": 2048,
    }

    doc = load_models_document()
    if doc.get("models"):
        merged: Dict[str, Any] = merge_active_into_effective(doc, defaults, {})
    else:
        merged = {**defaults, **file_data}

    # 规范化 mode
    m = (merged.get("mode") or "builtin").strip().lower()
    merged["mode"] = m if m in ("custom", "builtin") else "builtin"

    # 环境覆盖
    em = _env_strip("LLM_MODE")
    if em:
        eml = em.lower()
        if eml in ("custom", "builtin"):
            merged["mode"] = eml

    for env_k, key in (
        ("LLM_BASE_URL", "base_url"),
        ("LLM_API_KEY", "api_key"),
        ("LLM_MODEL", "model"),
    ):
        v = _env_strip(env_k)
        if v:
            merged[key] = v

    disp = _env_strip("LLM_DISPLAY_NAME", "LLM_NAME")
    if disp:
        merged["display_name"] = disp

    bp = _env_strip("LLM_BUILTIN_PROVIDER")
    if bp:
        merged["builtin_provider"] = bp.lower() if bp.lower() in ("deepseek", "openai", "minimax") else bp

    # 类型整理
    merged["base_url"] = str(merged.get("base_url") or "").strip()
    merged["api_key"] = str(merged.get("api_key") or "").strip()
    merged["model"] = str(merged.get("model") or "").strip()
    merged["display_name"] = str(merged.get("display_name") or "").strip()
    bp_val = merged.get("builtin_provider")
    if bp_val is not None and str(bp_val).strip():
        merged["builtin_provider"] = str(bp_val).strip().lower()
    else:
        merged["builtin_provider"] = None

    merged["no_think"] = _parse_bool_loose(merged.get("no_think"), True)
    env_nt = _env_strip("LLM_NO_THINK", "LLM_OLLAMA_NO_THINK")
    if env_nt:
        merged["no_think"] = _parse_bool_loose(env_nt, True)

    # HTTP 读超时（秒）：环境 LLM_HTTP_TIMEOUT 优先，其次 JSON；限制 15～600
    env_to = _env_strip("LLM_HTTP_TIMEOUT", "LLM_READ_TIMEOUT")
    if env_to:
        try:
            merged["http_timeout_sec"] = max(15, min(600, float(env_to)))
        except ValueError:
            pass
    elif merged.get("http_timeout_sec") is not None and str(merged.get("http_timeout_sec")).strip() != "":
        try:
            merged["http_timeout_sec"] = max(
                15, min(600, float(merged["http_timeout_sec"]))
            )
        except (TypeError, ValueError):
            merged["http_timeout_sec"] = None
    else:
        merged["http_timeout_sec"] = None

    # temperature / max_tokens（文件或默认）
    try:
        t_raw = merged.get("temperature")
        if t_raw is not None and str(t_raw).strip() != "":
            merged["temperature"] = max(0.0, min(2.0, float(t_raw)))
        else:
            merged["temperature"] = 0.1
    except (TypeError, ValueError):
        merged["temperature"] = 0.1
    try:
        mt_raw = merged.get("max_tokens")
        if mt_raw is not None and str(mt_raw).strip() != "":
            merged["max_tokens"] = max(256, min(128000, int(float(mt_raw))))
        else:
            merged["max_tokens"] = 2048
    except (TypeError, ValueError):
        merged["max_tokens"] = 2048

    for _k in list(merged.keys()):
        if _k.startswith("_"):
            merged.pop(_k, None)
    return merged


def settings_for_api_response(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """供 HTTP 返回：api_key 已脱敏；多模型时附带 schema_version=2 与 models 列表。"""
    from chibycore.llm_models_store import load_models_document, models_document_for_api_response

    s = settings if settings is not None else get_effective_llm_settings()
    doc = load_models_document()
    if doc.get("models"):
        wrap, _ = models_document_for_api_response(doc)
        wrap["mode"] = s.get("mode", "custom")
        wrap["display_name"] = s.get("display_name") or ""
        wrap["model"] = s.get("model") or ""
        wrap["base_url"] = s.get("base_url") or ""
        wrap["api_key"] = mask_api_key_for_response(s.get("api_key"))
        wrap["builtin_provider"] = s.get("builtin_provider")
        wrap["no_think"] = bool(s.get("no_think", True))
        wrap["allow_thinking"] = bool(s.get("allow_thinking", False))
        wrap["temperature"] = float(s.get("temperature") or 0.1)
        wrap["max_tokens"] = int(s.get("max_tokens") or 4096)
        wrap["http_timeout_sec"] = s.get("http_timeout_sec")
        return wrap

    return {
        "schema_version": 1,
        "mode": s.get("mode", "builtin"),
        "display_name": s.get("display_name") or "",
        "base_url": s.get("base_url") or "",
        "api_key": mask_api_key_for_response(s.get("api_key")),
        "model": s.get("model") or "",
        "builtin_provider": s.get("builtin_provider"),
        "no_think": bool(s.get("no_think", True)),
        "allow_thinking": bool(s.get("allow_thinking", False)),
        "http_timeout_sec": s.get("http_timeout_sec"),
        "temperature": float(s.get("temperature") or 0.1),
        "max_tokens": int(s.get("max_tokens") or 2048),
        "models": [],
        "selected_model_name": "",
    }
