"""Pipeline Visualization Page — with dynamic stage rendering from plan."""

import streamlit as st

from frontend.api_client import get_projects, api_get_or_stop

st.set_page_config(page_title="Pipeline", page_icon="🔄", layout="wide")
st.title("🔄 流水线状态")

DEFAULT_STAGES = ["research", "prd", "design", "database", "backend", "frontend", "review", "deploy"]
STAGE_EMOJI = {
    "research": "🔍", "prd": "📋", "design": "🏗️",
    "database": "🗄️", "backend": "⚙️", "frontend": "🎨",
    "review": "✅", "deploy": "🚀",
}

# ── Project selection ──
project_id = st.session_state.get("project_id", "")
if not project_id:
    projects = get_projects()
    if projects:
        project_id = st.selectbox(
            "选择项目",
            options=[p["id"] for p in projects],
            format_func=lambda x: next((p["name"] for p in projects if p["id"] == x), x),
        )

# ── Pipeline status ──
if project_id:
    resp = api_get_or_stop(f"/agents/status/{project_id}")
    data = resp.json()
    status = data.get("status", "unknown")
    current_stage = data.get("current_stage", "")

    col1, col2, col3 = st.columns(3)
    col1.metric("状态", status.upper())
    col2.metric("当前阶段", current_stage or "N/A")
    col3.metric("最近更新", "刚刚" if status == "running" else "—")

    st.divider()

    # Use plan stages if available, otherwise default
    stages = st.session_state.get("plan_stages", DEFAULT_STAGES)

    cols = st.columns(len(stages))
    current_idx = stages.index(current_stage) if current_stage in stages else -1

    for i, (stage, col) in enumerate(zip(stages, cols)):
        with col:
            label = f"{STAGE_EMOJI.get(stage, '•')} {stage}"
            if i < current_idx:
                st.success(label)
            elif i == current_idx:
                st.warning(f"{label} ⏳")
            else:
                st.info(label)

    st.divider()
    st.caption(f"共 {len(stages)} 个阶段 | 当前: {current_stage or 'N/A'}")

    # Auto-refresh via Streamlit native mechanism (no sleep blocking)
    if st.button("🔄 刷新状态"):
        st.rerun()
