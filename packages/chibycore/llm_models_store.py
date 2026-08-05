"""多模型 LLM 配置：data/llm_models.json（列表 + 当前选中）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def default_llm_models_path() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data" / "llm_models.json"


def _legacy_llm_config_path() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data" / "llm_config.json"


def _load_legacy_llm_config() -> Dict[str, Any]:
    p = _legacy_llm_config_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# 内置示例：无需额外依赖即可在界面展示；真实推理需本机已启动 Ollama 并拉取对应模型
DEFAULT_MODELS_DOC: Dict[str, Any] = {
    "selected_model_name": "本地Llama-3",
    "temperature": 0.1,
    "http_timeout_sec": None,
    "models": [
        {
            "model_name": "本地Llama-3",
            "inference_model": "llama3",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "max_tokens": 8192,
            "allow_thinking": False,
        },
    ],
}


def normalize_model_entry(raw: Dict[str, Any]) -> Dict[str, Any]:
    """单条模型配置规范化（支持用户表字段 + 可选 inference_model）。"""
    name = str(raw.get("model_name") or "").strip()
    if not name:
        raise ValueError("model_name 不能为空")
    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError(f"模型「{name}」缺少 base_url")
    api_key = str(raw.get("api_key") if raw.get("api_key") is not None else "")
    mt_raw = raw.get("max_tokens")
    try:
        max_tokens = int(float(mt_raw)) if mt_raw is not None and str(mt_raw).strip() != "" else 4096
    except (TypeError, ValueError):
        max_tokens = 4096
    max_tokens = max(256, min(128000, max_tokens))
    allow_thinking = raw.get("allow_thinking")
    if allow_thinking is None:
        allow_thinking = False
    elif isinstance(allow_thinking, bool):
        pass
    else:
        allow_thinking = str(allow_thinking).strip().lower() in ("1", "true", "yes", "on")

    inf = raw.get("inference_model")
    inference_model = str(inf).strip() if inf is not None and str(inf).strip() else ""
    if not inference_model:
        inference_model = name

    return {
        "model_name": name,
        "inference_model": inference_model,
        "base_url": base_url,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "allow_thinking": bool(allow_thinking),
    }


def mask_api_key_tail(api_key: str) -> str:
    if not api_key or not str(api_key).strip():
        return ""
    s = str(api_key).strip()
    if len(s) <= 4:
        return "****"
    return "****" + s[-4:]


def load_models_document(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取 llm_models.json；不存在则尝试从旧 llm_config 迁移或写入内置示例。"""
    p = path or default_llm_models_path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("读取 %s 失败，使用内置默认: %s", p, e)
            return json.loads(json.dumps(DEFAULT_MODELS_DOC))

        if isinstance(raw, list):
            models_list = raw
            doc = {
                "selected_model_name": (
                    str(models_list[0].get("model_name") or "").strip()
                    if models_list
                    else ""
                ),
                "temperature": 0.1,
                "http_timeout_sec": None,
                "models": models_list,
            }
        elif isinstance(raw, dict):
            doc = dict(raw)
            if "models" not in doc:
                doc["models"] = []
        else:
            return json.loads(json.dumps(DEFAULT_MODELS_DOC))

        models_out: List[Dict[str, Any]] = []
        for m in doc.get("models") or []:
            if not isinstance(m, dict):
                continue
            try:
                models_out.append(normalize_model_entry(m))
            except ValueError as e:
                logger.warning("跳过无效模型条目: %s", e)
        doc["models"] = models_out
        if not doc.get("selected_model_name") and models_out:
            doc["selected_model_name"] = models_out[0]["model_name"]
        elif doc.get("selected_model_name"):
            doc["selected_model_name"] = str(doc["selected_model_name"]).strip()
        return doc

    migrated = _try_migrate_legacy_llm_config()
    if migrated:
        try:
            save_models_document(migrated, p)
            logger.info("已从旧 llm_config 迁移写入 %s", p)
        except OSError as e:
            logger.warning("迁移写入失败，使用内存结果: %s", e)
        return migrated

    # 首次启动：落盘内置示例，便于开箱即用
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        save_models_document(json.loads(json.dumps(DEFAULT_MODELS_DOC)), p)
    except OSError as e:
        logger.debug("写入默认 llm_models.json 跳过: %s", e)
    return json.loads(json.dumps(DEFAULT_MODELS_DOC))


