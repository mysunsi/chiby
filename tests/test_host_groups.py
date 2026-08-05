"""静态主机组 CRUD 与级联剔除。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chibyterm import host_groups as hg
from chibyterm.models.app import Host, HostCreate


@pytest.fixture()
def groups_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_groups.json"

    def _path() -> Path:
        return path

    monkeypatch.setattr(hg, "_groups_path", _path)
    return path


def test_create_update_delete_group(groups_file: Path):
    g = hg.create_group({"name": "生产-Web", "host_ids": ["h1", "h2", "h1"]})
    assert g["name"] == "生产-Web"
    assert g["host_ids"] == ["h1", "h2"]
    assert g["type"] == "static"
    assert groups_file.is_file()

    items = hg.load_groups()
    assert len(items) == 1

    updated = hg.update_group(g["id"], {"name": "生产Web", "host_ids": ["h2"]})
    assert updated is not None
    assert updated["name"] == "生产Web"
    assert updated["host_ids"] == ["h2"]

    assert hg.delete_group(g["id"]) is True
    assert hg.load_groups() == []
    assert hg.delete_group("missing") is False


def test_remove_host_from_all_groups(groups_file: Path):
    a = hg.create_group({"name": "A", "host_ids": ["x", "y"]})
    b = hg.create_group({"name": "B", "host_ids": ["y", "z"]})
    n = hg.remove_host_from_all_groups("y")
    assert n == 2
    assert hg.get_group(a["id"])["host_ids"] == ["x"]
    assert hg.get_group(b["id"])["host_ids"] == ["z"]


def test_resolve_group_hosts_skips_unknown(groups_file: Path):
    g = hg.create_group({"name": "G", "host_ids": ["alive", "gone"]})
    resolved = hg.resolve_group_hosts(g["id"], known_host_ids=["alive"])
    assert resolved["ok"] is True
    assert resolved["host_ids"] == ["alive"]
    assert resolved["skipped"] == 1


def test_host_model_defaults_labels_status():
    """旧 JSON 无 labels/status 时 Pydantic 填默认。"""
    h = Host.model_validate(
        {
            "id": "abc",
            "name": "web",
            "host": "1.2.3.4",
            "username": "root",
        }
    )
    assert h.labels == {}
    assert h.status == "unknown"
    assert h.tags == []


def test_host_create_accepts_labels():
    body = HostCreate(
        name="n",
        host="h",
        username="u",
        tags=["a", "a", "b"],
        labels={"env": "prod", "role": "web"},
        status="online",
    )
    assert body.labels["env"] == "prod"
    assert hg.normalize_tags(body.tags) == ["a", "b"]
    assert hg.normalize_host_status("ONLINE") == "online"
    assert hg.normalize_host_status("weird") == "unknown"


def test_groups_json_roundtrip(groups_file: Path):
    hg.create_group({"name": "t", "host_ids": ["1"]})
    raw = json.loads(groups_file.read_text(encoding="utf-8"))
    assert "groups" in raw
    assert raw["groups"][0]["host_ids"] == ["1"]
