"""
Shared frontend configuration.
Use Streamlit secrets or environment variables for deployment overrides.
"""

import os

# API base URL — override via env var or Streamlit secrets
_API_BASE_ENV = os.environ.get("API_BASE", "")
if _API_BASE_ENV:
    API_BASE = _API_BASE_ENV.rstrip("/")
else:
    try:
        import streamlit as st
        API_BASE = st.secrets.get("api_base", "http://127.0.0.1:8000/api")
    except Exception:
        API_BASE = "http://127.0.0.1:8000/api"

# Default timeout in seconds for API requests (connect, read)
DEFAULT_TIMEOUT = (10, 30)

# Longer timeout for search and SSE operations
SEARCH_TIMEOUT = (10, 150)
SSE_CONNECT_TIMEOUT = 20
SSE_READ_TIMEOUT = 1200
