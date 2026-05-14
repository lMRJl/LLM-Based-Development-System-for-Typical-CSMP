"""Streamlit Main Entry — Chat Interface with RAG Inspection"""

import logging
import streamlit as st
import requests
import json
import time

from frontend.config import API_BASE, DEFAULT_TIMEOUT, SSE_CONNECT_TIMEOUT, SSE_READ_TIMEOUT
from frontend.api_client import get_projects, get_project_dict, api_get, api_post

logger = logging.getLogger("frontend.app")

st.set_page_config(
    page_title="Agent Platform",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

STAGE_LABELS = {
    "research": "🔍 深度研究", "prd": "📋 产品需求文档", "design": "🏗️ 概要设计",
    "database": "🗄️ 数据库设计", "backend": "⚙️ 后端代码", "frontend": "🎨 前端代码",
    "review": "✅ 代码审查", "deploy": "🚀 部署配置",
}


def _load_history(project_id: str):
    """Load saved artifacts as chat history for the given project."""
    try:
        resp = requests.get(
            f"{API_BASE}/artifacts/project/{project_id}",
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            for artifact in resp.json():
                stage = artifact.get("stage", "")
                content = artifact.get("content", "")
                if content:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"### {stage}\n\n{content}",
                        "stage": stage,
                    })
    except Exception:
        pass  # Silently skip if artifacts unavailable


def init_session():
    """Initialize session state defaults in a single call."""
    defaults = {
        "project_id": None, "project_name": "", "messages": [],
        "pipeline_running": False, "current_stage": "", "pipeline_stop": False,
    }
    missing = {k: v for k, v in defaults.items() if k not in st.session_state}
    if missing:
        st.session_state.update(missing)


def create_project(name: str, description: str = "") -> str | None:
    """Create a new project via API. Returns project ID or None."""
    resp = api_post("/projects", {"name": name, "description": description})
    if resp is None:
        return None
    if resp.status_code == 200:
        get_projects(force_refresh=True)  # invalidate project cache
        return resp.json()["id"]
    st.error(f"创建项目失败 ({resp.status_code}): {resp.text[:100]}")
    return None


def _run_resume_pipeline(project_id: str, placeholder):
    """SSE streaming for resumed pipeline — calls /resume endpoint."""
    st.session_state.pipeline_stop = False
    stage_count = 0
    completed_stages = 0

    try:
        resp = requests.post(
            f"{API_BASE}/agents/resume/{project_id}",
            stream=True,
            headers={"Accept": "text/event-stream"},
            timeout=(SSE_CONNECT_TIMEOUT, SSE_READ_TIMEOUT),
        )

        if resp.status_code != 200:
            st.error(f"恢复失败 ({resp.status_code}): {resp.text[:200]}")
            st.session_state.pipeline_running = False
            return

        current_stage_content = ""
        current_stage = ""
        progress_bar = st.progress(0.0, text="Resuming pipeline...")

        for line in resp.iter_lines():
            if st.session_state.get("pipeline_stop", False):
                resp.close()
                st.warning("Pipeline stopped by user.")
                st.session_state.pipeline_running = False
                return

            if line.startswith(b"data: "):
                data = json.loads(line[6:])
                event_type = data.get("type", "")

                if event_type == "plan":
                    plan_stages = data.get("stages", [])
                    stage_count = len(plan_stages)

                elif event_type == "file_start":
                    st.caption(f"📄 {data.get('path', '')}")

                elif event_type == "file_complete":
                    pass

                elif event_type == "stage_start":
                    current_stage = data.get("stage", "")
                    st.session_state.current_stage = current_stage
                    current_stage_content = ""

                elif event_type == "token":
                    current_stage_content += data.get("content", "")
                    placeholder.markdown(current_stage_content)

                elif event_type == "stage_complete":
                    completed_stages += 1
                    if stage_count > 0:
                        progress_bar.progress(
                            completed_stages / stage_count,
                            text=f"Resumed {completed_stages}/{stage_count}"
                        )
                    if current_stage_content:
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": f"### {current_stage}\n\n{current_stage_content}",
                            "stage": current_stage,
                        })
                    current_stage_content = ""

                elif event_type == "stage_error":
                    st.warning(f"Stage **{data.get('stage')}** failed: {data.get('error', '')}")

                elif event_type == "pipeline_error":
                    st.error(f"Resume failed: {data.get('error', '')}")
                    st.session_state.pipeline_running = False
                    return

                elif event_type == "pipeline_complete":
                    st.session_state.pipeline_running = False
                    progress_bar.progress(1.0, "Pipeline complete ✓")
                    st.toast("✅ Pipeline completed successfully!", icon="✅")

        get_projects(force_refresh=True)

    except requests.exceptions.ConnectionError:
        logger.error("Resume pipeline lost connection")
        st.error("连接中断 — 后端可能已停止。刷新页面后可从中断处继续。")
        st.session_state.pipeline_running = False
    except Exception:
        logger.exception("Unexpected resume error")
        st.error("恢复流水线时出错")
        st.session_state.pipeline_running = False


