"""上下文数据单元（CDU）：UnitStore + HostTargets。"""
from __future__ import annotations

from pathlib import Path

import pytest

from terminal.context_units.host_targets import (
    UNIT_ID_HOST_TARGETS,
    apply_host_targets_to_state,
    hydrate_host_targets_into_state,
    resolve_host_targets,
    save_host_targets,
)
from terminal.context_units.registry import ASSISTANT_CHIBY_MOBILE, list_unit_specs
from terminal.context_units.store import ContextUnitStore, reset_default_unit_store_for_tests
from terminal.mobile.acl import AclUser, MobileAcl
from terminal.mobile.headless_exec import FakeHeadlessExecutor
from terminal.mobile.models import HostSummary
from terminal.mobile.orchestrator import ConversationState, MobileSessionOrchestrator
from terminal.mobile.remote_tools import RemoteToolCall, execute_remote_tool_call
from terminal.tools_plugin_loader import discover_plugins, reset_registry


@pytest.fixture()
def unit_store(tmp_path: Path):
    store = ContextUnitStore(root=tmp_path / "cdu")
    reset_default_unit_store_for_tests(store)
    yield store
    reset_default_unit_store_for_tests(None)


def test_registry_has_host_targets():
    specs = list_unit_specs(ASSISTANT_CHIBY_MOBILE)
    ids = {s.unit_id for s in specs}
    assert UNIT_ID_HOST_TARGETS in ids


def test_unit_store_user_buckets(unit_store: ContextUnitStore):
    unit_store.save(
        assistant_id=ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id="u1",
        data={"host_ids": ["a"]},
    )
    unit_store.save(
        assistant_id=ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id="u2",
        data={"host_ids": ["b"]},
    )
    v1 = unit_store.load(
        assistant_id=ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id="u1",
    )
    v2 = unit_store.load(
        assistant_id=ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id="u2",
    )
    assert v1 is not None and v1.data["host_ids"] == ["a"]
    assert v2 is not None and v2.data["host_ids"] == ["b"]
    assert v1.user_key == "u1"
    assert v2.user_key == "u2"


def test_resolve_host_targets_acl_and_unknown():
    r = resolve_host_targets(
        ["a", "ghost"],
        known_ids=["a", "b"],
        allowed_ids={"*"},
    )
    assert r["ok"] is True
    assert r["host_ids"] == ["a"]
    assert r["bound_host_id"] == "a"
    assert any(x["host_id"] == "ghost" for x in r["skipped"])

    denied = resolve_host_targets(["a"], known_ids=["a"], allowed_ids=None)
    assert denied["ok"] is False


def test_set_ui_targets_writes_cdu(unit_store: ContextUnitStore):
    hosts = [
        HostSummary(id="a", name="Alpha", host="1.1.1.1"),
        HostSummary(id="b", name="Beta", host="2.2.2.2"),
    ]
    orch = MobileSessionOrchestrator(
        host_provider=lambda: hosts,
        acl=MobileAcl(
            users={
                "demo-user-1": AclUser(
                    external_user_id="demo-user-1",
                    internal_user="ops",
                    host_ids={"*"},
                ),
            },
        ),
        executor=FakeHeadlessExecutor(),
        session_store=None,
    )
    r = orch.set_ui_targets(
        conversation_id="c-cdu",
        external_user_id="demo-user-1",
        host_ids=["a", "b"],
    )
    assert r["ok"]
    assert r.get("cdu") == "host_targets"
    loaded = unit_store.load(
        assistant_id=ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id="demo-user-1",
    )
    assert loaded is not None
    assert loaded.data["host_ids"] == ["a", "b"]

    st = ConversationState(conversation_id="c-empty")
    hyd = hydrate_host_targets_into_state(
        st, external_user_id="demo-user-1", store=unit_store
    )
    assert hyd["source"] == "server"
    assert st.ui_host_ids == ["a", "b"]
    assert st.bound_host_id == "a"


@pytest.mark.asyncio
async def test_host_list_does_not_mutate_host_targets(unit_store: ContextUnitStore):
    reset_registry()
    discover_plugins(force=True)
    save_host_targets(
        host_ids=["keep"],
        external_user_id="u",
        store=unit_store,
    )
    ts = unit_store.load(
        assistant_id=ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id="u",
    ).updated_at

    async def _boom(*_a, **_k):
        raise AssertionError("no ssh")

    tr = await execute_remote_tool_call(
        RemoteToolCall(tool="host_list", raw={"tool": "host_list"}),
        executor=_boom,
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: {},
        list_visible_hosts=lambda: [{"id": "keep"}],
    )
    assert tr.ok
    again = unit_store.load(
        assistant_id=ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id="u",
    )
    assert again.data["host_ids"] == ["keep"]
    assert again.updated_at == ts


def test_apply_host_targets_clears_bound():
    st = ConversationState(conversation_id="c1", bound_host_id="x", ui_host_ids=["x"])
    apply_host_targets_to_state(st, host_ids=[])
    assert st.ui_host_ids == []
    assert st.bound_host_id is None
