"""数据库高危命令扫描与阻断。"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from remediator.core.rule_engine import BaseRule, RuleBlockedError

if TYPE_CHECKING:
    from remediator.remediation.models import StructuredError


_DROP_TABLE = re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE)
_RM_MYSQL_DATA = re.compile(r"rm\s+[^\n;]*?/data/mysql", re.IGNORECASE)


class DatabaseProtectionRule(BaseRule):
    """
    扫描命令字符串：发现 ``DROP TABLE`` 或对 ``/data/mysql`` 的破坏性 ``rm`` 则阻断。
    预处理阶段（error is None）也必须执行，故 ``should_trigger`` 恒为 True。
    """

    def should_trigger(self, error: Optional["StructuredError"]) -> bool:
        return True

    def process(self, command: str) -> str:
        if _DROP_TABLE.search(command):
            raise RuleBlockedError(
                self.name,
                "检测到 DROP TABLE，已由企业数据库保护规则阻断。",
            )
        if _RM_MYSQL_DATA.search(command) or (
            "rm" in command and "/data/mysql" in command.replace("\\", "/").lower()
        ):
            raise RuleBlockedError(
                self.name,
                "检测到针对 MySQL 数据目录的危险 rm 操作，已由企业规则阻断。",
            )
        return command
