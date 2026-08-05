"""
企业可插拔命令规则（预处理 / 错误上下文后处理）。

``should_trigger`` 在「尚无 StructuredError」时可传入 ``None``（仅预处理阶段）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional, Sequence

if TYPE_CHECKING:
    from remediator.remediation.models import StructuredError

logger = logging.getLogger(__name__)


class RuleBlockedError(Exception):
    """规则插件主动阻断命令执行（如数据库保护）。"""

    def __init__(self, rule_name: str, reason: str) -> None:
        self.rule_name = rule_name
        self.reason = reason
        super().__init__(f"[{rule_name}] {reason}")


class BaseRule(ABC):
    """单条规则：在满足触发条件时改写命令字符串。"""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def should_trigger(self, error: Optional["StructuredError"]) -> bool:
        """是否对当前命令应用 ``process``；预处理阶段 ``error`` 可为 ``None``。"""

    @abstractmethod
    def process(self, command: str) -> str:
        """返回修改后的命令；亦可抛出 :class:`RuleBlockedError`。"""


def apply_command_rules(
    rules: Sequence[BaseRule],
    command: str,
    error: Optional["StructuredError"],
) -> str:
    """
    按顺序执行规则链：每条 ``should_trigger`` 为真则 ``process``。

    若 ``process`` 抛出 :class:`RuleBlockedError`，向上传递。
    """
    cmd = command
    for rule in rules:
        try:
            if rule.should_trigger(error):
                before = cmd
                cmd = rule.process(cmd)
                if cmd != before:
                    logger.info("规则 %s 已改写命令", rule.name)
        except RuleBlockedError:
            raise
        except Exception as e:  # pragma: no cover
            logger.warning("规则 %s 执行异常（已忽略该规则）: %s", rule.name, e)
    return cmd


__all__ = ["BaseRule", "RuleBlockedError", "apply_command_rules"]
