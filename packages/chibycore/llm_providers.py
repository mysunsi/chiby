"""多后端 LLM Provider 抽象层。
支持 OpenAI / DeepSeek / MiniMax，自动降级，空 key 时静默回退。
"""
from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from chibycore.llm_config import get_effective_llm_settings

logger = logging.getLogger(__name__)


def effective_llm_read_timeout(fallback_sec: float) -> float:
    """
    从 llm_config.json / 环境变量解析 HTTP「读」超时（秒）。
    未配置时使用 fallback_sec（各 Provider 不同，一般已放宽）。
    """
    s = get_effective_llm_settings()
    v = s.get("http_timeout_sec")
    if v is not None and str(v).strip() != "":
        try:
            return max(15.0, min(600.0, float(v)))
        except (TypeError, ValueError):
            pass
    try:
        return max(15.0, min(600.0, float(fallback_sec)))
    except (TypeError, ValueError):
        return 120.0


def _httpx_timeout(read_sec: float):
    """连接略短、读超时为主，避免慢模型/高负载时误杀连接。"""
    import httpx

    r = max(15.0, min(600.0, float(read_sec)))
    return httpx.Timeout(connect=30.0, read=r, write=120.0, pool=30.0)


def _load_dotenv_if_present() -> None:
    """加载项目根目录 `.env`（不覆盖进程已有环境变量）。

    便于在未手动 export 的情况下读取 MiniMax / OpenAI 等 KEY。
    """
    try:
        root = __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()
        env_path = root / ".env"
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
        logger.debug("已从 %s 装载环境变量键（未覆盖已存在项）", env_path)
    except OSError as e:
        logger.debug("跳过 .env 读取: %s", e)


_load_dotenv_if_present()


def _strip_env(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def strip_model_thinking_output(text: str) -> str:
    """去除模型「思考过程」块（Ollama/Qwen 等仍可能泄漏到 content 时）。"""
    if not text or not str(text).strip():
        return text
    s = str(text)
    # 常见「思考」包裹标签（think:false 后仍可能零星泄漏到正文）
    patterns = [
        r"\x3cthink\x3e[\s\S]*?\x3c/think\x3e",
        r"\x3credacted_reasoning\x3e[\s\S]*?\x3c/redacted_reasoning\x3e",
        r"<thought>[\s\S]*?</thought>",
        r"<reasoning>[\s\S]*?</reasoning>",
        r"\[think\][\s\S]*?\[/think\]",
    ]
    for p in patterns:
        s = re.sub(p, "", s, flags=re.IGNORECASE)
    return s.strip()


# ─── Provider 基类 ─────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """LLM Provider 接口。"""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """同步对话，返回文本。"""

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """对话并解析 JSON 响应。"""
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        return self._extract_json(text)

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        """从 LLM 返回中提取 JSON。"""
        text = text.strip()
        # 尝试 ```json ... ``` 包裹格式
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
            text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start: end + 1])
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}


# ─── OpenAI Provider ────────────────────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.api_key = (api_key or _strip_env("OPENAI_API_KEY")) or ""
        self.model = model

    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        import httpx

        to = _httpx_timeout(effective_llm_read_timeout(120.0))
        with httpx.Client(timeout=to) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


# ─── OpenAI 兼容 Provider（Ollama / vLLM 等：POST {base}/chat/completions）────────