def run_pipeline(project_id: str, user_input: str, placeholder):
    """SSE streaming pipeline execution with cancel support."""
    st.session_state.pipeline_stop = False
    stage_count = 0
    completed_stages = 0
    stage_placeholders = {}

    try:
        resp = requests.post(
            f"{API_BASE}/agents/run/{project_id}",
            json={"user_input": user_input},
            stream=True,
            headers={"Accept": "text/event-stream"},
            timeout=(SSE_CONNECT_TIMEOUT, SSE_READ_TIMEOUT),
        )

        if resp.status_code != 200:
            st.error(f"Backend returned status {resp.status_code}: {resp.text[:200]}")
            st.session_state.pipeline_running = False
            return

        current_stage_content = ""
        current_stage = ""
        progress_bar = st.progress(0.0, text="Starting pipeline...")

        for line in resp.iter_lines():
            # Check for user-requested stop
            if st.session_state.get("pipeline_stop", False):
                resp.close()
                st.warning("Pipeline stopped by user.")
                st.session_state.pipeline_running = False
                return

            if line.startswith(b"data: "):
                data = json.loads(line[6:])
                event_type = data.get("type", "")

                if event_type == "plan":
                    plan_stages = data.get("stages", [])
                    st.session_state.plan_stages = plan_stages
                    stage_count = len(plan_stages)
                    # Build progress column placeholders
                    if stage_count > 0:
                        cols = st.columns(stage_count)
                        for i, s in enumerate(plan_stages):
                            with cols[i]:
                                stage_placeholders[s] = st.empty()
                                stage_placeholders[s].info(STAGE_LABELS.get(s, s))

                elif event_type == "file_start":
                    file_path = data.get("path", "")
                    st.caption(f"📄 {file_path}")

                elif event_type == "file_complete":
                    pass

                elif event_type == "stage_start":
                    current_stage = data.get("stage", "")
                    st.session_state.current_stage = current_stage
                    current_stage_content = ""
                    # Mark stage as running
                    if current_stage in stage_placeholders:
                        stage_placeholders[current_stage].warning(
                            f"{STAGE_LABELS.get(current_stage, current_stage)} ⏳"
                        )

                elif event_type == "token":
                    current_stage_content += data.get("content", "")
                    placeholder.markdown(current_stage_content)

                elif event_type == "stage_complete":
                    completed_stages += 1
                    duration = data.get("duration_seconds")
                    duration_str = f" ({duration:.1f}s)" if duration else ""
                    if stage_count > 0:
                        progress_bar.progress(
                            completed_stages / stage_count,
                            text=f"Completed {completed_stages}/{stage_count}: {STAGE_LABELS.get(current_stage, current_stage)}"
                        )
                    if current_stage in stage_placeholders:
                        stage_placeholders[current_stage].success(
                            f"{STAGE_LABELS.get(current_stage, current_stage)} ✓{duration_str}"
                        )
                    if current_stage_content:
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": f"### {current_stage}{duration_str}\n\n{current_stage_content}",
                            "stage": current_stage,
                        })
                    current_stage_content = ""

                elif event_type == "stage_error":
                    st.warning(f"Stage **{data.get('stage', 'unknown')}** failed: {data.get('error', 'Unknown error')}")
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"### {data.get('stage', 'unknown')} - Failed\n\n{data.get('error', '')}",
                        "stage": data.get("stage", ""),
                    })
                    if data.get("stage") in stage_placeholders:
                        stage_placeholders[data["stage"]].error(
                            f"{STAGE_LABELS.get(data['stage'], data['stage'])} ✗"
                        )

                elif event_type == "pipeline_error":
                    st.error(f"Pipeline fatal error: {data.get('error', 'Unknown error')}")
                    st.session_state.pipeline_running = False
                    return

                elif event_type == "pipeline_complete":
                    st.session_state.pipeline_running = False
                    progress_bar.progress(1.0, text="Pipeline complete ✓")
                    st.toast("✅ Pipeline completed successfully!", icon="✅")

        get_projects(force_refresh=True)  # refresh status after pipeline

    except requests.exceptions.ConnectionError:
        logger.error("Pipeline lost connection to backend")
        st.error("Connection lost — the backend may have stopped. Check and try again.")
        st.session_state.pipeline_running = False
    except requests.exceptions.Timeout:
        logger.error("Pipeline request timed out")
        st.error("Request timed out — the pipeline may be taking too long or the backend is unresponsive.")
        st.session_state.pipeline_running = False
    except Exception:
        logger.exception("Unexpected pipeline error")
        st.error(f"Pipeline error — check backend logs.")
        st.session_state.pipeline_running = False


