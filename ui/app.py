"""GW-RePO Streamlit UI: navigation, sidebar (API key, run filters and selector,
refresh) and page wiring. Pages are ui/nav/*.py; their rendering code is in
ui/views/, pure computations in ui/runprofile.py. See PLAN-frontend.md."""

import os

import pandas as pd
import streamlit as st

from common import api_get, api_key
from views import runs

st.set_page_config(page_title="GW-RePO (Genomic Workflow Resource and Parameter Optimization)", layout="wide")

if 'api_key' not in st.session_state:
    st.session_state['api_key'] = os.getenv("API_KEY", "")
if 'run' not in st.session_state:
    st.session_state['run'] = st.query_params.get('run')


# Each page is a small script in ui/nav/ (file pages, so AppTest can switch to them);
# they read the run list that the sidebar below puts in session state.
navigation = st.navigation({
    "Observability": [st.Page("nav/runs.py", title="Runs", icon="📋", url_path="runs", default=True),
                      st.Page("nav/run_detail.py", title="Run detail", icon="🔍", url_path="run"),
                      st.Page("nav/provenance.py", title="Provenance", icon="🧬", url_path="provenance"),
                      st.Page("nav/samples.py", title="Samples", icon="🧪", url_path="samples")],
    "Across runs": [st.Page("nav/process_profile.py", title="Process profile", icon="⚙️", url_path="process"),
                    st.Page("nav/compare.py", title="Compare runs", icon="⚖️", url_path="compare"),
                    st.Page("nav/analytics.py", title="Process analytics", icon="📈", url_path="analytics")],
    "Resource optimization": [st.Page("nav/optimization.py", title="Bayesian modelling", icon="🤖",
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
    st.text_input("API Key", type="password", key="api_key")
    all_runs = pd.DataFrame()
    filtered_runs = all_runs
    if api_key():
        all_runs = runs.prepare(api_get("/workflows/summary") or [])
    if not all_runs.empty:
        st.subheader("Runs")
        filtered_runs = run_filters(all_runs)
        options = filtered_runs['id'].tolist()
        current = st.session_state.get('run')
        if current in all_runs['id'].values and current not in options:
            options = [current] + options  # keep a linked or selected run visible whatever the filters
        labels = dict(zip(all_runs['id'], all_runs['run_label']))
        choice = st.selectbox("Run", options, index=options.index(current) if current in options else 0,
                              format_func=labels.get) if options else None
        st.session_state['run'] = choice
        if choice:
            st.query_params['run'] = choice
    st.session_state['all_runs'] = all_runs
    st.session_state['filtered_runs'] = filtered_runs
    st.divider()
    if st.button("🔄 Refresh data", help="Data is cached for 30 s; this fetches it now"):
        st.cache_data.clear()
        st.rerun()

if not api_key():
    st.warning("Please enter your API key in the sidebar to authenticate.")
else:
    navigation.run()