def _try_migrate_legacy_llm_config() -> Optional[Dict[str, Any]]:
    legacy = _load_legacy_llm_config()
    if not legacy:
        return None
    mode = str(legacy.get("mode") or "builtin").strip().lower()
    base_url = str(legacy.get("base_url") or "").strip()
    model_id = str(legacy.get("model") or "").strip()
    if mode == "custom" and base_url and model_id:
        disp = str(legacy.get("display_name") or "").strip() or model_id
        entry = normalize_model_entry(
            {
                "model_name": disp,
                "inference_model": model_id,
                "base_url": base_url,
                "api_key": legacy.get("api_key") or "",
                "max_tokens": legacy.get("max_tokens"),
                "allow_thinking": not bool(legacy.get("no_think", True)),
            }
        )
        return {
            "selected_model_name": entry["model_name"],
            "temperature": float(legacy.get("temperature") or 0.1),
            "http_timeout_sec": legacy.get("http_timeout_sec"),
            "models": [entry],
        }
    return None


def save_models_document(doc: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or default_llm_models_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    out = dict(doc)
    models: List[Dict[str, Any]] = []
    for m in out.get("models") or []:
        if isinstance(m, dict):
            models.append(normalize_model_entry(m))
    out["models"] = models
    if out.get("selected_model_name"):
        out["selected_model_name"] = str(out["selected_model_name"]).strip()
    if out["models"] and not out.get("selected_model_name"):
        out["selected_model_name"] = out["models"][0]["model_name"]
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def pick_active_model(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    models = doc.get("models") or []
    if not models:
        return None
    sel = str(doc.get("selected_model_name") or "").strip()
    for m in models:
        if m.get("model_name") == sel:
            return m
    return models[0]


def set_selected_model_name(doc: Dict[str, Any], name: str) -> Dict[str, Any]:
    n = str(name or "").strip()
    models = doc.get("models") or []
    names = {str(m.get("model_name") or "") for m in models}
    if n and n in names:
        doc["selected_model_name"] = n
    elif models:
        doc["selected_model_name"] = models[0].get("model_name")
    return doc


def models_document_for_api_response(doc: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """返回包装对象与脱敏后的 models 列表。"""
    masked: List[Dict[str, Any]] = []
    for m in doc.get("models") or []:
        masked.append(
            {
                "model_name": m.get("model_name"),
                "inference_model": m.get("inference_model"),
                "base_url": m.get("base_url"),
                "api_key": mask_api_key_tail(str(m.get("api_key") or "")),
                "max_tokens": m.get("max_tokens"),
                "allow_thinking": bool(m.get("allow_thinking")),
            }
        )
    wrap = {
        "schema_version": 2,
        "selected_model_name": doc.get("selected_model_name") or "",
        "temperature": doc.get("temperature"),
        "http_timeout_sec": doc.get("http_timeout_sec"),
        "models": masked,
    }
    return wrap, masked


def merge_models_api_keys_preserve_masked(
    incoming: List[Dict[str, Any]],
    previous_doc: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """保存多模型时：若某条 api_key 为空或为脱敏占位（**** …），保留磁盘上的原密钥。"""
    prev_by_name = {
        str(m.get("model_name") or ""): m for m in (previous_doc.get("models") or []) if m.get("model_name")
    }
    out: List[Dict[str, Any]] = []
    for raw in incoming:
        cur = normalize_model_entry(raw)
        name = cur["model_name"]
        prev = prev_by_name.get(name)
        key = str(cur.get("api_key") or "")
        if prev:
            prev_key = str(prev.get("api_key") or "")
            if key.strip() == "":
                cur["api_key"] = prev_key
            elif key.startswith("****") and mask_api_key_tail(prev_key) == key:
                cur["api_key"] = prev_key
        out.append(cur)
    return out


def apply_flat_llm_put_to_models_document(
    *,
    mode: str,
    display_name: str = "",
    base_url: str = "",
    model: str = "",
    api_key: Optional[str] = None,
    no_think: bool = True,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    http_timeout_sec: Any = None,
    path: Optional[Path] = None,
) -> bool:
    """
    将 UI 扁平 PUT（/api/llm/config）同步到 ``llm_models.json`` 当前选中项。

    GET 在存在非空 ``models`` 时只读该文件；PUT 若只写 ``llm_config.json`` 会导致
    「保存成功但重开仍是初始值」。此函数在 models 非空时补齐写回。

    ``api_key`` 为 None 时保留磁盘上原密钥；空字符串表示清空。

    返回是否已写入 models 文件。builtin 模式不改 models（避免误删列表）。
    """
    p = path or default_llm_models_path()
    # 仅在文件已存在且含 models 时同步，避免无调用即落盘内置示例
    if not p.is_file():
        return False
    doc = load_models_document(p)
    if not doc.get("models"):
        return False

    mode_l = (mode or "").strip().lower()
    if mode_l != "custom":
        return False

    try:
        temp = max(0.0, min(2.0, float(temperature)))
    except (TypeError, ValueError):
        temp = 0.1
    try:
        mt = max(256, min(128000, int(max_tokens)))
    except (TypeError, ValueError):
        mt = 4096

    doc["temperature"] = temp
    if http_timeout_sec is not None and str(http_timeout_sec).strip() != "":
        try:
            doc["http_timeout_sec"] = max(15.0, min(600.0, float(http_timeout_sec)))
        except (TypeError, ValueError):
            pass
    else:
        doc["http_timeout_sec"] = None

    active = pick_active_model(doc)
    disp = str(display_name or "").strip()
    inference = str(model or "").strip()
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        url = str((active or {}).get("base_url") or "").strip().rstrip("/")
    if not inference:
        inference = str(
            (active or {}).get("inference_model") or (active or {}).get("model_name") or ""
        ).strip()
    if not url or not inference:
        logger.warning("扁平 LLM PUT 无法同步 models：缺少 base_url 或 model")
        return False

    name = disp or str((active or {}).get("model_name") or "").strip() or inference
    prev_key = str((active or {}).get("api_key") or "")
    if api_key is None:
        key = prev_key
    else:
        key = str(api_key)

    entry = normalize_model_entry(
        {
            "model_name": name,
            "inference_model": inference,
            "base_url": url,
            "api_key": key,
            "max_tokens": mt,
            "allow_thinking": not bool(no_think),
        }
    )

    models = list(doc.get("models") or [])
    if active:
        sel = str(active.get("model_name") or "")
        replaced = False
        for i, m in enumerate(models):
            if str(m.get("model_name") or "") == sel:
                models[i] = entry
                replaced = True
                break
        if not replaced:
            models.append(entry)
    else:
        models.append(entry)
    doc["models"] = models
    doc["selected_model_name"] = entry["model_name"]
    save_models_document(doc, p)
    return True


def merge_active_into_effective(
    doc: Dict[str, Any],
    defaults: Dict[str, Any],
    file_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    将当前选中模型映射为 get_effective_llm_settings 使用的扁平字段：
    mode=custom，model=inference_model，no_think=not allow_thinking。
    """
    active = pick_active_model(doc)
    if not active:
        return {**defaults, **file_data}

    merged = {
        **defaults,
        **file_data,
        "mode": "custom",
        "display_name": active["model_name"],
        "base_url": active["base_url"],
        "api_key": active.get("api_key") or "",
        "model": active.get("inference_model") or active["model_name"],
        "builtin_provider": None,
        "no_think": not bool(active.get("allow_thinking")),
        "max_tokens": int(active.get("max_tokens") or 4096),
    }
    if doc.get("temperature") is not None:
        try:
            merged["temperature"] = max(0.0, min(2.0, float(doc["temperature"])))
        except (TypeError, ValueError):
            pass
    if doc.get("http_timeout_sec") is not None and str(doc.get("http_timeout_sec")).strip() != "":
        try:
            merged["http_timeout_sec"] = max(
                15.0, min(600.0, float(doc["http_timeout_sec"]))
            )
        except (TypeError, ValueError):
            pass
    merged["allow_thinking"] = bool(active.get("allow_thinking"))
    merged["_active_model_name"] = active["model_name"]
    return merged
