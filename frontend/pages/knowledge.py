"""Knowledge Base Management Page"""

import streamlit as st

from frontend.api_client import api_get, api_post, api_delete
from frontend.config import DEFAULT_TIMEOUT, SEARCH_TIMEOUT

st.set_page_config(page_title="Knowledge Base", page_icon="🔍", layout="wide")
st.title("🔍 知识库管理")

tab1, tab2, tab3 = st.tabs(["📊 概览", "🔎 搜索测试", "📤 上传文档"])

with tab1:
    st.subheader("知识库统计")
    resp = api_get("/knowledge/stats")
    if resp and resp.status_code == 200:
        stats = resp.json()
        col1, col2, col3 = st.columns(3)
        col1.metric("知识库数量", stats.get("kb_count", 0))
        col2.metric("文档数量", stats.get("doc_count", 0))
        col3.metric("分块数量", stats.get("chunk_count", 0))

        if stats.get("by_type"):
            st.subheader("按类型分布")
            st.json(stats["by_type"])

        # Delete knowledge base
        st.divider()
        st.subheader("🗑️ 删除知识库")
        kb_to_delete = st.text_input("输入要删除的知识库名称", placeholder="e.g., security_compliance")
        if st.button("删除知识库", type="secondary"):
            if kb_to_delete:
                resp_del = api_delete(f"/knowledge/{kb_to_delete}")
                if resp_del and resp_del.status_code == 200:
                    st.success(resp_del.json().get("message", "已删除"))
                    st.rerun()
                elif resp_del:
                    st.error(f"删除失败: {resp_del.json().get('detail', resp_del.text)}")
            else:
                st.warning("请输入知识库名称")
    else:
        st.info("知识库为空，请先上传文档或初始化种子数据")

with tab2:
    st.subheader("检索测试")
    query = st.text_input("查询", placeholder="输入检索查询...")
    kb_types = st.multiselect("知识库类型过滤", ["compliance", "pattern", "template", "schema", "deploy"])
    top_k = st.slider("Top K", 1, 10, 5)

    if st.button("搜索") and query:
        payload = {"query": query, "top_k": top_k}
        if kb_types:
            payload["kb_types"] = kb_types
        resp = api_post("/knowledge/search", payload, timeout=SEARCH_TIMEOUT)
        if resp and resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            variants = data.get("query_variants", [])

            if variants:
                st.caption(f"查询变体: {', '.join(variants)}")

            if results:
                for i, r in enumerate(results):
                    with st.expander(f"[{i+1}] Score: {r.get('score', 0):.4f} | {r.get('doc_title', '')} ({r.get('kb_name', '')})"):
                        st.markdown(r.get("content", ""))
                        st.caption(f"Metadata: {r.get('metadata', {})}")
            else:
                st.info("无结果 — 知识库可能为空")

with tab3:
    st.subheader("上传文档到知识库")

    # File uploader — drag & drop .md / .txt files
    uploaded_files = st.file_uploader(
        "拖放或选择 Markdown 文件 (.md, .txt)",
        type=["md", "txt"],
        accept_multiple_files=True,
        help="支持批量上传多个文件",
    )

    kb_name = st.text_input("知识库名称", placeholder="e.g., security_compliance")
    kb_type = st.selectbox("知识库类型", ["compliance", "pattern", "template", "schema", "deploy"])

    # Manual text input (fallback / supplement)
    title_manual = st.text_input("文档标题 (手动输入)", placeholder="或通过上方文件上传自动填充")
    content_manual = st.text_area("文档内容 (Markdown，手动粘贴)", height=200)

    # Upload button — processes files first, then manual input
    if st.button("📤 上传索引", use_container_width=True):
        uploaded_count = 0

        # Process uploaded files
        if uploaded_files:
            for f in uploaded_files:
                file_content = f.read().decode("utf-8")
                file_title = f.name.rsplit(".", 1)[0]
                resp = api_post("/knowledge/documents", {
                    "kb_name": kb_name, "kb_type": kb_type,
                    "title": file_title, "content": file_content, "format": "md",
                })
                if resp and resp.status_code == 200:
                    uploaded_count += 1
            if uploaded_count:
                st.success(f"已上传并索引 {uploaded_count} 个文件")

        # Process manual text input
        if title_manual and content_manual and kb_name:
            resp = api_post("/knowledge/documents", {
                "kb_name": kb_name, "kb_type": kb_type,
                "title": title_manual, "content": content_manual, "format": "md",
            })
            if resp and resp.status_code == 200:
                st.success("手动文档已上传并索引")
                uploaded_count += 1
            elif resp:
                st.error(f"上传失败: {resp.text}")

        if not uploaded_count:
            st.warning("请选择文件上传或填写文档标题和内容")
