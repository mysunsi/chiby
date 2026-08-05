"""chibycore.llm_config 合并与脱敏。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_mask_api_key_for_response():
    from chibycore.llm_config import mask_api_key_for_response

    assert mask_api_key_for_response("") == ""
    assert mask_api_key_for_response(None) == ""
    assert mask_api_key_for_response("ab") == "****"
    assert mask_api_key_for_response("sk-secret-key-here").startswith("****")


def test_load_save_roundtrip(tmp_path: Path):
    from chibycore.llm_config import load_json_config, save_json_config

    p = tmp_path / "llm.json"
    payload = {"mode": "custom", "model": "m", "api_key": "k"}
    save_json_config(payload, p)
    got = load_json_config(p)
    assert got["mode"] == "custom"
    assert got["model"] == "m"


def test_get_effective_llm_settings_env_override(monkeypatch, tmp_path: Path):
    from chibycore import llm_config as lc
    from chibycore import llm_models_store as lms

    cfg = tmp_path / "llm_config.json"
    cfg.write_text(json.dumps({"mode": "builtin", "model": "fromfile"}), encoding="utf-8")
    monkeypatch.setattr(lc, "default_llm_config_path", lambda: cfg)

    def _empty_doc():
        return {"models": [], "selected_model_name": "", "temperature": 0.1, "http_timeout_sec": None}

    monkeypatch.setattr(lms, "load_models_document", _empty_doc)

    monkeypatch.setenv("LLM_MODEL", "fromenv")
    try:
        s = lc.get_effective_llm_settings(cfg)
        assert s["model"] == "fromenv"
    finally:
        monkeypatch.delenv("LLM_MODEL", raising=False)
