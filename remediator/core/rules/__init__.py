"""内置规则插件。"""
from __future__ import annotations

from typing import List

from remediator.core.rule_engine import BaseRule
from remediator.core.rules.database_protection import DatabaseProtectionRule
from remediator.core.rules.k8s_resource_limit import K8sResourceLimitRule


def default_builtin_rules() -> List[BaseRule]:
    """默认链路：先数据库保护，再 K8s 资源补救（可按客户调整顺序或替换列表）。"""
    return [DatabaseProtectionRule(), K8sResourceLimitRule()]


__all__ = [
    "DatabaseProtectionRule",
    "K8sResourceLimitRule",
    "default_builtin_rules",
]
