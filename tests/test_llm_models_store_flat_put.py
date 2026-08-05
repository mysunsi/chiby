"""扁平 PUT 须同步写回 llm_models.json（否则 GET 仍读旧选中项）。"""
from __future__ import annotations

import json
from pathlib import Path


def test_apply_flat_llm_put_updates_active_model(tmp_path: Path):
    from chibycore.llm_models_store import (
        apply_flat_llm_put_to_models_document,
        load_models_document,
        pick_active_model,
    )

    p = tmp_path / "llm_models.json"
    p.write_text(
        json.dumps(
            {
                "selected_model_name": "本地Llama-3",
                "temperature": 0.1,
                "models": [
                    {
                        "model_name": "本地Llama-3",
                        "inference_model": "llama3",
                        "base_url": "http://localhost:11434/v1",
                        "api_key": "keep-me",
                        "max_tokens": 8192,
                        "allow_thinking": False,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ok = apply_flat_llm_put_to_models_document(
        mode="custom",
        display_name="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="llama3.2",
        api_key=None,  # 保留原密钥
        no_think=True,
        temperature=0.5,
        max_tokens=4096,
        path=p,
    )
    assert ok is True

    doc = load_models_document(p)
    assert doc["temperature"] == 0.5
    active = pick_active_model(doc)
    assert active is not None
    assert active["model_name"] == "Ollama"
    assert active["inference_model"] == "llama3.2"
    assert active["base_url"] == "http://127.0.0.1:11434/v1"
    assert active["api_key"] == "keep-me"
    assert active["max_tokens"] == 4096


def test_apply_flat_llm_put_skips_when_no_file(tmp_path: Path):
    from chibycore.llm_models_store import apply_flat_llm_put_to_models_document

    p = tmp_path / "missing.json"
    ok = apply_flat_llm_put_to_models_document(
        mode="custom",
        display_name="x",
        base_url="http://127.0.0.1:1/v1",
        model="m",
        path=p,
    )
    assert ok is False
    assert not p.is_file()
