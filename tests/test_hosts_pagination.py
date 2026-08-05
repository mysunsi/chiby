"""GET /api/hosts 分页与检索。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chibyterm.host_query import (
    filter_hosts,
    host_list_payload,
    parse_id_list,
    parse_label_kv,
    prefer_hosts_first,
)
from chibyterm.models.app import Host


def _host(**kw) -> Host:
    data = {
        "id": kw.pop("id", "h1"),
        "name": kw.pop("name", "web-1"),
        "host": kw.pop("host", "10.0.0.1"),
        "username": kw.pop("username", "root"),
        "tags": kw.pop("tags", []),
        "labels": kw.pop("labels", {}),
        "status": kw.pop("status", "unknown"),
    }
    data.update(kw)
    return Host(**data)


def test_parse_label_kv():
    assert parse_label_kv("env=prod") == ("env", "prod")
    assert parse_label_kv(" role = web ") == ("role", "web")
    with pytest.raises(ValueError):
        parse_label_kv("noks")
    with pytest.raises(ValueError):
        parse_label_kv("=v")


def test_filter_q_tag_label_status():
    rows = [
        _host(id="a1", name="web-alpha", host="1.1.1.1", tags=["production"], labels={"env": "prod"}, status="online"),
        _host(id="b2", name="db-beta", host="2.2.2.2", tags=["staging"], labels={"env": "dev"}, status="offline"),
        _host(id="c3", name="web-gamma", host="3.3.3.3", tags=["production", "web"], labels={"env": "prod", "role": "web"}, status="online"),
    ]
    assert [h.id for h in filter_hosts(rows, q="web")] == ["a1", "c3"]
    assert [h.id for h in filter_hosts(rows, q="b2")] == ["b2"]
    assert [h.id for h in filter_hosts(rows, tag="production")] == ["a1", "c3"]
    assert [h.id for h in filter_hosts(rows, label="env=prod")] == ["a1", "c3"]
    assert [h.id for h in filter_hosts(rows, label="role=web")] == ["c3"]
    assert [h.id for h in filter_hosts(rows, status="offline")] == ["b2"]


def test_parse_id_list_and_prefer_hosts_first():
    assert parse_id_list("a,b; a ,c") == ["a", "b", "c"]
    assert parse_id_list("x,y,z", limit=2) == ["x", "y"]
    rows = [_host(id=f"h{i}", name=f"n{i}", host=f"10.0.0.{i}") for i in range(5)]
    pinned = prefer_hosts_first(rows, ["h3", "missing", "h1", "h3"])
    assert [h.id for h in pinned] == ["h3", "h1", "h0", "h2", "h4"]


def test_host_list_payload_full_and_paged():
    rows = [_host(id=f"h{i}", name=f"n{i}", host=f"10.0.0.{i}") for i in range(12)]
    full = host_list_payload(rows)
    assert full["page"] is None
    assert full["size"] is None
    assert full["pages"] is None
    assert full["total"] == 12
    assert len(full["items"]) == 12

    page1 = host_list_payload(rows, page=1, size=5)
    assert page1["page"] == 1
    assert page1["size"] == 5
    assert page1["pages"] == 3
    assert page1["total"] == 12
    assert len(page1["items"]) == 5
    assert page1["items"][0].id == "h0"

    page3 = host_list_payload(rows, page=3, size=5)
    assert len(page3["items"]) == 2
    assert page3["items"][0].id == "h10"

    # 过滤后置顶：h10、h11 应出现在第 1 页
    pinned = host_list_payload(rows, page=1, size=5, prefer_ids=["h11", "h10"])
    assert [h.id for h in pinned["items"]] == ["h11", "h10", "h0", "h1", "h2"]


@pytest.fixture()
def hosts_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPS_UI_AUTH", "0")
    from chibyterm import main as m

    monkeypatch.setattr(m, "_load_hosts", lambda: None)
    store = {}
    for i in range(12):
        h = _host(
            id=f"h{i:02d}",
            name=("web-%d" % i) if i < 5 else ("db-%d" % i),
            host=f"10.0.0.{i}",
            tags=["production"] if i % 2 == 0 else ["staging"],
            labels={"env": "prod" if i < 6 else "dev"},
            status="online" if i < 3 else "unknown",
        )
        store[h.id] = h
    monkeypatch.setattr(m, "_HOST_STORE", store)
    with TestClient(m.app) as c:
        yield c


def test_api_hosts_full_compat(hosts_client: TestClient):
    r = hosts_client.get("/api/hosts")
    assert r.status_code == 200
    body = r.json()
    assert body["page"] is None
    assert body["size"] is None
    assert body["pages"] is None
    assert body["total"] == 12
    assert len(body["items"]) == 12


def test_api_hosts_pagination(hosts_client: TestClient):
    r = hosts_client.get("/api/hosts", params={"page": 1, "size": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1
    assert body["size"] == 5
    assert body["total"] == 12
    assert body["pages"] == 3
    assert len(body["items"]) == 5


def test_api_hosts_q_filter(hosts_client: TestClient):
    r = hosts_client.get("/api/hosts", params={"q": "web"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert all("web" in x["name"] for x in body["items"])


def test_api_hosts_tag_filter(hosts_client: TestClient):
    r = hosts_client.get("/api/hosts", params={"tag": "production"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 6
    assert all("production" in (x.get("tags") or []) for x in body["items"])


def test_api_hosts_label_filter(hosts_client: TestClient):
    r = hosts_client.get("/api/hosts", params={"label": "env=prod"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 6
    assert all((x.get("labels") or {}).get("env") == "prod" for x in body["items"])


def test_api_hosts_status_and_bad_label(hosts_client: TestClient):
    r = hosts_client.get("/api/hosts", params={"status": "online"})
    assert r.status_code == 200
    assert r.json()["total"] == 3

    bad = hosts_client.get("/api/hosts", params={"label": "noequals"})
    assert bad.status_code == 400


def test_api_hosts_prefer_ids(hosts_client: TestClient):
    r = hosts_client.get(
        "/api/hosts",
        params={"page": 1, "size": 5, "prefer_ids": "h11,h10"},
    )
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()["items"]]
    assert ids[:2] == ["h11", "h10"]
    assert ids == ["h11", "h10", "h00", "h01", "h02"]
