"""意图广播：分段与冲突检测。"""
from chibycore.intent_broadcast.analysis import (
    ConflictItem,
    IntentSegment,
    analyze_static_conflicts,
    conflicts_to_jsonable,
    resolve_hosts_by_ids,
    resolve_hosts_by_tag,
    resolve_hosts_union,
    segment_hosts,
    segments_to_jsonable,
)

__all__ = [
    "ConflictItem",
    "IntentSegment",
    "analyze_static_conflicts",
    "conflicts_to_jsonable",
    "resolve_hosts_by_ids",
    "resolve_hosts_by_tag",
    "resolve_hosts_union",
    "segment_hosts",
    "segments_to_jsonable",
]
