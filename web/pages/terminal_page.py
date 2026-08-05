"""ChibyTerm（赤壁终端）— 终端管理面板（导航到独立终端界面）"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

# terminal 服务地址
TERMINAL_BASE = "http://127.0.0.1:8000"

st.set_page_config(
    page_title="ChibyTerm — 赤壁终端管理",
    page_icon="🖥️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.stApp { background: #0d1117; color: #e6edf3; }
[data-testid="stMainBlockContainer"] { padding-top: 0.5rem; }

/* 侧边栏 */
section[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }

/* 输入框 */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
[data-testid="stSelectbox"] > div > div,
.stNumberInput > div > div > input {
    background: #161b22 !important; color: #e6edf3 !important;
    border: 1px solid #30363d !important; border-radius: 6px;
}

/* 主按钮 */
.stButton > button {
    border-radius: 6px !important; font-weight: 600 !important;
}
.primary-btn > button { background: #238636 !important; color: white !important; border: none !important; }
.primary-btn > button:hover { background: #2ea043 !important; }
.secondary-btn > button { background: #21262d !important; color: #e6edf3 !important; border: 1px solid #30363d !important; }
.danger-btn > button { background: #da3633 !important; color: white !important; border: none !important; }

/* Cards */
[data-testid="stHorizontalBlock"] > div {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 1rem;
}

/* 终端卡片 */
.term-card {
    background: #0d1117; border: 1px solid #30363d;
    border-radius: 8px; padding: 1.2rem; margin-bottom: 0.5rem;
    display: flex; align-items: center; justify-content: space-between;
}
.term-card:hover { border-color: #58a6ff; }
.term-info { flex: 1; }
.term-title { font-size: 1rem; font-weight: 600; color: #e6edf3; }
.term-meta { font-size: 0.8rem; color: #8b949e; margin-top: 4px; }
.term-badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }
.badge-connected  { background: #23863644; color: #3fb950; }
.badge-connecting { background: #d2992244; color: #e3b341; animation: pulse 1s infinite; }
.badge-error      { background: #da363344; color: #f85149; }
.badge-pending    { background: #30363d44; color: #8b949e; }
.badge-disconnected { background: #30363d44; color: #8b949e; }
.term-launch-btn {
    background: #238636 !important; color: white !important;
    border: none !important; border-radius: 6px !important;
    padding: 6px 20px !important; font-weight: 700 !important;
    text-decoration: none !important;
}
.term-launch-btn:hover { background: #2ea043 !important; }

/* 主机列表 */
.host-card {
    background: #0d1117; border: 1px solid #30363d;
    border-radius: 8px; padding: 0.8rem 1rem; margin-bottom: 0.5rem;
    display: flex; align-items: center; justify-content: space-between;
}
.host-card:hover { border-color: #58a6ff; }
.host-name { font-weight: 600; color: #58a6ff; font-size: 0.95rem; }
.host-meta { font-size: 0.8rem; color: #8b949e; }
</style>
""", unsafe_allow_html=True)