# ── UI ──
init_session()

st.title("🛡️ Agent Development Platform")
st.caption("自然语言驱动 · 网络安全管理平台全流程开发")

# Sidebar
with st.sidebar:
    st.header("📁 项目")

    # Fetch projects (cached in api_client)
    projects = get_projects()
    project_map = get_project_dict()

    # ── Project selection ──
    if projects:
        st.caption(f"已有 {len(projects)} 个项目")
        # Use " | " separator to avoid bracket collision
        option_labels = [f"{p['name']} | {p['status']}" for p in projects]
        project_options = dict(zip(option_labels, [p["id"] for p in projects]))
        selected_label = st.selectbox(
            "选择项目",
            options=list(project_options.keys()),
            index=None,
            placeholder="点击选择一个项目...",
            key="project_selector",
        )
        if selected_label:
            pid = project_options[selected_label]
            if st.session_state.project_id != pid:
                st.session_state.project_id = pid
                proj = project_map.get(pid, {})
                st.session_state.project_name = proj.get("name", "")
                st.session_state.messages = []
                st.session_state._resume_mode = False
                # Load historical messages
                _load_history(pid)
                st.rerun()

    # ── Current project status ──
    if st.session_state.project_id:
        status_map = {"draft": "📝 草稿", "running": "⏳ 运行中", "completed": "✅ 已完成", "failed": "❌ 失败"}
        proj = project_map.get(st.session_state.project_id, {})
        status = proj.get("status", "draft")
        st.info(f"**{st.session_state.project_name}**\n\n{status_map.get(status, status)}")

        if st.button("↩ 切换项目", use_container_width=True, help="清除当前项目并返回选择"):
            if st.session_state.pipeline_running:
                st.warning("流水线正在运行中，请先等待完成或刷新页面中止")
            else:
                st.session_state.project_id = None
                st.session_state.messages = []
                st.rerun()

    st.divider()

    # ── Create new project ──
    with st.expander("➕ 创建新项目", expanded=not projects):
        name = st.text_input("项目名称", placeholder="输入项目名称...", key="new_project_name")
        desc = st.text_area("描述 (可选)", key="new_project_desc")
        if st.button("创建项目", use_container_width=True) and name:
            pid = create_project(name, desc)
            if pid:
                st.session_state.project_id = pid
                st.session_state.project_name = name
                st.rerun()

    st.divider()

    # ── Resume detection ──
    if st.session_state.project_id and not st.session_state.pipeline_running:
        try:
            status_resp = requests.get(
                f"{API_BASE}/agents/status/{st.session_state.project_id}",
                timeout=DEFAULT_TIMEOUT,
            )
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                run_status = status_data.get("status", "")
                if run_status in ("interrupted", "failed"):
                    last_stage = status_data.get("current_stage", "unknown")
                    stage_labels = {
                        "research": "🔍 深度研究", "prd": "📋 PRD", "design": "🏗️ 概要设计",
                        "database": "🗄️ 数据库设计", "backend": "⚙️ 后端代码",
                        "frontend": "🎨 前端代码", "review": "✅ 代码审查", "deploy": "🚀 部署配置",
                    }
                    label = stage_labels.get(last_stage, last_stage)
                    st.warning(
                        f"⚠️ 检测到未完成的流水线\n\n"
                        f"状态: {run_status}\n"
                        f"中断阶段: {label}\n\n"
                        f"可以从中断处继续执行。"
                    )
                    col_a, col_b = st.columns(2)
                    with col_a:
                        if st.button("🔄 从中断处继续", key="resume_pipeline", use_container_width=True):
                            st.session_state._resume_mode = True
                            st.rerun()
                    with col_b:
                        if st.button("🗑️ 放弃并新建", key="discard_run", use_container_width=True):
                            st.session_state._resume_mode = False
                            st.rerun()
        except Exception:
            pass  # Backend unavailable — silently skip

    st.header("🔍 知识库")
    if st.button("浏览知识库"):
        st.switch_page("pages/knowledge.py")

    st.divider()
    st.header("📊 流水线")
    if st.button("查看状态"):
        st.switch_page("pages/pipeline.py")

    # ── Mock LLM debug panel ──
    try:
        stats_resp = requests.get(f"{API_BASE}/system/mock-stats", timeout=(2, 3))
        if stats_resp.status_code == 200 and stats_resp.json().get("mock_active"):
            stats = stats_resp.json()
            st.divider()
            with st.expander("🧪 Mock LLM 调试面板", expanded=False):
                col_a, col_b = st.columns(2)
                col_a.metric("总调用次数", stats.get("total_calls", 0))
                handlers = stats.get("handlers", {})
                top = sorted(handlers.items(), key=lambda x: -x[1])[:5]
                col_b.metric("Handler 类型", len(handlers))
                if top:
                    st.caption("最活跃 Handler:")
                    for name, count in top:
                        st.caption(f"  • {name}: {count}")
                recent = stats.get("recent_calls", [])
                if recent:
                    st.caption(f"最近 {len(recent)} 次调用:")
                    for call in reversed(recent[-8:]):
                        st.caption(
                            f"`{call['handler']}` → {call['chars']} chars "
                            f"(t={call['temperature']}, max={call['max_tokens']})"
                        )
    except Exception:
        pass  # Backend unavailable or not in mock mode — silently skip

