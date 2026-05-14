"""Project Management Page"""

import streamlit as st

from frontend.api_client import get_projects, api_get_or_stop, api_post, api_delete

st.set_page_config(page_title="Projects", page_icon="📁", layout="wide")
st.title("📁 项目管理")


# ── Load projects ──
resp = api_get_or_stop("/projects")
projects = resp.json() if resp and resp.status_code == 200 else []

# ── Render project table ──
if projects:
    for p in projects:
        col1, col2, col3, col4, col5, col6 = st.columns([3, 3, 1.5, 1.5, 1, 1])
        with col1:
            st.markdown(f"**{p['name']}**")
        with col2:
            desc = p.get("description", "")
            if len(desc) > 60:
                with st.expander(f"{desc[:57]}..."):
                    st.caption(desc)
            else:
                st.caption(desc)
        with col3:
            status = p.get("status", "draft")
            color = {"draft": "gray", "running": "orange", "completed": "green", "failed": "red"}.get(status, "gray")
            st.markdown(f":{color}[{status}]")
        with col4:
            st.caption(p.get("created_at", "")[:10] if p.get("created_at") else "")
        with col5:
            pid = p["id"]
            if st.button("📋", key=f"view_{pid}", help="查看产出物"):
                st.session_state.selected_project_id = pid
                st.switch_page("pages/artifacts.py")
        with col6:
            confirm_key = f"confirm_del_{pid}"
            if st.button("🗑️", key=f"del_{pid}", help="删除项目"):
                if st.session_state.get(confirm_key):
                    try:
                        resp_del = api_delete(f"/projects/{pid}")
                        if resp_del and resp_del.status_code == 200:
                            get_projects(force_refresh=True)
                            st.success("已删除")
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
                        elif resp_del:
                            st.error(f"删除失败 ({resp_del.status_code})")
                        st.session_state.pop(confirm_key, None)
                    except Exception:
                        st.session_state.pop(confirm_key, None)
                else:
                    st.session_state[confirm_key] = True
                    st.warning(f"确认删除「{p['name']}」？再次点击删除按钮确认。")

    st.divider()

# ── Create form (shown regardless of whether projects exist) ──
st.subheader("创建新项目")
with st.form("create_project"):
    name = st.text_input("项目名称")
    desc = st.text_area("描述")
    if st.form_submit_button("创建") and name:
        resp = api_post("/projects", {"name": name, "description": desc})
        if resp and resp.status_code == 200:
            get_projects(force_refresh=True)
            st.success("项目已创建")
            st.rerun()
        elif resp:
            st.error(f"创建失败: {resp.text}")
