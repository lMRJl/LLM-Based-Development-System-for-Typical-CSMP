"""Code Viewer Component — Syntax Highlighting Preview with copy button."""

import os
import html
import streamlit as st

LANGUAGE_MAP = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".sql": "sql",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".md": "markdown", ".html": "html", ".css": "css",
    ".sh": "bash", ".dockerfile": "dockerfile", ".go": "go",
    ".java": "java", ".cs": "csharp", ".rs": "rust",
}


def _copy_button_html(text: str, button_label: str = "📋 复制") -> str:
    """Generate an HTML copy-to-clipboard button."""
    safe_text = html.escape(text, quote=True)
    # Escape backticks for JS template literal
    backtick_escaped = safe_text.replace("`", "\\`")
    return (
        '<button onclick="navigator.clipboard.writeText(`'
        + backtick_escaped
        + '`)" style="padding:4px 12px;margin:4px 0;border:1px solid #ccc;'
        'border-radius:4px;background:#f8f8f8;cursor:pointer;font-size:13px">'
        + button_label
        + '</button>'
    )


def code_block(code: str, language: str = "python", title: str = ""):
    """Display a syntax-highlighted code block."""
    if title:
        st.caption(title)
    st.code(code, language=language)


def code_file_viewer(content: str, filename: str = "", line_numbers: bool = True):
    """Render a file-like code viewer with copy-to-clipboard support."""
    ext = os.path.splitext(filename)[1].lower() if filename else ""
    lang = LANGUAGE_MAP.get(ext, "text")

    if filename:
        col1, col2 = st.columns([5, 1])
        with col1:
            st.caption(f"📄 {filename}")
        with col2:
            st.html(_copy_button_html(content))

    if line_numbers and content:
        lines = content.split("\n")
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        st.code(numbered, language=lang, line_numbers=False)
    else:
        st.code(content, language=lang)


def diff_viewer(original: str, modified: str):
    """Simple side-by-side diff viewer."""
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Original")
        st.code(original, language="text")
    with col2:
        st.caption("Modified")
        st.code(modified, language="text")
