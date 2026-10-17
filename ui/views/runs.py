"""Runs overview: one row per run (swift-provenancedb's script_run_summary), with
the sidebar filters applied. Selecting a row opens Run detail."""

import pandas as pd
import plotly.express as px
import streamlit as st

from common import pipeline_label, run_label

COLUMNS = {
    'run_label': 'Run', 'pipeline': 'Pipeline', 'revision_id': 'Revision', 'commit': 'Commit',
    'profile': 'Profile', 'institute_id': 'Institute', 'data_size_tag': 'Size tag',
    'campaign': 'Campaign', 'state': 'State', 'start': 'Start', 'duration_h': 'Duration (h)',
    'logical_tasks': 'Tasks', 'retried': 'Retried', 'failed': 'Failed attempts',
    'not_succeeded': 'Not succeeded', 'cpu_hours_used': 'CPU-h used',
    'cpu_hours_allocated': 'CPU-h allocated', 'energy_wh': 'Energy (Wh)', 'co2e_g': 'CO2e (g)',
}


def render(runs: pd.DataFrame, run_detail_page):
    st.title("Runs")
    if runs.empty:
        st.info("No runs match the filters. Run a pipeline with gwrepo.config, or widen the filters.")
        return

    c = st.columns(5)
    c[0].metric("Runs", len(runs))
    c[1].metric("Running", int((runs['state'] == 'RUNNING').sum()))
    c[2].metric("Failed runs", int((runs['state'] == 'FAILED').sum()))
    c[3].metric("CPU-hours used", f"{runs['cpu_hours_used'].sum():.2f}")
    c[4].metric("Energy", f"{runs['energy_wh'].sum():.1f} Wh")

    st.caption("Select a row to open the run's detail page.")
    table = runs.assign(duration_h=runs['duration'] / 3600)[list(COLUMNS)].rename(columns=COLUMNS)
    event = st.dataframe(
        table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={
            'Start': st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
            'Duration (h)': st.column_config.NumberColumn(format="%.2f"),
            'CPU-h used': st.column_config.NumberColumn(format="%.3f"),
            'CPU-h allocated': st.column_config.NumberColumn(format="%.3f"),
            'Energy (Wh)': st.column_config.NumberColumn(format="%.2f"),
            'CO2e (g)': st.column_config.NumberColumn(format="%.2f"),
        },
    )
    if event.selection.rows:
        st.session_state['run'] = runs.iloc[event.selection.rows[0]]['id']
        st.switch_page(run_detail_page)

    timed = runs[runs['start'].notna()]
    if timed.empty:
        return
    left, right = st.columns(2)
    with left:
        fig = px.scatter(timed, x='start', y=timed['duration'] / 3600, color='state', symbol='pipeline',
                         hover_name='run_label', title="Run duration over time",
                         labels={'start': 'Start', 'y': 'Duration (h)', 'state': 'State', 'pipeline': 'Pipeline'})
        st.plotly_chart(fig, width="stretch")
    with right:
        fig = px.bar(timed.sort_values('start'), x='run_label', y='cpu_hours_used', color='institute_id',
                     title="CPU-hours used per run",
                     labels={'run_label': 'Run', 'cpu_hours_used': 'CPU-hours', 'institute_id': 'Institute'})
        fig.update_xaxes(showticklabels=len(timed) <= 30)
        st.plotly_chart(fig, width="stretch")

    trouble = timed[timed['logical_tasks'] > 0].assign(
        retried_pct=lambda d: 100 * d['retried'] / d['logical_tasks'],
        not_succeeded_pct=lambda d: 100 * d['not_succeeded'] / d['logical_tasks'])
    if not trouble.empty:
        long = trouble.melt(id_vars=['start', 'run_label'], value_vars=['retried_pct', 'not_succeeded_pct'],
                            var_name='kind', value_name='percent')
        long['kind'] = long['kind'].map({'retried_pct': 'retried', 'not_succeeded_pct': 'not succeeded'})
        fig = px.line(long.sort_values('start'), x='start', y='percent', color='kind', markers=True,
                      hover_name='run_label', title="Share of tasks retried or not succeeded, per run",
                      labels={'start': 'Start', 'percent': '% of tasks', 'kind': ''})
        st.plotly_chart(fig, width="stretch")


def prepare(summary: list) -> pd.DataFrame:
    """Summary rows from /workflows/summary -> DataFrame with display columns."""
    runs = pd.DataFrame(summary)
    if runs.empty:
        return runs
    runs['state'] = runs['final_state'].fillna('RUNNING')  # set at /trace/complete
    runs['start'] = pd.to_datetime(runs['start_time'], unit='s', errors='coerce')
    runs['pipeline'] = runs.apply(pipeline_label, axis=1)
    runs['run_label'] = runs.apply(run_label, axis=1)
    runs['commit'] = runs['commit_id'].map(lambda c: c[:12] if isinstance(c, str) else None)
    runs['energy_wh'] = runs['energy_mwh'] / 1000.0
    runs['co2e_g'] = runs['co2e_mg'] / 1000.0
    return runs
