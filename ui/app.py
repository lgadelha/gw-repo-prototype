"""GW-RePO Streamlit UI: navigation, sidebar (API key, run filters and selector,
refresh) and page wiring. Pages are ui/nav/*.py; their rendering code is in
ui/views/, pure computations in ui/runprofile.py. See PLAN-frontend.md."""

import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from common import api_get, api_key, clear_api_cache
from views import runs

STATIC = Path(__file__).parent / "static"
st.set_page_config(page_title="GW-RePO", page_icon=str(STATIC / "icon.svg"), layout="wide")
st.logo(str(STATIC / "logo.svg"), icon_image=str(STATIC / "icon.svg"), size="large")
st.session_state['_error_box'] = st.container()  # API errors, once each, above the page (common.api_get)
st.session_state['_errors_shown'] = set()

REFRESH_S = 15

if 'api_key' not in st.session_state:
    st.session_state['api_key'] = os.getenv("API_KEY", "")
if 'run' not in st.session_state:
    st.session_state['run'] = st.query_params.get('run')


# Each page is a small script in ui/nav/ (file pages, so AppTest can switch to them);
# they read the run list that the sidebar below puts in session state.
navigation = st.navigation({
    "Observability": [st.Page("nav/runs.py", title="Runs", icon=":material/list_alt:", url_path="runs", default=True),
                      st.Page("nav/run_detail.py", title="Run detail", icon=":material/monitoring:", url_path="run"),
                      st.Page("nav/provenance.py", title="Provenance", icon=":material/account_tree:", url_path="provenance"),
                      st.Page("nav/samples.py", title="Samples", icon=":material/science:", url_path="samples"),
                      st.Page("nav/energy.py", title="Energy & CO2", icon=":material/eco:", url_path="energy")],
    "Across runs": [st.Page("nav/process_profile.py", title="Process profile", icon=":material/memory:", url_path="process"),
                    st.Page("nav/compare.py", title="Compare runs", icon=":material/compare_arrows:", url_path="compare")],
    "Optimization": [st.Page("nav/optimization.py", title="Bayesian modelling", icon=":material/model_training:",
                             url_path="optimization")],
})


def run_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Sidebar filters: institute, pipeline, start date. Runs without a start time
    (created but not begun) always pass the date filter."""
    institutes = sorted(df['institute_id'].dropna().unique())
    chosen = st.multiselect("Institutes", institutes, default=institutes, placeholder="All institutes")
    pipeline = st.selectbox("Pipeline", ["All"] + sorted(df['pipeline'].unique()))
    out = df[df['institute_id'].isin(chosen or institutes)]
    if pipeline != "All":
        out = out[out['pipeline'] == pipeline]
    starts = df['start'].dropna()
    if not starts.empty:
        first, last = starts.min().date(), starts.max().date()
        dates = st.date_input("Started between", value=(first, last), min_value=first, max_value=last)
        if isinstance(dates, tuple) and len(dates) == 2:
            day = out['start'].dt.date
            out = out[out['start'].isna() | ((day >= dates[0]) & (day <= dates[1]))]
    return out


with st.sidebar:
    with st.expander("Connection", icon=":material/key:", expanded=not api_key()):
        st.text_input("API Key", type="password", key="api_key")
    all_runs = pd.DataFrame()
    filtered_runs = all_runs
    if api_key():
        all_runs = runs.prepare(api_get("/workflows/summary") or [])
    if not all_runs.empty:
        st.subheader("Runs")
        with st.expander("Filters", icon=":material/filter_list:"):
            filtered_runs = run_filters(all_runs)
        st.caption(f"Showing {len(filtered_runs)} of {len(all_runs)} runs")
        options = filtered_runs['id'].tolist()
        current = st.session_state.get('run')
        if current in all_runs['id'].values and current not in options:
            options = [current] + options  # keep a linked or selected run visible whatever the filters
        labels = dict(zip(all_runs['id'], all_runs['run_label']))
        pick, refresh = st.columns([5, 1], vertical_alignment="bottom")
        choice = pick.selectbox("Run", options, index=options.index(current) if current in options else 0,
                                format_func=labels.get) if options else None
        if refresh.button("", icon=":material/refresh:", help="Data is cached for 30 s; this fetches it now"):
            st.cache_data.clear()
            st.rerun()
        st.session_state['run'] = choice
        if choice:
            st.query_params['run'] = choice
    st.session_state['all_runs'] = all_runs
    st.session_state['filtered_runs'] = filtered_runs
    st.session_state['_last_full_run'] = time.time()
    running = not filtered_runs.empty and (filtered_runs['state'] == 'RUNNING').any()
    if running and st.toggle(f"Auto-refresh every {REFRESH_S} s", value=True, key='auto_refresh',
                             help="A run in the filter is still running"):

        @st.fragment(run_every=REFRESH_S)
        def _tick():
            # The fragment also runs with every full rerun; only a timer tick reloads the page.
            if time.time() - st.session_state.get('_last_full_run', 0) >= REFRESH_S - 1:
                clear_api_cache()
                st.rerun(scope="app")

        _tick()

if not api_key():
    st.warning("Enter your API key under Connection in the sidebar.", icon=":material/key:")
else:
    navigation.run()
