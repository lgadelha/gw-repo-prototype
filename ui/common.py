"""Shared by all pages: API access (cached 30 s), finished-attempt filter, process
name normalisation and display formatting."""

import os
import sys

import pandas as pd
import requests
import streamlit as st

sys.path.append('api')
from nfcore_modules import normalize_module_name  # noqa: E402

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost")
FINISHED_STATUSES = ('COMPLETED', 'CACHED')


def api_key() -> str:
    return st.session_state.get('api_key', '')


@st.cache_data(ttl=30, show_spinner=False)
def _cached_get(path: str, key: str, params: tuple):
    response = requests.get(f"{API_BASE_URL}{path}", headers={"Authorization": f"Bearer {key}"},
                            params=dict(params), timeout=30)
    response.raise_for_status()
    return response.json()


def api_get(path: str, _quiet: bool = False, **params):
    """GET an API path (cached 30 s per key and parameters); None after showing the
    error, or silently with `_quiet` (for optional data, e.g. a 404)."""
    try:
        return _cached_get(path, api_key(), tuple(sorted(params.items())))
    except requests.RequestException as e:
        if not _quiet:
            st.error(f"API request {path} failed: {e}")
        return None


def clear_api_cache():
    _cached_get.clear()


def finished(df: pd.DataFrame) -> pd.DataFrame:
    """Only attempts that finished: failed or killed attempts have cut-off peaks
    and would distort averages."""
    if 'final_status' not in df.columns:
        return df
    return df[df['final_status'].isin(FINISHED_STATUSES)]


def pipeline_label(row) -> str:
    """Repository without the host for pipelines run from a repository, else the
    main script's hash."""
    repo = row.get('repository')
    if isinstance(repo, str) and repo:
        return repo.split('://', 1)[-1].split('/', 1)[-1]
    script = row.get('script_id')
    return f"local script {script[:8]}" if isinstance(script, str) and script else "unknown"


def run_label(row) -> str:
    start = pd.to_datetime(row.get('start_time'), unit='s', errors='coerce')
    parts = [row.get('run_name') or row['id'][:8], row.get('revision_id'),
             start.strftime('%Y-%m-%d %H:%M') if pd.notna(start) else None,
             row.get('final_state') or 'RUNNING']
    return ' · '.join(str(p) for p in parts if isinstance(p, str) and p)


def fmt_duration(seconds) -> str:
    if seconds is None or pd.isna(seconds):
        return '–'
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def fmt_mb(mb) -> str:
    if mb is None or pd.isna(mb):
        return '–'
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"
