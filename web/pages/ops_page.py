"""AI Ops Assistant — Streamlit 运维页面。"""
from __future__ import annotations

import time
from typing import Optional

import requests
import streamlit as st

# ── Config ──────────────────────────────────────────────────────────────────────
API_BASE = "http://127.0.0.1:8000"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_USER = "sunsi"
DEFAULT_PASS = "csswzqzy"  # 占位

st.set_page_config(
    page_title="AI Ops Assistant",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* 全局暗色 */
.stApp { background: #0d1117; color: #e6edf3; }
[data-testid="stMainBlockContainer"] { padding-top: 1.5rem; }

/* 输入框 */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stNumberInput > div > div > input,
.stSelectbox > div > div,
[data-testid="stSelectbox"] > div {
    background: #161b22 !important;
    color: #e6edf3 !important;
    border: 1px solid #30363d !important;
    border-radius: 6px;
}

/* 按钮 */
.primary-btn button,
[data-testid="stFormSubmitButton"] > button {
    background: #238636 !important;
    color: white !important;
    border: none !important;
    font-weight: 700 !important;
    border-radius: 6px !important;
    font-size: 1rem !important;
    padding: 0.5rem 1.5rem !important;
}
.primary-btn button:hover { background: #2ea043 !important; }

/* 执行中按钮 */
.running-btn button,
[data-testid="stFormSubmitButton"][data-testid="stFormSubmitButton"] > button {
    background: #1f6feb !important;
}

/* 标题 */
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3 { color: #58a6ff; }

/* 警告/成功/错误区块 */
.stSuccess, .stWarning, .stError, .stInfo {
    border-radius: 8px;
    padding: 0.6rem 1rem;
    margin-bottom: 0.5rem;
}

/* 状态徽章 */
.status-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.8rem;
    font-weight: 600;
}
.badge-pending  { background: #30363d; color: #8b949e; }
.badge-running  { background: #1f6feb44; color: #58a6ff; }
.badge-success  { background: #23863644; color: #3fb950; }
.badge-verified { background: #23863688; color: #56d364; }
.badge-failed   { background: #da363344; color: #f85149; }
.badge-partial  { background: #d2992244; color: #e3b341; }
.badge-planned  { background: #8957e522; color: #bc8cff; }

/* 步骤卡片 */
.step-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 0.7rem 1rem;
    margin-bottom: 0.4rem;
    transition: border-color 0.2s;
}
.step-card:hover { border-color: #58a6ff; }
.step-card-active { border-color: #1f6feb; box-shadow: 0 0 0 1px #1f6feb33; }

/* 输出框 */
.result-box {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 0.5rem 0.8rem;
    font-family: 'Courier New', monospace;
    font-size: 0.8rem;
    white-space: pre-wrap;
    max-height: 350px;
    overflow-y: auto;
    line-height: 1.5;
}

/* 侧边栏 */
section[data-testid="stSidebar"] {
    background: #161b22;
    border-right: 1px solid #30363d;
}

/* Tabs */
button[data-testid="stTab"] {
    color: #8b949e !important;
}
button[data-testid="stTab"][aria-selected="true"] {
    color: #58a6ff !important;
    border-bottom: 2px solid #58a6ff !important;
}

/* 进度条 */
.stProgress > div > div > div {
    background: #238636 !important;
}
.stSpinner > div {
    border-color: #238636 !important;
}

/* 连接状态指示 */
.api-indicator {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    margin-right: 6px;
}
.api-ok  { background: #3fb950; box-shadow: 0 0 4px #3fb95088; }
.api-no  { background: #f85149; }

/* 计划预览表格 */
.plan-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
}
.plan-table th {
    text-align: left;
    color: #8b949e;
    padding: 6px 10px;
    border-bottom: 1px solid #30363d;
}
.plan-table td {
    padding: 6px 10px;
    border-bottom: 1px solid #21262d;
    color: #e6edf3;
}
.plan-table tr:hover td { background: #1f6feb11; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ────────────────────────────────────────────────────────────────────
def status_badge(status: str) -> str:
    cls = f"badge-{status}"
    label = {
        "pending": "⏳ 待执行",
        "running": "🔄 执行中",
        "success": "✅ 成功",
        "verified": "✅ 已验证",
        "failed": "❌ 失败",
        "partial": "⚠️ 部分成功",
        "planned": "📋 已计划",
        "skipped": "⏭️ 已跳过",
        "rolled_back": "↩️ 已回滚",
    }.get(status.lower(), status)
    return f'<span class="status-badge {cls}">{label}</span>'


def api_ok() -> bool:
    try:
        r = requests.get(f"{API_BASE}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def api_get(path: str):
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def api_post(path: str, data: dict):
    try:
        r = requests.post(f"{API_BASE}{path}", json=data, timeout=60)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        return {"_error": str(e)}


def exec_step_animation():
    """执行中动画 SVG"""
    return """
    <div style="text-align:center;padding:10px">
    <svg width="40" height="40" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
        <circle cx="20" cy="20" r="16" fill="none" stroke="#1f6feb" stroke-width="3" stroke-dasharray="80 20" stroke-dashoffset="0">
            <animateTransform attributeName="transform" type="rotate" from="0 20 20" to="360 20 20" dur="0.8s" repeatCount="indefinite"/>
        </circle>
    </svg>
    </div>
    """


# ── 结果展示函数 ───────────────────────────────────────────────────────────────
def _show_result(resp: Optional[dict], command: str):
    """展示执行结果，存入历史。"""
    if not resp or "_error" in resp:
        st.error(f"❌ 调用失败: {resp.get('_error', '未知错误') if resp else '无响应'}")
        return

    # 存入历史
    st.session_state["history"].insert(0, {
        "command": command,
        "result": resp,
        "timestamp": time.strftime("%H:%M:%S"),
    })

    status = resp.get("status", "?")
    tid = resp.get("task_id", "?")
    chain = resp.get("chain_name") or resp.get("chain_id", "?")
    steps = resp.get("steps", [])

    # 状态横幅
    if status in ("success", "verified"):
        st.success(f"✅ 任务 `{tid}` 执行成功 — {chain} ({resp.get('total_duration_ms',0)}ms)")
    elif status == "partial":
        st.warning(f"⚠️ 任务 `{tid}` 部分成功")
    elif status == "planned":
        st.info(f"📋 任务 `{tid}` 已计划")
    else:
        st.error(f"❌ 任务 `{tid}` 执行失败")

    # 指标行
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("任务ID", tid)
    m2.metric("耗时", f"{resp.get('total_duration_ms',0)}ms")
    m3.metric("步骤数", len(steps))
    m4.metric("任务链", chain)

    # 步骤展开
    if steps:
        st.markdown("### 📌 执行步骤")
        for i, s in enumerate(steps):
            s_status = s.get("status", "pending")
            s_action = s.get("action", "")
            s_desc = s.get("description", "")
            s_dur = s.get("duration_ms", 0)
            s_cmd = s.get("command", "")
            s_out = s.get("stdout", "")
            s_err = s.get("stderr", "")

            is_ok = s_status in ("success", "verified")
            card_cls = "step-card" + ("-active" if s_status == "running" else "")
            border_color = "#238636" if is_ok else ("#da3633" if s_status == "failed" else "#30363d")

            with st.container():
                st.markdown(
                    f'<div class="{card_cls}" style="border-left:3px solid {border_color}">'
                    f'<b>步骤 {i+1}</b> {status_badge(s_status)} '
                    f'<span style="color:#58a6ff;font-size:0.85em">{s_action}</span> '
                    f'<span style="color:#8b949e;font-size:0.75em">· {s_dur}ms</span><br/>'
                    f'<span style="color:#e6edf3">{s_desc}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                with st.expander("🔧 查看命令 & 输出"):
                    if s_cmd:
                        st.code(s_cmd, language="bash")
                    if s_out:
                        st.markdown("**输出:**")
                        st.markdown(
                            f'<div class="result-box">{s_out[:800]}</div>',
                            unsafe_allow_html=True,
                        )
                    if s_err:
                        st.markdown("**错误:**")
                        st.markdown(
                            f'<div class="result-box" style="border-color:#da3633">{s_err[:400]}</div>',
                            unsafe_allow_html=True,
                        )
                    if s.get("verified"):
                        st.markdown("✅ **验证通过**")

    # 原始输出
    raw = resp.get("final_output", "")
    if raw:
        with st.expander("📄 原始输出", expanded=False):
            st.markdown(f'<div class="result-box">{raw[:1000]}</div>', unsafe_allow_html=True)

    # 错误信息
    err = resp.get("error_message")
    if err:
        st.error(f"**错误详情:** {err}")

if "history" not in st.session_state:
    st.session_state["history"] = []
if "pending_plan" not in st.session_state:
    st.session_state["pending_plan"] = None
if "last_preview" not in st.session_state:
    st.session_state["last_preview"] = None


# ── Header ─────────────────────────────────────────────────────────────────────
col_title, col_api = st.columns([4, 1])
with col_title:
    st.title("🤖 AI Ops Assistant")
    st.caption("自然语言 → 任务链 → SSH 执行 → 验证回滚")
with col_api:
    st.markdown("　")  # spacer
    ok = api_ok()
    dot = "api-ok" if ok else "api-no"
    label = "✅ API 在线" if ok else "❌ API 离线"
    st.markdown(f'<span class="api-indicator {dot}"></span>{label}', unsafe_allow_html=True)


# ── Sidebar: 连接设置 + 任务链列表 ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 连接设置")
    host = st.text_input("目标主机", value=DEFAULT_HOST)
    ssh_user = st.text_input("SSH 用户", value=DEFAULT_USER)
    ssh_pass = st.text_input("SSH 密码", value=DEFAULT_PASS, type="password")

    st.divider()
    st.markdown("**📋 可用任务链**")

    # 加载链列表
    chains = api_get("/api/v1/ops/chains")
    if chains:
        for ch in chains:
            with st.expander(f"**{ch['name']}** ({ch['step_count']}步)"):
                st.caption(ch.get("description", ""))
                for s in ch.get("steps_summary", []):
                    st.markdown(f"  • {s}")
    else:
        st.warning("无法加载任务链，请检查 API 服务")

    st.divider()
    st.markdown("**⚡ 快捷命令**")
    quick_cmds = [
        ("主机资源监控", "帮我获取主机资源使用情况"),
        ("创建测试用户", "帮我创建一个账号 testuser，密码 Test@123"),
        ("安装 Nginx", "帮我安装 nginx"),
        ("批量安装", "帮我安装 nginx 和 redis"),
        ("SSH 服务状态", "帮我检查 ssh 服务的运行状态"),
        ("故障排查", "帮我排查 ssh 服务的问题"),
        ("用户检查", "检查一下 sunsi 这个用户是否存在"),
    ]
    for label, cmd in quick_cmds:
        if st.button(f"▶ {label}", use_container_width=True, disabled=not ok):
            st.session_state["quick_cmd"] = cmd

    st.divider()
    st.caption("Powered by AI Ops Engine v0.1")


# ── Main area ──────────────────────────────────────────────────────────────────
tab_execute, tab_history, tab_chains = st.tabs(["💬 命令执行", "📋 执行历史", "🔗 任务链管理"])

# ══════════════════════════════════════════════════════════════════════════════
with tab_execute:
    # 待执行的命令（快捷或手动输入）
    default_cmd = st.session_state.pop("quick_cmd", "")
    user_cmd = st.text_area(
        "📝 输入运维指令",
        value=default_cmd,
        placeholder="例如：帮我创建一个账号 testuser，密码: Test@123，并验证是否创建成功",
        height=80,
        key="cmd_input",
    )

    col_preview, col_exec, col_clear = st.columns([1, 1, 1])
    with col_preview:
        do_preview = st.button("🔍 预览计划", use_container_width=True, disabled=not ok)
    with col_exec:
        do_exec = st.button("🚀 执行", use_container_width=True, disabled=not ok or not user_cmd.strip())
    with col_clear:
        if st.button("🗑️ 清空", use_container_width=True):
            st.session_state["pending_plan"] = None
            st.session_state["last_preview"] = None
            st.rerun()

    # ── 预览模式 ──────────────────────────────────────────────────────────────
    if do_preview and user_cmd.strip():
        st.session_state["last_preview"] = {
            "command": user_cmd,
            "host": host,
            "user": ssh_user,
            "pass": ssh_pass,
        }
        st.session_state["pending_plan"] = None
        st.rerun()

    if st.session_state.get("last_preview"):
        preview_data = st.session_state["last_preview"]
        preview_resp = api_post("/api/v1/ops/preview", {
            "command": preview_data["command"],
            "host": preview_data["host"],
            "ssh_user": preview_data["user"],
            "ssh_password": preview_data["pass"],
        })

        if preview_resp and "_error" not in preview_resp:
            plan = preview_resp.get("plan", [])
            params = preview_resp.get("params", {})
            chain_name = preview_resp.get("chain_name", "单步执行")
            chain_id = preview_resp.get("chain_id", "?")

            st.markdown(f"### 📋 执行计划 — **{chain_name}**")
            st.caption(f"任务链 ID: `{chain_id}`")

            # 参数字段
            if params:
                with st.expander("🔧 已识别的参数", expanded=True):
                    cols = st.columns(min(len(params), 3))
                    for i, (k, v) in enumerate(params.items()):
                        with cols[i % 3]:
                            st.markdown(f"**`{k}`** = `{v}`")

            # 执行计划表格
            st.markdown("**执行步骤：**")
            rows = []
            for i, p in enumerate(plan):
                dep = f"依赖 step_{p['depends_on'][0]}" if p.get("depends_on") else "无依赖"
                par = f"[{p['parallel_group']}]" if p.get("parallel_group") else ""
                rows.append({
                    "步骤": f"step_{i}",
                    "动作": p["action"],
                    "描述": p["description"],
                    "依赖": dep,
                    "并行组": par,
                })
            st.table(rows)

            # 确认执行
            st.markdown("---")
            col_confirm, col_discard = st.columns([1, 1])
            with col_confirm:
                if st.button("✅ 确认执行此计划", use_container_width=True):
                    st.session_state["pending_plan"] = preview_data
                    st.session_state["last_preview"] = None
                    st.rerun()
            with col_discard:
                if st.button("❌ 放弃，重新编辑"):
                    st.session_state["last_preview"] = None
                    st.rerun()
        else:
            st.error(f"预览失败: {preview_resp.get('_error', preview_resp)}")

    # ── 确认执行待处理计划 ────────────────────────────────────────────────────
    if st.session_state.get("pending_plan"):
        plan_data = st.session_state["pending_plan"]
        with st.form("confirm_exec_form", clear_on_submit=False):
            st.info("📌 上一步预览的计划已准备好，确认执行：")
            st.markdown(f"**命令:** `{plan_data['command']}`")
            st.markdown(f"**主机:** `{plan_data['host']}`")
            submitted = st.form_submit_button("⚡ 立即执行", use_container_width=True)

        if submitted:
            del st.session_state["pending_plan"]
            st.session_state["last_preview"] = None
            with st.spinner(""):
                st.markdown(exec_step_animation(), unsafe_allow_html=True)
                st.markdown("**🔄 解析 → 生成脚本 → SSH 执行 → 验证...**")
                resp = api_post("/api/v1/ops/execute", {
                    "command": plan_data["command"],
                    "host": plan_data["host"],
                    "ssh_user": plan_data["user"],
                    "ssh_password": plan_data["pass"],
                    "dry_run": False,
                })

            _show_result(resp, plan_data["command"])

    # ── 直接执行（无预览）─────────────────────────────────────────────────────
    elif do_exec and user_cmd.strip() and not st.session_state.get("pending_plan"):
        with st.spinner(""):
            st.markdown(exec_step_animation(), unsafe_allow_html=True)
            st.markdown("**🔄 解析 → 生成脚本 → SSH 执行 → 验证...**")
            resp = api_post("/api/v1/ops/execute", {
                "command": user_cmd,
                "host": host,
                "ssh_user": ssh_user,
                "ssh_password": ssh_pass,
                "dry_run": False,
            })
        _show_result(resp, user_cmd)


# ══════════════════════════════════════════════════════════════════════════════
with tab_history:
    st.markdown("### 📋 执行历史")
    if not st.session_state["history"]:
        st.info("暂无执行记录")
    else:
        # 按任务显示折叠卡片
        for idx, item in enumerate(reversed(st.session_state["history"])):
            cmd = item["command"]
            resp = item["result"]
            ts = item["timestamp"]
            status = resp.get("status", "?")
            tid = resp.get("task_id", "?")
            dur = resp.get("total_duration_ms", 0)
            chain = resp.get("chain_name") or resp.get("chain_id", "?")
            steps = resp.get("steps", [])

            with st.expander(
                f"**[{ts}]** {status_badge(status)} "
                f"`{chain}` — {cmd[:60]}{'...' if len(cmd) > 60 else ''}"
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("任务ID", tid)
                c2.metric("耗时", f"{dur}ms")
                c3.metric("步骤数", len(steps))
                c4.metric("链", chain)

                # 步骤列表（用 HTML <details> 避免嵌套 expander）
                for si, s in enumerate(steps):
                    s_status = s.get("status", "pending")
                    s_action = s.get("action", "")
                    s_cmd = s.get("command", "") or ""
                    s_out = (s.get("stdout") or "")[:500]
                    s_err = (s.get("stderr") or "")[:300]
                    s_verified = s.get("verified", False)

                    # 构造折叠内容
                    details_content = ""
                    if s_cmd:
                        # 双重转义：HTML属性 + Markdown code block
                        escaped_cmd = s_cmd.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")
                        details_content += f"<pre style='margin:4px 0'><code>{escaped_cmd}</code></pre>"
                    if s_out:
                        esc_out = s_out.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                        details_content += f"<p style='margin:4px 0;color:#8b949e;font-size:0.8em'><b>输出:</b></p><pre style='background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:6px;margin:2px 0;font-size:0.75em;max-height:150px;overflow-y:auto;white-space:pre-wrap'>{esc_out}</pre>"
                    if s_err:
                        esc_err = s_err.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                        details_content += f"<p style='margin:4px 0;color:#f85149;font-size:0.8em'><b>错误:</b></p><pre style='background:#da363322;border:1px solid #da3633;border-radius:4px;padding:6px;margin:2px 0;font-size:0.75em'>{esc_err}</pre>"
                    if s_verified:
                        details_content += f"<p style='margin:4px 0;color:#3fb950'>✅ 验证通过</p>"

                    card_html = f"""
                    <details style="background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:0.4rem;padding:0.5rem 0.8rem">
                        <summary style="cursor:pointer;list-style:none;color:#e6edf3">
                            <b>步骤 {si+1}</b> {status_badge(s_status)}
                            <span style="color:#58a6ff;font-size:0.85em">{s_action}</span>
                            {('<span style="color:#3fb950;font-size:0.8em"> ✅</span>' if s_verified else '')}
                        </summary>
                        <div style="margin-top:8px;padding-top:8px;border-top:1px solid #30363d">
                            {details_content}
                        </div>
                    </details>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)

                # 原始输出
                raw = resp.get("final_output", "")
                if raw:
                    with st.expander("📄 原始输出"):
                        st.markdown(f'<div class="result-box">{raw[:1000]}</div>', unsafe_allow_html=True)

                # 删除
                if st.button(f"🗑️ 删除此记录", key=f"del_h_{idx}"):
                    st.session_state["history"].pop(len(st.session_state["history"]) - 1 - idx)
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
with tab_chains:
    st.markdown("### 🔗 任务链管理")
    chains = api_get("/api/v1/ops/chains")
    if not chains:
        st.warning("无法加载任务链，请确认 API 服务已启动")
    else:
        for ch in chains:
            with st.expander(f"**{ch['name']}** — {ch['description']}"):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(f"**ID:** `{ch['chain_id']}`")
                    st.markdown(f"**步骤数:** {ch['step_count']}")
                    st.markdown(f"**需要确认:** {'是 ✅' if ch['requires_approval'] else '否'}")
                    st.markdown(f"**触发关键词（前5）:**")
                    for kw in ch.get("keywords", []):
                        st.code(kw, language="text")
                with c2:
                    st.markdown("**执行步骤：**")
                    for s in ch.get("steps_summary", []):
                        st.markdown(f"  • {s}")
                st.markdown("---")
                # 在此链上测试
                if st.button(f"▶ 在此链上测试", key=f"try_{ch['chain_id']}"):
                    # 取第一个关键词作为测试命令
                    test_kw = ch.get("keywords", [ch["name"]])[0]
                    st.session_state["quick_cmd"] = f"帮我{test_kw}" if not test_kw.startswith("帮我") else test_kw
                    st.rerun()


# ── 结果展示函数 ───────────────────────────────────────────────────────────────
def _show_result(resp: Optional[dict], command: str):
    """展示执行结果，存入历史。"""
    if not resp or "_error" in resp:
        st.error(f"❌ 调用失败: {resp.get('_error', '未知错误') if resp else '无响应'}")
        return

    # 存入历史
    st.session_state["history"].insert(0, {
        "command": command,
        "result": resp,
        "timestamp": time.strftime("%H:%M:%S"),
    })

    status = resp.get("status", "?")
    tid = resp.get("task_id", "?")
    chain = resp.get("chain_name") or resp.get("chain_id", "?")
    steps = resp.get("steps", [])

    # 状态横幅
    if status in ("success", "verified"):
        st.success(f"✅ 任务 `{tid}` 执行成功 — {chain} ({resp.get('total_duration_ms',0)}ms)")
    elif status == "partial":
        st.warning(f"⚠️ 任务 `{tid}` 部分成功")
    elif status == "planned":
        st.info(f"📋 任务 `{tid}` 已计划")
    else:
        st.error(f"❌ 任务 `{tid}` 执行失败")

    # 指标行
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("任务ID", tid)
    m2.metric("耗时", f"{resp.get('total_duration_ms',0)}ms")
    m3.metric("步骤数", len(steps))
    m4.metric("任务链", chain)

    # 步骤展开
    if steps:
        st.markdown("### 📌 执行步骤")
        for i, s in enumerate(steps):
            s_status = s.get("status", "pending")
            s_action = s.get("action", "")
            s_desc = s.get("description", "")
            s_dur = s.get("duration_ms", 0)
            s_cmd = s.get("command", "")
            s_out = s.get("stdout", "")
            s_err = s.get("stderr", "")

            is_ok = s_status in ("success", "verified")
            card_cls = "step-card" + ("-active" if s_status == "running" else "")
            border_color = "#238636" if is_ok else ("#da3633" if s_status == "failed" else "#30363d")

            with st.container():
                st.markdown(
                    f'<div class="{card_cls}" style="border-left:3px solid {border_color}">'
                    f'<b>步骤 {i+1}</b> {status_badge(s_status)} '
                    f'<span style="color:#58a6ff;font-size:0.85em">{s_action}</span> '
                    f'<span style="color:#8b949e;font-size:0.75em">· {s_dur}ms</span><br/>'
                    f'<span style="color:#e6edf3">{s_desc}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                with st.expander("🔧 查看命令 & 输出"):
                    if s_cmd:
                        st.code(s_cmd, language="bash")
                    if s_out:
                        st.markdown("**输出:**")
                        st.markdown(
                            f'<div class="result-box">{s_out[:800]}</div>',
                            unsafe_allow_html=True,
                        )
                    if s_err:
                        st.markdown("**错误:**")
                        st.markdown(
                            f'<div class="result-box" style="border-color:#da3633">{s_err[:400]}</div>',
                            unsafe_allow_html=True,
                        )
                    if s.get("verified"):
                        st.markdown("✅ **验证通过**")

    # 原始输出
    raw = resp.get("final_output", "")
    if raw:
        with st.expander("📄 原始输出", expanded=False):
            st.markdown(f'<div class="result-box">{raw[:1000]}</div>', unsafe_allow_html=True)

    # 错误信息
    err = resp.get("error_message")
    if err:
        st.error(f"**错误详情:** {err}")
