"""Shared API client — eliminates duplicate request wrappers across frontend pages."""

import logging
import requests
import streamlit as st
from frontend.config import API_BASE, DEFAULT_TIMEOUT, SEARCH_TIMEOUT

logger = logging.getLogger("frontend.api")

# ── Project-level cache in session_state ──

def get_projects(force_refresh: bool = False) -> list[dict]:
    """Fetch project list with session_state caching. Returns [] on failure."""
    cache_key = "_cached_projects"
    ts_key = "_cached_projects_ts"
    import time

    if not force_refresh and cache_key in st.session_state:
        age = time.time() - st.session_state.get(ts_key, 0)
        if age < 10:  # 10-second cache
            return st.session_state[cache_key]

    try:
        resp = requests.get(f"{API_BASE}/projects", timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            st.session_state[cache_key] = resp.json()
            st.session_state[ts_key] = time.time()
            return st.session_state[cache_key]
    except requests.exceptions.ConnectionError:
        logger.warning("Backend unreachable during project list fetch")
    except Exception:
        logger.exception("Unexpected error fetching project list")
    return st.session_state.get(cache_key, [])


def get_project_dict() -> dict[str, dict]:
    """Return {project_id: project} dict for O(1) lookups."""
    projects = get_projects()
    return {p["id"]: p for p in projects}


# ── Generic request helpers ──

def _full_url(endpoint: str) -> str:
    return f"{API_BASE}{endpoint}"


def api_get(endpoint: str, timeout=DEFAULT_TIMEOUT) -> requests.Response | None:
    """GET request with error handling. Returns Response or None."""
    try:
        return requests.get(_full_url(endpoint), timeout=timeout)
    except requests.exceptions.ConnectionError:
        st.error(f"无法连接后端服务 ({API_BASE})\n\n请确认后端已启动: `python run.py`")
        return None
    except requests.exceptions.Timeout:
        st.error(f"请求超时 ({_full_url(endpoint)})")
        return None
    except Exception:
        logger.exception(f"GET {endpoint} failed")
        st.error("请求失败，请稍后重试")
        return None


def api_post(endpoint: str, json_data: dict | None = None, timeout=DEFAULT_TIMEOUT) -> requests.Response | None:
    """POST request with error handling."""
    try:
        return requests.post(_full_url(endpoint), json=json_data or {}, timeout=timeout)
    except requests.exceptions.ConnectionError:
        st.error(f"无法连接后端服务 ({API_BASE})")
        return None
    except requests.exceptions.Timeout:
        st.error(f"请求超时 ({_full_url(endpoint)})")
        return None
    except Exception:
        logger.exception(f"POST {endpoint} failed")
        st.error("请求失败")
        return None


def api_delete(endpoint: str, timeout=DEFAULT_TIMEOUT) -> requests.Response | None:
    """DELETE request with error handling."""
    try:
        return requests.delete(_full_url(endpoint), timeout=timeout)
    except requests.exceptions.ConnectionError:
        st.error(f"无法连接后端服务 ({API_BASE})")
        return None
    except requests.exceptions.Timeout:
        st.error(f"请求超时 ({_full_url(endpoint)})")
        return None
    except Exception:
        logger.exception(f"DELETE {endpoint} failed")
        st.error("请求失败")
        return None


def api_get_or_stop(endpoint: str, timeout=DEFAULT_TIMEOUT) -> requests.Response:
    """GET request for critical page-load data. Returns Response, or None if backend unreachable."""
    try:
        resp = requests.get(_full_url(endpoint), timeout=timeout)
        return resp
    except requests.exceptions.ConnectionError:
        st.error(f"无法连接后端服务 ({API_BASE})")
        st.info("启动命令: `python run.py`")
        return None
    except requests.exceptions.Timeout:
        st.error(f"请求超时 ({_full_url(endpoint)})")
        return None
    except Exception:
        logger.exception(f"Critical GET {endpoint} failed")
        st.error("请求失败")
        return None