class OpenAICompatibleProvider(LLMProvider):
    """OpenAI 兼容 HTTP API；base_url 例如 http://127.0.0.1:11434/v1，可无 API Key（Ollama）。"""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "",
        no_think: bool = True,
    ):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.no_think = bool(no_think)

    def is_available(self) -> bool:
        return bool(self.base_url and self.model)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        *,
        no_think: Optional[bool] = None,
    ) -> str:
        import httpx

        url = f"{self.base_url}/chat/completions"
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        nt = self.no_think if no_think is None else bool(no_think)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Ollama Thinking API：关闭推理输出（Qwen3 等）；参见 docs.ollama.com Thinking
        if nt:
            payload["think"] = False

        to = _httpx_timeout(effective_llm_read_timeout(180.0))
        with httpx.Client(timeout=to) as client:
            resp = client.post(
                url,
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if content and nt:
                content = strip_model_thinking_output(content)
            return content


# ─── DeepSeek Provider ──────────────────────────────────────────────────────────

class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self, api_key: Optional[str] = None, model: str = "deepseek-chat"):
        self.api_key = (api_key or _strip_env("DEEPSEEK_API_KEY")) or ""
        self.model = model

    def is_available(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        import httpx

        to = _httpx_timeout(effective_llm_read_timeout(120.0))
        with httpx.Client(timeout=to) as client:
            resp = client.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


# ─── MiniMax Provider ──────────────────────────────────────────────────────────

class MiniMaxProvider(LLMProvider):
    name = "minimax"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.minimax.chat/v1",
        model: str = "MiniMax-M2.7",
    ):
        # 默认与官方 OpenAPI 文本示例一致。MiniMax-Text-01 等多为高阶/多模态，部分 Token 套餐不可用（会报 2061）
        self.api_key = (api_key or _strip_env(
            "MINIMAX_CN_API_KEY",
            "MINIMAX_API_KEY",
            "MINIMAX_KEY",
        )) or ""
        self.base_url = (os.getenv("MINIMAX_BASE_URL") or base_url or "").strip() or "https://api.minimax.chat/v1"
        self.model = (os.getenv("MINIMAX_MODEL") or model or "").strip() or "MiniMax-M2.7"
        self._group_id = (os.getenv("MINIMAX_GROUP_ID") or "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _extract_minimax_reply(data: Dict[str, Any]) -> str:
        """兼容多种 MiniMax / OpenAI 风格返回体。"""
        for key in ("reply", "result"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        choices = data.get("choices") or []
        if not choices:
            return ""
        ch0 = choices[0] if isinstance(choices[0], dict) else {}
        msg = ch0.get("message")
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str) and c.strip():
                return c.strip()
        msgs = ch0.get("messages") or []
        if msgs and isinstance(msgs[0], dict):
            t = msgs[0].get("text") or msgs[0].get("content")
            if isinstance(t, str) and t.strip():
                return t.strip()
        return ""

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self._group_id:
            headers["Group-Id"] = self._group_id

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        to = _httpx_timeout(effective_llm_read_timeout(120.0))
        with httpx.Client(base_url=self.base_url.rstrip("/"), timeout=to) as client:
            resp = client.post(
                "/text/chatcompletion_v2",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            br = data.get("base_resp") or {}
            if isinstance(br, dict) and br.get("status_code") not in (None, 0):
                code = br.get("status_code")
                sm = (br.get("status_msg") or "").strip()
                hint = ""
                low = sm.lower()
                if code == 2061 or "not support model" in low or "token plan" in low:
                    hint = (
                        " 请在 MiniMax 控制台确认套餐可用模型，并设置环境变量 "
                        "MINIMAX_MODEL（如 MiniMax-M2.7、MiniMax-M2、abab6.5-chat）后重启服务。"
                    )
                raise RuntimeError(f"MiniMax API [{code}] {sm}.{hint}")
            text = MiniMaxProvider._extract_minimax_reply(data)
            if not text:
                logger.warning(
                    "MiniMax 返回无可解析正文，响应键: %s",
                    list(data.keys())[:25],
                )
            return text


def _build_builtin_provider_chain(builtin_preference: Optional[str]) -> List[LLMProvider]:
    """DeepSeek → OpenAI → MiniMax；builtin_preference 可将其一提前到链首。"""
    chain: List[LLMProvider] = [
        DeepSeekProvider(),
        OpenAIProvider(),
        MiniMaxProvider(),
    ]
    pref = (builtin_preference or "").strip().lower()
    if pref in ("deepseek", "openai", "minimax"):
        idx = next((i for i, p in enumerate(chain) if p.name == pref), -1)
        if idx > 0:
            chain = [chain[idx]] + chain[:idx] + chain[idx + 1 :]
    return chain


# ─── 全局 Provider 管理器 ──────────────────────────────────────────────────────

class LLMManager:
    """多后端 LLM 管理器，按优先级自动选择可用 provider。"""

    def __init__(self):
        settings = get_effective_llm_settings()
        self._settings = settings
        mode = (settings.get("mode") or "builtin").strip().lower()
        base_url = (settings.get("base_url") or "").strip()
        model = (settings.get("model") or "").strip()
        api_key = (settings.get("api_key") or "").strip()

        if mode == "custom" and base_url and model:
            no_think = bool(settings.get("no_think", True))
            self.providers = [
                OpenAICompatibleProvider(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    no_think=no_think,
                )
            ]
            logger.info(
                "LLM 自定义端点: base_url=%s model=%s（密钥已配置=%s）",
                base_url,
                model,
                bool(api_key),
            )
        else:
            self.providers = _build_builtin_provider_chain(
                settings.get("builtin_provider"),
            )

        self._active: Optional[LLMProvider] = None
        self._resolve_active()

    def _resolve_active(self) -> None:
        mm = _strip_env("MINIMAX_CN_API_KEY", "MINIMAX_API_KEY", "MINIMAX_KEY")
        settings = getattr(self, "_settings", {}) or {}
        if settings.get("mode") == "custom" and settings.get("base_url") and settings.get("model"):
            logger.info(
                "LLM 自定义模式：仅使用 OpenAI 兼容端点（密钥是否配置=%s）",
                bool((settings.get("api_key") or "").strip()),
            )
        else:
            logger.info(
                "LLM 密钥是否已配置（不记录内容）: deepseek=%s openai=%s minimax=%s",
                bool(_strip_env("DEEPSEEK_API_KEY")),
                bool(_strip_env("OPENAI_API_KEY")),
                bool(mm),
            )
        for p in self.providers:
            if p.is_available():
                self._active = p
                logger.info(f"LLM Provider 激活: {p.name}")
                return
        self._active = None
        logger.warning(
            "没有可用的 LLM API Key，所有 LLM 功能将回退到规则引擎。"
            "请在环境变量或项目根目录 .env 中设置之一："
            "MINIMAX_CN_API_KEY / MINIMAX_API_KEY / MINIMAX_KEY（MiniMax），"
            "并重启 ChibyTerm 进程。"
        )

    @property
    def is_available(self) -> bool:
        return self._active is not None

    @property
    def active_name(self) -> str:
        return self._active.name if self._active else "none"

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        *,
        no_think: Optional[bool] = None,
    ) -> Optional[str]:
        if not self._active:
            return None
        if self._active.name == "openai_compatible":
            return self._active.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                no_think=no_think,
            )
        return self._active.chat(messages, temperature=temperature, max_tokens=max_tokens)

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> Optional[Dict[str, Any]]:
        if not self._active:
            return None
        return self._active.chat_json(messages, temperature=temperature, max_tokens=max_tokens)


