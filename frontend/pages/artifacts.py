"""Artifacts browser page."""

import os
import re
import logging

import streamlit as st

from frontend.components.code_viewer import code_file_viewer
from frontend.api_client import get_projects, api_get_or_stop

logger = logging.getLogger("frontend.artifacts")


def parse_path_code_files(content: str) -> list[tuple[str, str]]:
    """Parse PATH/CODE multi-file artifacts. Uses line-by-line parser to avoid regex backtracking."""
    text = (content or "").replace("\r\n", "\n")
    files = []
    current_path = None
    current_lines = []

    for line in text.split("\n"):
        path_match = re.match(r"^\s*PATH\s*[:：]\s*(.+?)\s*$", line, re.IGNORECASE)
        if path_match:
            if current_path and current_lines:
                code = "\n".join(current_lines).strip()
                # Strip trailing ``` fence if present
                if code.endswith("```"):
                    code = code[:-3].strip()
                files.append((current_path, code))
            current_path = path_match.group(1).strip()
            current_lines = []
            continue

        code_match = re.match(r"^\s*CODE\s*[:：]\s*$", line, re.IGNORECASE)
        if code_match:
            continue  # CODE marker line, skip

        if current_path is not None:
            current_lines.append(line)

    # Last file
    if current_path and current_lines:
        code = "\n".join(current_lines).strip()
        if code.endswith("```"):
            code = code[:-3].strip()
        files.append((current_path, code))

    # Fallback: if no PATH/CODE found, treat entire content as single file
    if not files and text.strip():
        files.append(("output.txt", text.strip()))

    return files


def display_code_artifact(content: str, file_path_summary: str = ""):
    files = parse_path_code_files(content)
    if not files:
        st.markdown(content)
        return

    st.caption(f"{len(files)} generated files")
    if file_path_summary:
        st.caption(f"Saved: {file_path_summary}")

    labels = [os.path.basename(path) or path for path, _ in files]
    tabs = st.tabs(labels[:20])
    for tab, (path, code) in zip(tabs, files[:20]):
        with tab:
            code_file_viewer(code, filename=path, line_numbers=False)

    if len(files) > 20:
        with st.expander(f"Additional files ({len(files) - 20})"):
            for path, code in files[20:]:
                with st.expander(path):
                    code_file_viewer(code, filename=path, line_numbers=False)


ACITEP_MAP = {
    "prd": "md", "design": "md", "database": "sql", "review": "md", "deploy": "yml",
    "backend_code": "py", "frontend_code": "tsx", "other": "md",
}

st.set_page_config(page_title="Artifacts", page_icon="📦", layout="wide")
st.title("产物浏览")

project_id = st.query_params.get("project_id", st.session_state.get("project_id", ""))

if not project_id:
    projects = get_projects()
    if projects:
        project_id = st.selectbox(
            "选择项目",
            options=[p["id"] for p in projects],
            format_func=lambda x: next((p["name"] for p in projects if p["id"] == x), x),
        )

if project_id:
    resp = api_get_or_stop(f"/artifacts/project/{project_id}")
    artifacts = resp.json()
    if not artifacts:
        st.info("📦 暂无产物 — 运行流水线后产物会出现在这里")
        st.stop()

    # Auto-expand when few artifacts; collapsed when many
    auto_expand = len(artifacts) <= 3

    for artifact in artifacts:
        stage = artifact.get("stage", "")
        title = artifact.get("title", "Untitled")
        artifact_type = artifact.get("type", "")
        file_path = artifact.get("file_path") or ""
        content = artifact.get("content", "")

        with st.expander(f"[{stage}] {title}", expanded=auto_expand):
            if artifact_type in {"backend_code", "frontend_code"} or parse_path_code_files(content):
                display_code_artifact(content, file_path_summary=file_path)
            else:
                st.markdown(content)

            # Download button for text-based artifacts
            file_ext = ACITEP_MAP.get(artifact_type, "md")
            st.download_button(
                label=f"📥 下载 {title}",
                data=content,
                file_name=f"{stage}_{title}.{file_ext}",
                mime="text/plain",
                key=f"dl_{artifact.get('id', stage)}",
            )

            if artifact.get("rag_context_used"):
                st.caption("RAG Context Used")
            st.caption(f"Created: {artifact.get('created_at', '')} | Type: {artifact_type}")
