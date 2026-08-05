"""Embedding：优先 litellm（复用 LLM 配置）；无密钥时自动回退本地 hash。"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

_HASH_DIM = 256


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


def _l2_normalize(vec: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def _token_freq(text: str) -> Dict[str, float]:
    """与 KnowledgeHub 类似的轻量分词（中文单字 + 英文词）。"""
    toks = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_/.\-:@]{2,}", text or "")
    d: Dict[str, float] = {}
    for t in toks:
        t_low = t.lower()
        d[t_low] = d.get(t_low, 0.0) + 1.0
    return d


class HashEmbedder:
    """本地确定性向量：无 embedding API 时仍可入库/检索（语义弱于真 embedding）。"""

    def __init__(self, dim: int = _HASH_DIM) -> None:
        self.dim = max(32, int(dim))

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for t in texts:
            vec = [0.0] * self.dim
            freqs = _token_freq(t or "")
            if not freqs:
                raw = (t or "").encode("utf-8")
                if raw:
                    h = hashlib.sha256(raw).digest()
                    for j in range(self.dim):
                        vec[j] = (h[j % len(h)] - 128) / 128.0
                out.append(_l2_normalize(vec))
                continue
            for tok, w in freqs.items():
                th = hashlib.md5(tok.encode("utf-8")).digest()
                # 每个 token 落到多个维度，减轻碰撞
                for k in range(4):
                    idx = (th[k] + th[k + 4] * 17) % self.dim
                    sign = 1.0 if (th[k + 8] % 2 == 0) else -1.0
                    vec[idx] += sign * float(w)
            out.append(_l2_normalize(vec))
        return out


def resolve_embedding_credentials() -> Optional[Dict[str, Any]]:
    """解析 DocHub embedding 用的 model / api_key / api_base。

    优先级：
    1. DOC_HUB_EMBEDDING_MODEL / API_KEY / BASE_URL
    2. OPENAI_API_KEY → text-embedding-3-small
    3. 自定义 LLM（mode=custom + base_url）且显式配置了 DOC_HUB_EMBEDDING_MODEL
    4. 无可用 embedding 凭据 → None（调用方回退 HashEmbedder）
    """
    from chibycore.llm_config import get_effective_llm_settings

    get_effective_llm_settings()  # 确保 .env 已装载

    model = (
        os.getenv("DOC_HUB_EMBEDDING_MODEL")
        or os.getenv("EMBEDDING_MODEL")
        or ""
    ).strip()
    api_key = (
        os.getenv("DOC_HUB_EMBEDDING_API_KEY")
        or os.getenv("EMBEDDING_API_KEY")
        or ""
    ).strip()
    api_base = (
        os.getenv("DOC_HUB_EMBEDDING_BASE_URL")
        or os.getenv("EMBEDDING_BASE_URL")
        or ""
    ).strip().rstrip("/")

    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key and openai_key:
        api_key = openai_key
        if not model:
            model = "text-embedding-3-small"
        return {"model": model, "api_key": api_key, "api_base": api_base or None}

    if api_key and model:
        # Ollama OpenAI 兼容 /v1：litellm 需 openai/ 前缀才走自定义 api_base
        if api_base and "11434" in api_base and "/" not in model.split(":", 1)[0]:
            model = f"openai/{model}"
        return {"model": model, "api_key": api_key, "api_base": api_base or None}

    # 自定义 OpenAI 兼容网关：仅当显式指定了 embedding 模型名才用（避免把 chat 模型当 embedding）
    settings = get_effective_llm_settings()
    mode = (settings.get("mode") or "builtin").strip().lower()
    base_url = (settings.get("base_url") or "").strip().rstrip("/")
    file_key = (settings.get("api_key") or "").strip()
    if mode == "custom" and base_url and model and (api_key or file_key):
        return {
            "model": model,
            "api_key": api_key or file_key or None,
            "api_base": api_base or base_url,
        }

    return None


class LitellmEmbedder:
    """经 litellm 调用 embedding API。"""

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> None:
        creds = resolve_embedding_credentials() or {}
        self.model = (model or creds.get("model") or "text-embedding-3-small").strip()
        self.api_key = (api_key if api_key is not None else creds.get("api_key")) or None
        self.api_base = (api_base if api_base is not None else creds.get("api_base")) or None

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        import litellm

        batch = [t if t else " " for t in texts]
        if not batch:
            return []
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "input": list(batch),
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        resp = litellm.embedding(**kwargs)
        data = getattr(resp, "data", None) or resp.get("data")  # type: ignore[union-attr]
        items = sorted(
            data,
            key=lambda x: x.get("index", 0) if isinstance(x, dict) else getattr(x, "index", 0),
        )
        vectors: List[List[float]] = []
        for item in items:
            emb = item.get("embedding") if isinstance(item, dict) else getattr(item, "embedding", None)
            if not emb:
                raise RuntimeError("embedding 响应缺少 embedding 字段")
            vectors.append([float(x) for x in emb])
        if len(vectors) != len(batch):
            raise RuntimeError(f"embedding 条数不匹配: {len(vectors)} vs {len(batch)}")
        return vectors


_default_embedder: Optional[Embedder] = None
_default_backend_label: str = ""


def _build_embedder(backend: str) -> tuple[Embedder, str]:
    backend = (backend or "auto").strip().lower()
    if backend in ("hash", "local_hash", "test"):
        return HashEmbedder(), "hash"
    if backend == "litellm":
        creds = resolve_embedding_credentials()
        if not creds:
            raise RuntimeError(
                "未配置 embedding 密钥。请设置 OPENAI_API_KEY，或 "
                "DOC_HUB_EMBEDDING_MODEL + DOC_HUB_EMBEDDING_API_KEY"
                "（可选 DOC_HUB_EMBEDDING_BASE_URL）；"
                "也可设 DOC_HUB_EMBEDDING_BACKEND=hash 使用本地向量。"
            )
        emb: Embedder = LitellmEmbedder(
            model=str(creds.get("model") or ""),
            api_key=creds.get("api_key"),  # type: ignore[arg-type]
            api_base=creds.get("api_base"),  # type: ignore[arg-type]
        )
        return emb, f"litellm:{creds.get('model')}"
    # auto
    creds = resolve_embedding_credentials()
    if creds:
        emb = LitellmEmbedder(
            model=str(creds.get("model") or ""),
            api_key=creds.get("api_key"),  # type: ignore[arg-type]
            api_base=creds.get("api_base"),  # type: ignore[arg-type]
        )
        label = f"litellm:{creds.get('model')}"
        logger.info("DocHub embedding: %s", label)
        return emb, label
    logger.warning(
        "DocHub 未检测到 embedding API（OPENAI_API_KEY 或 DOC_HUB_EMBEDDING_*），"
        "已回退本地 hash 向量；可检索但语义弱于真 embedding。"
    )
    return HashEmbedder(), "hash"


def get_embedder(*, force_backend: Optional[str] = None) -> Embedder:
    """backend: auto | litellm | hash；环境变量 DOC_HUB_EMBEDDING_BACKEND（默认 auto）。"""
    global _default_embedder, _default_backend_label
    backend = (force_backend or os.getenv("DOC_HUB_EMBEDDING_BACKEND") or "auto").strip().lower()
    if force_backend is not None:
        emb, _label = _build_embedder(backend)
        return emb

    # 曾回退 hash，但现在已配好 Ollama/OpenAI → 自动升级，避免进程长驻卡在 hash
    if _default_embedder is not None and _default_backend_label == "hash" and backend == "auto":
        if resolve_embedding_credentials():
            logger.info("DocHub embedding：检测到可用凭据，从 hash 切换到真向量")
            reset_embedder()

    if _default_embedder is None:
        emb, label = _build_embedder(backend)
        _default_embedder = emb
        _default_backend_label = label
    return _default_embedder


def embedder_backend_label() -> str:
    get_embedder()
    return _default_backend_label or "unknown"


def reset_embedder() -> None:
    global _default_embedder, _default_backend_label
    _default_embedder = None
    _default_backend_label = ""


def embed_texts(texts: Sequence[str], embedder: Optional[Embedder] = None) -> List[List[float]]:
    e = embedder or get_embedder()
    return e.embed(texts)