def _litellm_model_builtin_deepseek(model_cfg: str) -> str:
    """
    builtin DeepSeek 时构造 litellm 路由名。全局 llm_config.model 可能是 Ollama/其它用途，
    若原样加 ``deepseek/`` 前缀会调用官方 API 失败（如 qwen3.5:0.8b）。
    与 DeepSeekProvider 默认 ``deepseek-chat`` 对齐。
    """
    m = (model_cfg or "").strip()
    if not m:
        return "deepseek/deepseek-chat"
    ml = m.lower()
    suspicious = (
        ":" in m
        or ("qwen" in ml and "deepseek" not in ml)
        or ("llama" in ml and "deepseek" not in ml)
        or "ollama" in ml
        or "mistral" in ml
    )
    if suspicious:
        logger.warning(
            "llm_config.model=%r 不适合 DeepSeek 官方 API，remediator 回退 deepseek/deepseek-chat",
            model_cfg,
        )
        return "deepseek/deepseek-chat"
    if ml.startswith("deepseek/"):
        return m
    if "/" not in m:
        return f"deepseek/{m}"
    return m


def remediator_litellm_credentials() -> Optional[Dict[str, Optional[str]]]:
    """
    供 remediator（litellm）使用的 model / api_base / api_key，与当前激活的 LLM 后端一致。

    builtin 模式下密钥常写在 DEEPSEEK_API_KEY / OPENAI_API_KEY 等变量中，而
    ``get_effective_llm_settings()['api_key']`` 仅合并 ``LLM_API_KEY``。
    若不在此处按激活后端解析，remediator_fix_bridge 会误判「无密钥」并返回空列表。
    """
    mgr = get_llm()
    if not mgr.is_available:
        return None
    settings = get_effective_llm_settings()
    mode = (settings.get("mode") or "builtin").strip().lower()
    base_url = (settings.get("base_url") or "").strip()
    model_cfg = (settings.get("model") or "").strip()
    file_key = (settings.get("api_key") or "").strip()

    if mode == "custom" and base_url and model_cfg:
        return {
            "model": model_cfg,
            "api_base": base_url.rstrip("/"),
            "api_key": file_key or None,
        }

    name = mgr.active_name
    if name == "deepseek":
        key = file_key or _strip_env("DEEPSEEK_API_KEY")
        if not key:
            return None
        litellm_model = _litellm_model_builtin_deepseek(model_cfg)
        return {"model": litellm_model, "api_base": None, "api_key": key}
    if name == "openai":
        key = file_key or _strip_env("OPENAI_API_KEY")
        if not key:
            return None
        return {
            "model": model_cfg or "gpt-4o-mini",
            "api_base": None,
            "api_key": key,
        }
    if name == "minimax":
        key = file_key or _strip_env(
            "MINIMAX_CN_API_KEY",
            "MINIMAX_API_KEY",
            "MINIMAX_KEY",
        )
        if not key:
            return None
        m = (os.getenv("MINIMAX_MODEL") or model_cfg or "MiniMax-M2.7").strip()
        litellm_model = m if "/" in m else f"minimax/{m}"
        bu = (os.getenv("MINIMAX_BASE_URL") or "").strip() or "https://api.minimax.chat/v1"
        return {
            "model": litellm_model,
            "api_base": bu.rstrip("/"),
            "api_key": key,
        }
    return None


# ─── 单例 ─────────────────────────────────────────────────────────────────────

_llm_manager: Optional[LLMManager] = None


def reset_llm_singleton() -> None:
    """清空全局 LLMManager，下次 get_llm() 将按最新配置重建。"""
    global _llm_manager
    _llm_manager = None


def get_llm() -> LLMManager:
    global _llm_manager
    if _llm_manager is None:
        _llm_manager = LLMManager()
    return _llm_manager
