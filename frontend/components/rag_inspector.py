"""RAG Inspector Component — Display RAG retrieval results"""

import streamlit as st


def rag_results_panel(results: list[dict], expanded: bool = False):
    """Display RAG retrieval results in an expandable panel."""
    if not results:
        st.caption("No RAG results available")
        return

    with st.expander(f"🔍 RAG 检索结果 ({len(results)} docs)", expanded=expanded):
        for i, doc in enumerate(results):
            score = doc.get("score", doc.get("rrf_score", 0))
            source = doc.get("source", "unknown")
            metadata = doc.get("metadata", {})
            kb = metadata.get("kb_name", "unknown")
            title = metadata.get("doc_title", "Untitled")

            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**[{i+1}] {title}** _{kb}_")
            with col2:
                st.metric("Score", f"{score:.4f}")

            with st.expander(f"Preview ({source})", expanded=False):
                content = doc.get("content", "")
                if len(content) > 1000:
                    st.markdown(content[:1000])
                    with st.expander("📖 显示全部内容"):
                        st.markdown(content)
                else:
                    st.markdown(content)

            if i < len(results) - 1:
                st.divider()


def rag_context_used_badge(context: str):
    """Show a badge if RAG context was used."""
    if context:
        st.success(f"RAG Context: {len(context)} chars injected")
        with st.expander("View RAG Context"):
            st.text(context[:2000])
    else:
        st.caption("No RAG context")


def query_variants_display(variants: list[str]):
    """Show expanded query variants."""
    if variants and len(variants) > 1:
        with st.expander(f"查询变体 ({len(variants)})"):
            for v in variants:
                st.caption(f"• {v}")


def graph_context_display(graph_ctx: list[dict]):
    """Display knowledge graph context."""
    if not graph_ctx:
        return
    with st.expander(f"🕸️ 知识图谱上下文 ({len(graph_ctx)} entities)"):
        for entity in graph_ctx:
            st.markdown(f"**{entity.get('entity', '')}** ({entity.get('type', '')})")
            st.caption(entity.get("description", ""))
            for rel in entity.get("relations", []):
                st.caption(f"  → {rel.get('target', '')}: {rel.get('relation', '')}")