# ── 初始化 session state ─────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "_hosts": [],          # 主机列表缓存
        "_sessions": [],       # 会话列表缓存
        "_refresh_key": 0,     # 强制刷新计数器
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── API 辅助函数 ──────────────────────────────────────────────────────────────
def _api_get(path: str) -> Any:
    """调用 terminal API。"""
    try:
        r = requests.get(f"{TERMINAL_BASE}{path}", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def _api_post(path: str, data: dict) -> Any:
    try:
        r = requests.post(f"{TERMINAL_BASE}{path}", json=data, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def _api_delete(path: str) -> Any:
    try:
        r = requests.delete(f"{TERMINAL_BASE}{path}", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def _refresh():
    """刷新主机和会话列表。"""
    raw = _api_get("/api/hosts") or []
    if isinstance(raw, dict) and "items" in raw:
        st.session_state._hosts = raw.get("items") or []
    elif isinstance(raw, list):
        st.session_state._hosts = raw
    else:
        st.session_state._hosts = []
    st.session_state._sessions = _api_get("/api/sessions") or []
    st.session_state._refresh_key += 1

_refresh()


# ── 侧边栏：主机管理 ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🖥️ 主机管理")
    st.divider()

    # 健康检查
    health = _api_get("/api/health")
    if "error" not in health:
        col1, col2 = st.columns(2)
        with col1:
            st.metric("主机", len(st.session_state._hosts))
        with col2:
            st.metric("会话", len(st.session_state._sessions))
        llm_ok = health.get("llm_available", False)
        llm_prov = health.get("llm_provider", "N/A")
        st.caption(f"🤖 LLM: {'✅ ' + llm_prov if llm_ok else '❌ 离线（规则引擎）'}")
    else:
        st.error(f"无法连接终端服务: {health.get('error')}")
        st.caption("请确保 `terminal` 已启动：")
        st.code("python -m uvicorn terminal.main:app --host 127.0.0.1 --port 8000")
        st.stop()

    st.divider()

    # ── 添加主机表单 ───────────────────────────────────────────────────────
    st.markdown("**➕ 添加主机**")
    with st.form("add_host", clear_on_submit=True):
        h_name = st.text_input("名称", placeholder="MyServer")
        h_host = st.text_input("IP/主机", placeholder="192.168.1.100")
        h_port = st.number_input("端口", value=22, min_value=1, max_value=65535, format="%d")
        h_user = st.text_input("用户名", placeholder="root")
        h_pass = st.text_input("密码", type="password")
        submitted = st.form_submit_button("保存主机", use_container_width=True)
        if submitted:
            if not h_name or not h_host or not h_user:
                st.warning("请填写名称、主机和用户名")
            else:
                result = _api_post("/api/hosts", {
                    "name": h_name,
                    "host": h_host,
                    "port": int(h_port),
                    "username": h_user,
                    "password": h_pass or None,
                })
                if "error" not in result:
                    st.success(f"✅ 主机「{h_name}」已保存")
                    _refresh()
                    st.rerun()
                else:
                    st.error(f"保存失败: {result.get('error')}")

    st.divider()

    # ── 主机列表 ───────────────────────────────────────────────────────────
    st.markdown("**📋 主机列表**")
    hosts = st.session_state._hosts
    if not hosts:
        st.caption("暂无主机，请添加")
    for h in hosts:
        col_info, col_del = st.columns([4, 1])
        with col_info:
            st.markdown(
                f"<div class='host-card'>"
                f"<div><div class='host-name'>{h.get('name', '?')}</div>"
                f"<div class='host-meta'>{h.get('username','')}@{h.get('host','')}:{h.get('port',22)}</div></div>"
                f"</div>",
                unsafe_allow_html=True
            )
        with col_del:
            if st.button("🗑️", key=f"del_host_{h['id']}", help="删除主机"):
                _api_delete(f"/api/hosts/{h['id']}")
                _refresh()
                st.rerun()

    st.divider()
    if st.button("🔄 刷新", use_container_width=True):
        _refresh()
        st.rerun()


# ── 主区域 ───────────────────────────────────────────────────────────────────
st.markdown("## 🖥️ ChibyTerm — 赤壁终端管理")

# 状态提示
st.info("👆 在左侧添加主机后，点击「打开终端」即可启动交互式终端。终端在独立页面运行，支持多标签、多主机管理。")

# ── 新建会话区 ────────────────────────────────────────────────────────────────
st.markdown("### 🚀 快速启动")

col_local, col_ssh = st.columns(2)

with col_local:
    st.markdown("#### 📎 本地终端")
    st.markdown("在本地 WSL/Linux 环境中执行命令，无需 SSH 连接。")
    if st.button("🖥️ 打开本地终端", key="launch_local", use_container_width=True, type="primary"):
        # 创建本地会话并导航
        result = _api_post("/api/sessions", {
            "conn_type": "local",
            "title": "本地终端",
        })
        if "error" not in result and "id" in result:
            sid = result["id"]
            # 直接用 meta refresh 跳转到终端页面
            st.markdown(
                f"<meta http-equiv='refresh' content='0; url={TERMINAL_BASE}/?open={sid}'>",
                unsafe_allow_html=True,
            )
            st.success(f"✅ 本地终端已创建! 正在跳转...")
            st.info(f"👉 如果没有自动跳转，"
                    f"[点击这里打开终端]({TERMINAL_BASE}/?open={sid})")
            st.stop()
        else:
            st.error(f"创建失败: {result}")

with col_ssh:
    st.markdown("#### 🔐 SSH 远程终端")
    st.markdown("通过 SSH 连接到远程主机，支持密码认证。")
    hosts = st.session_state._hosts
    if not hosts:
        st.caption("⚠️ 请先在左侧添加主机")
    else:
        host_options = {h["id"]: h["name"] for h in hosts}
        selected_id = st.selectbox(
            "选择主机", options=list(host_options.keys()),
            format_func=lambda k: f"{host_options[k]} ({hosts[[h['id'] for h in hosts].index(k)]['host']})",
            key="ssh_host_select",
        )
        if st.button("🔗 连接 SSH", key="launch_ssh", use_container_width=True, type="primary"):
            selected_host = next((h for h in hosts if h["id"] == selected_id), None)
            if selected_host:
                result = _api_post("/api/sessions", {
                    "conn_type": "ssh",
                    "title": selected_host["name"],
                    "host_id": selected_host["id"],
                    "host": selected_host["host"],
                    "port": selected_host["port"],
                    "username": selected_host["username"],
                })
                if "error" not in result and "id" in result:
                    sid = result["id"]
                    # 用 JS 打开新标签页，避免 Streamlit meta refresh 被拦截
                    st.session_state._open_terminal = f"{TERMINAL_BASE}/?open={sid}"
                    st.markdown(
                        f"<meta http-equiv='refresh' content='0; url={TERMINAL_BASE}/?open={sid}'>",
                        unsafe_allow_html=True,
                    )
                    st.success(f"✅ SSH 会话已创建! 正在跳转...")
                    st.info(f"👉 如果没有自动跳转，"
                            f"[点击这里打开终端]({TERMINAL_BASE}/?open={sid})")
                    st.stop()
                else:
                    st.error(f"创建失败: {result}")


# ── 活跃会话列表 ──────────────────────────────────────────────────────────────
st.divider()
st.markdown("### 📊 活跃会话")

sessions = st.session_state._sessions
if not sessions:
    st.markdown("""
    <div style="text-align:center; padding: 3rem 2rem; color: #8b949e;">
        <div style="font-size:3rem;">🖥️</div>
        <p style="margin-top:1rem;">暂无活跃会话</p>
        <p style="font-size:0.85rem;">使用上方按钮创建新会话</p>
    </div>
    """, unsafe_allow_html=True)
else:
    for s in sessions:
        sid = s.get("id", "?")
        title = s.get("title", "终端")
        conn_type = s.get("conn_type", "local")
        status = s.get("status", "disconnected")
        host = s.get("host", "127.0.0.1")
        port = s.get("port", 22)
        username = s.get("username", "")

        badge_cls = f"badge-{status}"
        icon = "🖥️" if conn_type == "local" else "🔐"
        type_label = "本地" if conn_type == "local" else "SSH"
        host_label = f"{username}@{host}:{port}" if username else host

        col_info, col_status, col_action, col_del = st.columns([3, 1, 2, 1])

        with col_info:
            st.markdown(
                f"<div class='term-card'>"
                f"<div class='term-info'>"
                f"<div class='term-title'>{icon} {title}</div>"
                f"<div class='term-meta'>{type_label} · {host_label}</div>"
                f"</div></div>",
                unsafe_allow_html=True
            )
        with col_status:
            st.markdown(f"<span class='term-badge {badge_cls}'>{status}</span>", unsafe_allow_html=True)
        with col_action:
            open_url = f"{TERMINAL_BASE}/?open={sid}"
            if st.button("▶ 打开终端", key=f"open_{sid}", use_container_width=True):
                st.markdown(
                    f"<meta http-equiv='refresh' content='0; url={open_url}'>",
                    unsafe_allow_html=True,
                )
                st.success("正在跳转...")
                st.stop()
            # 始终显示链接
            st.caption(f"[直接打开]({open_url})")
        with col_del:
            if st.button("🗑️", key=f"del_{sid}", help="删除会话"):
                _api_delete(f"/api/sessions/{sid}")
                _refresh()
                st.rerun()

st.divider()

# ── 底部：打开完整终端界面 ───────────────────────────────────────────────────
st.markdown(
    "💡 **提示**: 也可以直接访问 [完整终端界面](http://127.0.0.1:8000) "
    "管理所有会话、主机和设置。",
    unsafe_allow_html=False
)
