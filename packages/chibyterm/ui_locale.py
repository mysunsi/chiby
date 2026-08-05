"""UI / AI 界面语言（简体、繁体、英文）。"""
from __future__ import annotations

from typing import Optional

SUPPORTED_LOCALES = ("zh-CN", "zh-TW", "en")
DEFAULT_LOCALE = "zh-CN"


def normalize_ui_locale(raw: Optional[str]) -> str:
    v = (raw or "").strip().replace("_", "-")
    if not v:
        return DEFAULT_LOCALE
    low = v.lower()
    if low in ("zh-tw", "zh-hant", "zh-hk", "zh-mo", "tw", "hant"):
        return "zh-TW"
    if low in ("en", "en-us", "en-gb", "en-au", "english"):
        return "en"
    if low in ("zh-cn", "zh", "zh-hans", "zh-sg", "cn", "hans"):
        return "zh-CN"
    # 兼容大小写已规范的值
    if v in SUPPORTED_LOCALES:
        return v
    return DEFAULT_LOCALE


def ai_language_instruction(locale: Optional[str]) -> str:
    """追加到 LLM system prompt，强制说明/解释语言与 UI 一致（命令语法不翻译）。"""
    loc = normalize_ui_locale(locale)
    if loc == "en":
        return (
            "\n\nLanguage policy (mandatory):\n"
            "- Write [EXPLAIN], [WARN], conclusions, and all user-facing natural language in **English**.\n"
            "- Do **not** translate shell / PowerShell command syntax; keep executable commands as-is.\n"
            "- UI language preference: English.\n"
        )
    if loc == "zh-TW":
        return (
            "\n\n語言規範（必須遵守）：\n"
            "- [EXPLAIN]、[WARN]、結論以及所有面向使用者的說明必須使用**繁體中文**。\n"
            "- **不要**翻譯或改寫 Shell / PowerShell 命令語法；可執行命令保持原樣。\n"
            "- 介面語言偏好：繁體中文。\n"
        )
    return (
        "\n\n语言规范（必须遵守）：\n"
        "- [EXPLAIN]、[WARN]、结论以及所有面向用户的说明必须使用**简体中文**。\n"
        "- **不要**翻译或改写 Shell / PowerShell 命令语法；可执行命令保持原样。\n"
        "- 界面语言偏好：简体中文。\n"
    )


def locale_label(locale: Optional[str]) -> str:
    loc = normalize_ui_locale(locale)
    return {"zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English"}.get(loc, loc)
