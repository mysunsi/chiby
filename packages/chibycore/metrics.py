"""轻量内存指标（工业级可观测入口）。"""
from __future__ import annotations

import threading
from typing import Dict


class GatewayMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._c: Dict[str, int] = {
            "gateway_allow": 0,
            "gateway_deny": 0,
            "gateway_skip_policy": 0,
            "gateway_change_window_hold": 0,
        }

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._c[name] = self._c.get(name, 0) + n

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._c)


_metrics: GatewayMetrics | None = None
_m_lock = threading.Lock()


def get_gateway_metrics() -> GatewayMetrics:
    global _metrics
    with _m_lock:
        if _metrics is None:
            _metrics = GatewayMetrics()
        return _metrics


def reset_metrics_for_tests() -> None:
    global _metrics
    with _m_lock:
        _metrics = None
