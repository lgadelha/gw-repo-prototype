"""Process profile (PLAN-frontend.md 3.4): one process across runs. How its resource
use scales with input size or a sample metric, how it differs between versions,
requested vs used, and failures per run."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import api_get, fmt_mb, kpi, empty_state, run_colours, institute_colours

Y_METRICS = {'peak_rss': 'Peak RSS (MB)', 'realtime': 'Realtime (s)', 'cpu_hours': 'CPU-hours',
             'percent_cpu': '%CPU'}
COLOURS = {'run': 'Run', 'code_checksum_short': 'Code checksum', 'revision_id': 'Revision',
           'institute_id': 'Institute', 'data_size_tag': 'Size tag'}


def render(all_runs: pd.DataFrame):
    st.title("Process profile")
    names = pd.DataFrame(api_get("/process_names") or [])
    if names.empty:
        empty_state("No tasks recorded yet.")
        return
    names = names.sort_values(['runs', 'tasks'], ascending=False)
    labels = {r.process: f"{r.process} ({r.tasks} tasks, {r.runs} run{'s' if r.runs != 1 else ''})"
              for r in names.itertuples()}
    options = list(labels)
    current = st.session_state.get('process') or st.query_params.get('process')
    process = st.selectbox("Process", options, index=options.index(current) if current in options else 0,
                           format_func=labels.get)
    st.session_state['process'] = process
    st.query_params['process'] = process

    df = pd.DataFrame(api_get("/process_profile", process=process) or [])
    if df.empty:
        empty_state("No tasks for this process.")
        return
    run_labels = dict(zip(all_runs['id'], all_runs['run_label']))
    df['run'] = df['workflow_execution_id'].map(run_labels).fillna(df['workflow_execution_id'].str[:8])
    st.session_state['pp_colours'] = {'run': run_colours(run_labels), 'institute_id': institute_colours()}
    df['finished'] = df['final_status'].isin(('COMPLETED', 'CACHED'))
    df['input_mb'] = df['input_bytes'] / 2**20
    df['code_checksum_short'] = df['code_checksum'].str[:8].fillna('unknown')
    done = df[df['finished']]

    c = st.columns(4)
    kpi(c[0], "Runs", df['workflow_execution_id'].nunique())
    kpi(c[1], "Finished tasks", len(done))
    kpi(c[2], "Attempts not finished", f"{(~df['finished']).sum()} of {len(df)}")
    kpi(c[3], "Median peak RSS", fmt_mb(done['peak_rss'].median()))
    if df['process_name'].nunique() > 1:
        st.caption("Includes " + ", ".join(sorted(df['process_name'].unique())))

    scaling, versions, sizing, failures = st.tabs(["Scaling", "Versions", "Requested vs used", "Failures"])
    with scaling:
        _scaling(done)
    with versions:
        _versions(done)
    with sizing:
        _requested_vs_used(done)
    with failures:
        _failures(df)


def _sample_metrics(df):
    """Sample metrics of the runs in df, keyed by (workflow, sample)."""
    rows = []
    for wf in df['workflow_execution_id'].unique():
        for m in api_get(f"/workflows/{wf}/sample_metrics/") or []:
            if m.get('value_num') is not None:
                rows.append(m)
    return pd.DataFrame(rows)


def _scaling(done):
    if done.empty:
        empty_state("No finished tasks.")
        return
    metrics = _sample_metrics(done)
    x_options = ['input_mb'] + (sorted(metrics['key'].unique()) if not metrics.empty else [])
    c = st.columns([2, 2, 2, 1, 1])
    y = c[0].selectbox("Resource", list(Y_METRICS), format_func=Y_METRICS.get, key='pp_scaling_y')
    x = c[1].selectbox("Against", x_options,
                       format_func=lambda k: "Input size (MB)" if k == 'input_mb' else f"Sample metric: {k}",
                       key='pp_against')
    colour = c[2].selectbox("Colour by", list(COLOURS), format_func=COLOURS.get, key='pp_colour')
    log_x, log_y = c[3].checkbox("Log x"), c[4].checkbox("Log y")
    data = done
    if x != 'input_mb':
        values = metrics[metrics['key'] == x][['workflow_execution_id', 'sample', 'value_num']]
        data = done.merge(values, left_on=['workflow_execution_id', 'tag'],
                          right_on=['workflow_execution_id', 'sample']).rename(columns={'value_num': x})
        st.caption("Joined on task tag = sample (nf-core's meta.id).")
    data = data[data[x].notna() & data[y].notna()]
    if data.empty:
        empty_state("No tasks have both values: input sizes come from lineage-import, sample metrics from multiqc-import.")
        return
    fit = len(data) > 2 and data[x].nunique() > 1
    fig = px.scatter(data, x=x, y=y, color=colour, hover_name='task_name', log_x=log_x, log_y=log_y,
                     trendline='ols' if fit else None, trendline_scope='overall',
                     color_discrete_map=st.session_state['pp_colours'].get(colour, {}),
                     labels={'input_mb': 'Input size (MB)', **Y_METRICS, **COLOURS})
    st.plotly_chart(fig, width="stretch")
    if fit:
        r2 = px.get_trendline_results(fig).iloc[0]['px_fit_results'].rsquared
        st.caption(f"{len(data)} tasks. Linear fit over all of them: R² = {r2:.2f}.")


def _versions(done):
    if done['code_checksum_short'].nunique() < 2 and done['revision_id'].nunique() < 2:
        empty_state("All these tasks ran the same process code; versions appear when runs used different revisions.")
    y = st.selectbox("Resource", list(Y_METRICS), format_func=Y_METRICS.get, key='pp_versions_y')
    fig = px.box(done, x='code_checksum_short', y=y, color='revision_id', points='all', hover_name='task_name',
                 labels={'code_checksum_short': 'Code checksum', 'revision_id': 'Revision', **Y_METRICS})
    st.plotly_chart(fig, width="stretch")
    summary = done.groupby(['code_checksum_short', 'revision_id'], dropna=False).agg(
        runs=('workflow_execution_id', 'nunique'), tasks=('id', 'size'),
        median=(y, 'median'), max=(y, 'max')).reset_index()
    st.dataframe(summary, hide_index=True, width="stretch",
                 column_config={'code_checksum_short': 'Code checksum', 'revision_id': 'Revision',
                                'median': st.column_config.NumberColumn(f'Median {Y_METRICS[y]}', format="%.2f"),
                                'max': st.column_config.NumberColumn(f'Max {Y_METRICS[y]}', format="%.2f")})


def _requested_vs_used(done):
    data = done[(done['memory_requested'] > 0) & (done['peak_rss'] > 0)]
    if data.empty:
        empty_state("No finished tasks with a memory request.")
        return
    fig = px.scatter(data, x='memory_requested', y='peak_rss', color='run', log_x=True, log_y=True,
                     color_discrete_map=st.session_state['pp_colours']['run'],
                     hover_name='task_name',
                     labels={'memory_requested': 'Memory requested (MB)', 'peak_rss': 'Peak RSS (MB)', 'run': 'Run'})
    lo = min(data['memory_requested'].min(), data['peak_rss'].min())
    hi = max(data['memory_requested'].max(), data['peak_rss'].max())
    fig.add_trace(go.Scatter(x=[lo, hi], y=[lo, hi], mode='lines', name='used = requested',
                             line=dict(dash='dash', color='grey')))
    st.plotly_chart(fig, width="stretch")
    eff = (data['peak_rss'] / data['memory_requested']).median()
    st.caption(f"Median memory efficiency: {eff:.0%}. Largest peak: {fmt_mb(data['peak_rss'].max())}, "
               f"smallest request: {fmt_mb(data['memory_requested'].min())}.")


def _failures(df):
    per_run = df.groupby('run').agg(
        attempts=('id', 'size'), finished=('finished', 'sum'),
        killed=('exit_code', lambda e: int(e.isin([137, 140]).sum())),
        retried_tasks=('attempt', lambda a: int((a > 1).sum()))).reset_index()
    per_run['not_finished'] = per_run['attempts'] - per_run['finished']
    if per_run['not_finished'].sum() == 0:
        st.success("Every attempt of this process finished, in every run.")
    fig = px.bar(per_run, x='run', y=['finished', 'not_finished'], title="Attempts per run",
                 labels={'run': 'Run', 'value': 'Attempts', 'variable': ''})
    st.plotly_chart(fig, width="stretch")
    st.dataframe(per_run, hide_index=True, width="stretch",
                 column_config={'run': 'Run', 'attempts': 'Attempts', 'finished': 'Finished',
                                'killed': 'Killed (137/140)', 'retried_tasks': 'Retries',
                                'not_finished': 'Not finished'})
