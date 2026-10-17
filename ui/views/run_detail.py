"""Run detail: the profile of one run (BioWorkbench's Profiler and Execution
overview). Sections of PLAN-frontend.md 3.2: KPIs, timeline, concurrency,
per-process table, right-sizing, retries, parameters and versions."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import runprofile as prof
from common import api_get, fmt_duration, fmt_mb

MAX_LABELLED_TASKS = 60


def render(run: pd.Series):
    st.title(f"Run {run['run_label']}")
    st.caption(f"Workflow ID `{run['id']}` · pipeline {run['pipeline']}")

    rows = api_get(f"/workflows/{run['id']}/task_profile")
    if rows is None:
        return
    if not rows:
        st.info("No tasks recorded for this run yet.")
        return
    tasks = prof.add_task_metrics(pd.DataFrame(rows))
    logical = pd.DataFrame(api_get(f"/workflows/{run['id']}/logical_tasks") or [])

    _kpis(run, tasks)
    timeline, processes, sizing, retries, params = st.tabs(
        ["Timeline", "Processes", "Right-sizing", "Retries", "Parameters & versions"])
    with timeline:
        _timeline(tasks)
        _concurrency(tasks)
    with processes:
        _process_table(tasks)
    with sizing:
        _rightsizing(tasks)
    with retries:
        _retries(tasks, logical)
    with params:
        _params_and_versions(run)


def _kpis(run, tasks):
    c = st.columns(4)
    c[0].metric("Wall time", fmt_duration(run['duration']), help="From the workflow's begin to complete")
    c[0].caption(f"State: **{run['state']}**")
    logical_n = int(run['logical_tasks']) if pd.notna(run['logical_tasks']) else len(tasks)
    c[1].metric("Tasks", logical_n, help="Logical tasks; retries of a task count once")
    c[1].caption(f"{int(run['finished'])} finished · {int(run['failed'])} failed attempts · "
                 f"{int(run['cached'])} cached · {int(run['retried'] or 0)} retried")
    used, allocated = run['cpu_hours_used'], run['cpu_hours_allocated']
    c[2].metric("CPU-hours used", f"{used:.3f}" if pd.notna(used) else '–',
                help="Σ realtime × %CPU / 100 over every attempt")
    if pd.notna(used) and allocated:
        c[2].caption(f"{100 * used / allocated:.0f}% of {allocated:.3f} allocated (realtime × CPUs requested)")
    c[3].metric("Peak task memory", fmt_mb(tasks['peak_rss'].max()))
    c = st.columns(4)
    c[0].metric("Data read", fmt_mb(tasks['read_bytes'].sum() / 2**20), help="Σ read_bytes (storage reads)")
    c[1].metric("Data written", fmt_mb(tasks['write_bytes'].sum() / 2**20), help="Σ write_bytes (storage writes)")
    c[2].metric("Energy", f"{run['energy_wh']:.2f} Wh" if pd.notna(run['energy_wh']) else '–',
                help="From nf-co2footprint (co2-import)")
    c[3].metric("CO2e", f"{run['co2e_g']:.2f} g" if pd.notna(run['co2e_g']) else '–')


def _timeline(tasks):
    st.subheader("Timeline")
    include_cached = st.checkbox(
        "Include cached tasks", value=False,
        help="Cached tasks keep the start time of the launch that ran them, which stretches the axis")
    df = tasks if include_cached else tasks[tasks['final_status'] != 'CACHED']
    df = df[df['start'].notna()].sort_values('start')
    instant = df['end'] <= df['start']  # Nextflow reports realtime 0 ms for sub-second commands
    df = df.assign(bar_end=df['end'].where(~instant, df['start'] + pd.Timedelta(seconds=1)))
    if df.empty:
        st.info("No tasks with a start time and runtime to show.")
        return
    color = st.radio("Colour by", ["process", "final_status"], horizontal=True,
                     format_func=lambda c: {"process": "Process", "final_status": "Status"}[c])
    fig = px.timeline(
        df, x_start='start', x_end='bar_end', y='task_name', color=color,
        pattern_shape=df['finished'].map({True: 'finished', False: 'not finished'}),
        hover_data={'task_name': True, 'attempt': True, 'exit_code': True, 'cpus_requested': True,
                    'cores_used': ':.2f', 'memory_requested': ':.0f', 'peak_rss': ':.0f', 'realtime': ':.1f'},
        labels={'task_name': 'Task', 'process': 'Process', 'final_status': 'Status',
                'pattern_shape': 'Attempt'},
    )
    fig.update_yaxes(categoryorder='array', categoryarray=df['task_name'].tolist()[::-1],
                     showticklabels=len(df) <= MAX_LABELLED_TASKS, title=None)
    fig.update_layout(height=max(400, min(6 * len(df), 1600)), showlegend=len(df[color].unique()) <= 40)
    st.plotly_chart(fig, width="stretch")
    st.caption("Bars run from start to start + realtime. Each attempt of a retried task is its own bar."
               + (f" {int(instant.sum())} tasks that Nextflow reports with 0 ms realtime are drawn 1 s wide."
                  if instant.any() else ""))


def _concurrency(tasks):
    series = prof.concurrency(tasks[tasks['final_status'] != 'CACHED'])
    if series.empty:
        return
    st.subheader("Concurrency")
    left, right = st.columns(2)
    with left:
        fig = go.Figure()
        for col, name in [('running', 'Running tasks'), ('cpus_allocated', 'CPUs allocated'),
                          ('cpus_used', 'CPUs used')]:
            fig.add_trace(go.Scatter(x=series['time'], y=series[col], name=name, line_shape='hv'))
        fig.update_layout(title="Tasks and CPUs over time", yaxis_title="Count / cores")
        st.plotly_chart(fig, width="stretch")
    with right:
        fig = go.Figure()
        for col, name in [('memory_allocated_gb', 'Memory allocated'), ('memory_peak_gb', 'Σ peak memory')]:
            fig.add_trace(go.Scatter(x=series['time'], y=series[col], name=name, line_shape='hv'))
        fig.update_layout(title="Memory over time", yaxis_title="GB")
        st.plotly_chart(fig, width="stretch")
    st.caption("CPUs used spreads each task's average %CPU over its runtime. Σ peak memory adds the running "
               "tasks' peaks, so it is an upper bound.")


def _process_table(tasks):
    table = prof.process_table(tasks)
    if table.empty:
        st.info("No finished tasks yet.")
        return
    st.subheader("Per process")
    st.caption("Finished attempts only. Efficiency = used / requested (median over tasks). "
               "Ordered by CPU-hours.")
    pct = dict(format="%.0f%%", min_value=0, max_value=100)
    st.dataframe(
        table.assign(cpu_eff_median=100 * table['cpu_eff_median'],
                     memory_eff_median=100 * table['memory_eff_median'],
                     time_eff_median=100 * table['time_eff_median']),
        hide_index=True, width="stretch",
        column_config={
            'process_name': None, 'process': 'Process', 'subworkflow': 'Subworkflow', 'tasks': 'Tasks',
            'realtime_median_s': st.column_config.NumberColumn('Realtime median (s)', format="%.1f"),
            'realtime_max_s': st.column_config.NumberColumn('Realtime max (s)', format="%.1f"),
            'cpu_percent_median': st.column_config.NumberColumn('%CPU median', format="%.0f"),
            'cpu_eff_median': st.column_config.ProgressColumn('CPU efficiency', **pct),
            'peak_rss_median_mb': st.column_config.NumberColumn('Peak RSS median (MB)', format="%.0f"),
            'peak_rss_max_mb': st.column_config.NumberColumn('Peak RSS max (MB)', format="%.0f"),
            'memory_eff_median': st.column_config.ProgressColumn('Memory efficiency', **pct),
            'time_eff_median': st.column_config.ProgressColumn('Time efficiency', **pct),
            'input_mb': st.column_config.NumberColumn('Input (MB)', format="%.1f"),
            'output_mb': st.column_config.NumberColumn('Output (MB)', format="%.1f"),
            'cpu_hours': st.column_config.NumberColumn('CPU-hours', format="%.4f"),
            'co2e_g': st.column_config.NumberColumn('CO2e (g)', format="%.3f"),
        },
    )


def _rightsizing(tasks):
    st.subheader("Requested vs used")
    resource = st.radio("Resource", list(prof.RESOURCES), horizontal=True,
                        format_func=lambda r: {"memory": "Memory", "cpu": "CPU", "time": "Time"}[r])
    df = prof.rightsizing(tasks, resource)
    unit = prof.RESOURCES[resource][2]
    if df.empty:
        st.info(f"No finished tasks with a {resource} request.")
        return
    fig = px.scatter(df, x='requested', y='used', color='process', symbol='flag', log_x=True, log_y=True,
                     hover_name='task_name', hover_data={'efficiency': ':.0%'},
                     labels={'requested': f'Requested ({unit})', 'used': f'Used ({unit})', 'process': 'Process'})
    positive = pd.concat([df['requested'], df['used']])
    positive = positive[positive > 0]
    if not positive.empty:
        lo, hi = positive.min(), positive.max()
        fig.add_trace(go.Scatter(x=[lo, hi], y=[lo, hi], mode='lines', name='used = requested',
                                 line=dict(dash='dash', color='grey')))
    fig.update_layout(height=550, showlegend=df['process'].nunique() <= 40)
    st.plotly_chart(fig, width="stretch")
    counts = df['flag'].value_counts()
    st.caption(f"{counts.get('over-provisioned', 0)} tasks use under {prof.OVER_PROVISIONED:.0%} of their "
               f"request, {counts.get('near limit', 0)} over {prof.NEAR_LIMIT:.0%}.")
    flagged = df[df['flag'] != 'ok'].sort_values('efficiency')
    if not flagged.empty:
        st.dataframe(flagged[['task_name', 'flag', 'requested', 'used', 'efficiency']].assign(
                         efficiency=100 * flagged['efficiency']),
                     hide_index=True, width="stretch",
                     column_config={'task_name': 'Task', 'flag': 'Flag',
                                    'requested': st.column_config.NumberColumn(f'Requested ({unit})', format="%.1f"),
                                    'used': st.column_config.NumberColumn(f'Used ({unit})', format="%.1f"),
                                    'efficiency': st.column_config.NumberColumn('Efficiency', format="%.0f%%")})


def _retries(tasks, logical):
    chains = prof.attempt_chains(tasks, logical)
    if chains.empty:
        st.success("No task was retried, and every task succeeded.")
        return
    st.subheader("Retried or unsuccessful tasks")
    st.caption("Exit 137/140: killed for exceeding memory/time, so the need was above what that attempt requested.")
    st.dataframe(chains[['task_name', 'attempts', 'succeeded', 'killed_attempts', 'history']],
                 hide_index=True, width="stretch",
                 column_config={'task_name': 'Task', 'attempts': 'Attempts', 'succeeded': 'Succeeded',
                                'killed_attempts': 'Killed (137/140)', 'history': 'Attempts (exit @ memory)'})


def _params_and_versions(run):
    st.subheader("Versions")
    versions = {
        'Repository': run.get('repository'), 'Revision': run.get('revision_id'),
        'Commit': run.get('commit_id'), 'Profile': run.get('profile'),
        'Nextflow': run.get('nextflow_version'), 'Main script hash': run.get('script_id'),
        'Config SHA-256': run.get('config_sha256'),
    }
    st.dataframe(pd.DataFrame({'Field': list(versions), 'Value': [v if isinstance(v, str) else '–' for v in versions.values()]}),
                 hide_index=True, width="stretch")

    annotations = pd.DataFrame(api_get(f"/workflows/{run['id']}/annotations/") or [])
    if annotations.empty:
        st.info("No params or annotations recorded for this run.")
        return
    annotations['value'] = annotations['value_text'].where(annotations['value_text'].notna(),
                                                          annotations['value_num'].astype(str))
    params = annotations[annotations['key'].str.startswith('params.')]
    other = annotations[~annotations['key'].str.startswith('params.')]
    if not other.empty:
        st.subheader("Annotations")
        st.dataframe(other[['key', 'value']], hide_index=True, width="stretch")
    st.subheader("Pipeline parameters")
    genomes = params['key'].str.startswith('params.genomes.')
    show_genomes = st.toggle(f"Show the genomes catalog ({int(genomes.sum())} entries)", value=False)
    shown = params if show_genomes else params[~genomes]
    search = st.text_input("Filter parameters", placeholder="e.g. aligner")
    if search:
        shown = shown[shown['key'].str.contains(search, case=False, regex=False)]
    st.dataframe(shown.assign(key=shown['key'].str.removeprefix('params.'))[['key', 'value']]
                 .rename(columns={'key': 'Parameter', 'value': 'Value'}),
                 hide_index=True, width="stretch", height=400)