# ── Main chat area ──
if st.session_state.project_id:
    # Display messages
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input — disabled while pipeline is running
    is_running = st.session_state.pipeline_running
    input_placeholder = (
        "流水线运行中，请等待完成..." if is_running
        else "描述你需要构建的功能..."
    )
    if prompt := st.chat_input(input_placeholder, disabled=is_running):
        if not st.session_state.pipeline_running:
            if st.session_state.get("_resume_mode"):
                # Resume flow: use resume endpoint
                st.session_state._resume_mode = False
                st.session_state.pipeline_running = True
                with st.chat_message("assistant"):
                    placeholder = st.empty()
                    cancel_col, _ = st.columns([1, 5])
                    with cancel_col:
                        if st.button("⏹ 停止", key="stop_pipeline", help="中止当前流水线"):
                            st.session_state.pipeline_stop = True
                    _run_resume_pipeline(st.session_state.project_id, placeholder)
            else:
                # New run flow
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(prompt)
                with st.chat_message("assistant"):
                    placeholder = st.empty()
                    cancel_col, _ = st.columns([1, 5])
                    with cancel_col:
                        if st.button("⏹ 停止", key="stop_pipeline", help="中止当前流水线"):
                            st.session_state.pipeline_stop = True
                    run_pipeline(st.session_state.project_id, prompt, placeholder)
else:
    st.info("👈 在左侧创建或选择一个项目开始")
