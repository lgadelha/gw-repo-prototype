"""Shared by all pages: API access (cached 30 s), finished-attempt filter, process
name normalisation, display formatting, page elements (header, KPI cards, empty
states), fixed chart colours and the pan/zoom graph view."""

import json
import os
import sys

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

sys.path.append('api')
from nfcore_modules import normalize_module_name  # noqa: E402

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost")
FINISHED_STATUSES = ('COMPLETED', 'CACHED')
# Same list as theme.chartCategoricalColors in .streamlit/config.toml.
PALETTE = ['#2563eb', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16', '#f97316',
           '#64748b']
STATE_COLOURS = {'COMPLETED': '#10b981', 'FAILED': '#ef4444', 'RUNNING': '#2563eb'}


def api_key() -> str:
    return st.session_state.get('api_key', '')


@st.cache_data(ttl=30, show_spinner="Loading from the API…")
def _cached_get(path: str, key: str, params: tuple):
    response = requests.get(f"{API_BASE_URL}{path}", headers={"Authorization": f"Bearer {key}"},
                            params=dict(params), timeout=30)
    response.raise_for_status()
    return response.json()


def api_get(path: str, _quiet: bool = False, **params):
    """GET an API path (cached 30 s per key and parameters); None after showing the
    error, or silently with `_quiet` (for optional data, e.g. a 404). Each kind of
    error is shown once per page, at the top (app.py's error box)."""
    try:
        return _cached_get(path, api_key(), tuple(sorted(params.items())))
    except requests.RequestException as e:
        status = e.response.status_code if e.response is not None else None
        shown = st.session_state.setdefault('_errors_shown', set())
        if not _quiet and (type(e), status) not in shown:
            shown.add((type(e), status))
            st.session_state.get('_error_box', st).error(f"API request {path} failed: {e}", icon=":material/error:")
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


STATE_BADGES = {'COMPLETED': ('green', ':material/check_circle:'), 'FAILED': ('red', ':material/error:'),
                'RUNNING': ('blue', ':material/progress_activity:')}


def page_header(title: str, run=None):
    """Page title, then for run pages one line with the run's state badge, name,
    revision, start and institute (the run label is too long for a title)."""
    st.title(title)
    if run is None:
        return
    colour, icon = STATE_BADGES.get(run['state'], ('gray', ':material/help:'))
    start = run['start'].strftime('%Y-%m-%d %H:%M') if pd.notna(run['start']) else None
    parts = [f"**{run.get('run_name') or run['id'][:8]}**", run.get('revision_id'), start,
             run.get('institute_id')]
    st.markdown(f":{colour}-badge[{icon} {run['state']}] &nbsp; "
                + " · ".join(str(p) for p in parts if isinstance(p, str) and p))


def kpi(column, label: str, value, caption: str = None, help: str = None):
    """A KPI card: bordered, with an optional line of detail under the value."""
    with column.container(border=True, height="stretch"):
        st.metric(label, value, help=help)
        if caption:
            st.caption(caption)


def empty_state(message: str):
    """No data to show: what is missing and how to get it."""
    st.info(message, icon=":material/info:")


def _ordered_colours(column: str) -> dict:
    """Value of an all_runs column -> palette colour, in order of first run start, so
    a run or institute keeps its colour on every page as new runs arrive."""
    runs = st.session_state.get('all_runs', pd.DataFrame())
    if runs.empty:
        return {}
    values = dict.fromkeys(runs.sort_values('start', na_position='last')[column].dropna())
    return {v: PALETTE[i % len(PALETTE)] for i, v in enumerate(values)}


def institute_colours() -> dict:
    return _ordered_colours('institute_id')


def run_colours(names: dict) -> dict:
    """Display name -> colour, for charts that show runs under `names` (run id ->
    display name)."""
    by_id = _ordered_colours('id')
    return {names[w]: c for w, c in by_id.items() if w in names}


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


GRAPH_VIEW = """
<style>
  body {{ margin: 0; font: 13px Inter, Helvetica, Arial, sans-serif; color: #1e293b; }}
  #g {{ width: 100%; height: {height}px; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: center; padding: 8px 2px 0; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .sw {{ display: inline-block; width: 22px; height: 12px; border-radius: 3px; border: 1px solid #94a3b8; }}
  .graph > polygon:first-of-type {{ fill: transparent; }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #e2e8f0; }}
    #g {{ background: #1e293b; border-color: #334155; }}
    .edge path[stroke="#888888"] {{ stroke: #94a3b8; }}
    .edge polygon[stroke="#888888"] {{ stroke: #94a3b8; fill: #94a3b8; }}
    .edge text {{ fill: #cbd5e1; }}
  }}
</style>
<div id="g"></div>
<div class="legend">{legend}</div>
<script src="https://cdn.jsdelivr.net/npm/@viz-js/viz@3.11.0/lib/viz-standalone.js"></script>
<script src="https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.2/dist/svg-pan-zoom.min.js"></script>
<script>
Viz.instance().then(viz => {{
  const svg = viz.renderSVGElement({dot});
  svg.setAttribute('width', '100%'); svg.setAttribute('height', '100%');
  const box = document.getElementById('g');
  box.appendChild(svg);
  let pz = null;  // created once visible: a graph in a hidden tab has no size to fit to
  new ResizeObserver(() => {{
    if (!box.clientWidth) return;
    if (!pz) pz = svgPanZoom(svg, {{controlIconsEnabled: true, fit: true, center: true, minZoom: 0.1, maxZoom: 20}});
    else {{ pz.resize(); pz.fit(); pz.center(); }}
  }}).observe(box);
}});
</script>
"""


def graph_view(dot: str, height: int = 600, legend: list = ()):
    """Graphviz DOT drawn in the browser (viz.js) with mouse-wheel zoom, drag to pan
    and +/−/reset buttons (svg-pan-zoom); node tooltips are kept. `legend`: (CSS
    background of the swatch, text) pairs shown under the graph."""
    items = ''.join(f'<span><i class="sw" style="background:{css}"></i>{text}</span>' for css, text in legend)
    components.html(GRAPH_VIEW.format(height=height, legend=items, dot=json.dumps(dot).replace('</', '<\\/')),
                    height=height + (40 if legend else 10))
