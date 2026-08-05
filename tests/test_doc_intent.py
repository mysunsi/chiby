"""文档意图：勿被多机远端运维抢走。"""

from __future__ import annotations

from terminal.mobile.doc_intent import (
    extract_doc_hub_query,
    looks_like_doc_hub_query,
    parse_doc_hub_intent,
    preferred_knowledge_sources,
)


def test_looks_like_doc_hub_query():
    assert looks_like_doc_hub_query("查询文档堡垒机有哪些角色用户")
    assert looks_like_doc_hub_query("在文档库里找变更窗口")
    assert looks_like_doc_hub_query("搜索企业文档 nginx 超时")
    assert not looks_like_doc_hub_query("堡垒机有哪些角色用户")
    assert not looks_like_doc_hub_query("查一下主机内存")


def test_extract_query_strips_doc_prefix():
    q = extract_doc_hub_query("查询文档堡垒机有哪些角色用户")
    assert "堡垒机" in q
    assert "查询文档" not in q
    assert parse_doc_hub_intent("查询文档堡垒机有哪些角色用户") is not None
    q2, src = parse_doc_hub_intent("查询文档堡垒机有哪些角色用户")
    assert "堡垒机" in q2
    assert src[0] == "doc"


def test_preferred_sources():
    assert preferred_knowledge_sources("查询文档xxx")[0] == "doc"
    assert "kb" in preferred_knowledge_sources("搜索知识库 nginx")
