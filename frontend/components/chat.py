"""Reusable Chat Component"""

import streamlit as st


def chat_message(role: str, content: str, stage: str = ""):
    """Render a chat message with optional stage badge."""
    with st.chat_message(role):
        if stage:
            st.caption(f"Stage: {stage}")
        st.markdown(content)


def chat_input_area(placeholder: str = "描述你的需求...") -> str | None:
    """Render chat input and return user input if submitted."""
    return st.chat_input(placeholder)


def streaming_placeholder():
    """Return a placeholder for streaming content."""
    return st.empty()


def display_messages(messages: list[dict]):
    """Render a list of messages."""
    for msg in messages:
        chat_message(msg.get("role", "user"), msg.get("content", ""), msg.get("stage", ""))
