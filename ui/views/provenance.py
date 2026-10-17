"""Provenance explorer for one run (PLAN-frontend.md 3.3): process graph coloured
by cost with the critical path, task lineage subgraph, task inspector
(swift-about-execution), file inspector (swift-about-dataset), critical path."""

import pandas as pd
import streamlit as st

import provgraph as pg
import runprofile as prof
from common import api_get, fmt_duration, fmt_mb

MAX_SUBGRAPH_NODES = 200
MAX_FILE_LOOKUPS = 50


def render(run: pd.Series, all_runs: pd.DataFrame):
    st.title(f"Provenance · {run['run_label']}")
    rows = api_get(f"/workflows/{run['id']}/task_profile")
    if rows is None:
        return
    if not rows:
        st.info("No tasks recorded for this run yet.")
        return
    tasks = prof.add_task_metrics(pd.DataFrame(rows))
    edges = pd.DataFrame(api_get(f"/workflows/{run['id']}/graph") or [])
    if edges.empty:
        st.info("No lineage edges for this run: run lineage-import (submit_run.py) after the pipeline.")
    internal = pg.internal_edges(edges, run['id'])
    path, total = pg.critical_path(tasks, internal) if not internal.empty else ([], 0.0)
    run_labels = dict(zip(all_runs['id'], all_runs['run_label']))

    graph_tab, lineage_tab, task_tab, file_tab, path_tab = st.tabs(
        ["Process graph", "Task lineage", "Task inspector", "File inspector", "Critical path"])
    with graph_tab:
        _process_graph(tasks, internal, path)
    with path_tab:
        _critical_path(tasks, path, total, run)
    with file_tab:
        _file_inspector(run, run_labels)
    focus = _task_picker(tasks)
    with lineage_tab:
        _lineage(focus, tasks, run, run_labels)
    with task_tab:
        _inspector(focus, tasks, edges, run, run_labels)


def _task_picker(tasks):
    """Task shared by the lineage and inspector tabs, kept in the URL as ?task=."""
    ordered = tasks.sort_values('start_time')
    labels = dict(zip(ordered['id'], ordered['task_name'].fillna(ordered['process_name']) +
                      ordered['attempt'].map(lambda a: f" · attempt {a:.0f}" if pd.notna(a) and a > 1 else '')))
    options = list(labels)
    current = st.session_state.get('task') or st.query_params.get('task')
    with st.sidebar:
        choice = st.selectbox("Task (lineage and inspector)", options,
                              index=options.index(current) if current in options else len(options) - 1,
                              format_func=labels.get)
    st.session_state['task'] = choice
    st.query_params['task'] = choice
    return tasks[tasks['id'] == choice].iloc[0]


def _process_graph(tasks, internal, path):
    metric = st.radio("Colour by", list(pg.METRICS), horizontal=True,
                      format_func=lambda m: pg.METRICS[m][0])
    nodes, agg = pg.process_graph(tasks, internal)
    proc = dict(zip(tasks['id'], tasks['process_name']))
    on_path = [proc[t] for t in path]
    st.graphviz_chart(pg.process_dot(nodes, agg, metric, highlight=set(on_path),
                                     highlight_edges=set(zip(on_path, on_path[1:]))),
                      width="stretch")
    st.caption(f"{len(nodes)} processes, {len(agg)} producer → consumer links (labels: files · bytes read). "
               "Red outline: processes on the critical path.")


def _critical_path(tasks, path, total, run):
    if not path:
        st.info("No dependency graph to compute a critical path from.")
        return
    startup, span = prof.task_span(tasks, run['start_time'])
    c = st.columns(4)
    c[0].metric("Critical path", fmt_duration(total), help="Longest chain of dependent tasks, by Nextflow duration")
    c[1].metric("Share of task span", f"{100 * total / span:.0f}%" if span else '–',
                help="Task span: first task's start to last task's end")
    c[2].metric("Tasks on it", len(path))
    c[3].metric("Before first task", fmt_duration(startup),
                help="Workflow start to first task start: fetching the pipeline, staging remote inputs")
    on = tasks.set_index('id').loc[path].reset_index()
    st.dataframe(on.assign(share=100 * on['duration'] / total)[['task_name', 'process', 'duration', 'share', 'start']],
                 hide_index=True, width="stretch",
                 column_config={'task_name': 'Task', 'process': 'Process',
                                'duration': st.column_config.NumberColumn('Duration (s)', format="%.1f"),
                                'share': st.column_config.ProgressColumn('Share of path', format="%.0f%%",
                                                                         min_value=0, max_value=100),
                                'start': st.column_config.DatetimeColumn('Start', format="HH:mm:ss")})
    st.caption("Duration runs from submit to complete. A task is submitted only after its inputs exist, so a "
               "path well below the task span means the run was limited by resources (tasks waiting for free "
               "CPUs or memory) rather than by dependencies.")


def _lineage(focus, tasks, run, run_labels):
    direction = st.radio("Show", ["ancestors", "descendants", "both"], horizontal=True,
                         format_func=str.capitalize)
    related = []
    if direction in ("ancestors", "both"):
        related += api_get(f"/processes/{focus['id']}/ancestors") or []
    if direction in ("descendants", "both"):
        related += api_get(f"/processes/{focus['id']}/descendants") or []
    nodes = pd.DataFrame(related + [focus[['id', 'task_name', 'process_name', 'workflow_execution_id',
                                           'final_status']].to_dict()]).drop_duplicates('id')
    st.caption(f"{len(nodes) - 1} {direction if direction != 'both' else 'ancestors and descendants'} "
               f"of **{focus['task_name']}**")
    if len(nodes) == 1:
        st.info(f"This task has no {direction} in the lineage graph.")
        return
    edge_rows = []
    for wf in nodes['workflow_execution_id'].unique():
        edge_rows += api_get(f"/workflows/{wf}/graph") or []
    edges = pd.DataFrame(edge_rows).drop_duplicates(['producer_id', 'consumer_id', 'filename'])
    ids = set(nodes['id'])
    edges = edges[edges['producer_id'].isin(ids) & edges['consumer_id'].isin(ids)]
    if len(nodes) > MAX_SUBGRAPH_NODES:
        st.warning(f"{len(nodes)} tasks are too many to draw; showing direct neighbours only.")
        edges = edges[(edges['producer_id'] == focus['id']) | (edges['consumer_id'] == focus['id'])]
        nodes = nodes[nodes['id'].isin(set(edges['producer_id']) | set(edges['consumer_id']) | {focus['id']})]
    st.graphviz_chart(pg.task_dot(nodes, edges, focus['id'], run_labels), width="stretch")
    st.caption("Orange: the selected task. Dashed blue: matched by content across runs. Red fill: not finished.")
    other = nodes[nodes['workflow_execution_id'] != run['id']]
    if not other.empty:
        st.info(f"{len(other)} of these tasks belong to other runs: "
                + ", ".join(sorted({run_labels.get(w, w[:8]) for w in other['workflow_execution_id']})))


def _inspector(focus, tasks, edges, run, run_labels):
    st.subheader(focus['task_name'])
    meta = {'Process': focus['process_name'], 'Tag': focus.get('tag'), 'Status': focus['final_status'],
            'Exit code': focus['exit_code'], 'Attempt': focus.get('attempt'), 'Container': focus.get('container_name'),
            'Code checksum': focus.get('code_checksum'), 'Started': focus['start'],
            'Queue wait (s)': focus.get('queue_wait_s'), 'Executor': focus.get('executor'),
            'Native job id': focus.get('native_id'), 'CPU model': focus.get('cpu_model')}
    st.dataframe(pd.DataFrame({'Field': list(meta), 'Value': [str(v) if pd.notna(v) else '–' for v in meta.values()]}),
                 hide_index=True, width="stretch")

    st.markdown("**Resources**")
    res = pd.DataFrame([
        ('CPU', focus['cpus_requested'], focus['cores_used'], focus['cpu_eff'], 'cores'),
        ('Memory', focus['memory_requested'], focus['peak_rss'], focus['memory_eff'], 'MB'),
        ('Time', focus['time_requested'], focus['realtime'], focus['time_eff'], 's'),
    ], columns=['Resource', 'Requested', 'Used', 'Efficiency', 'Unit'])
    st.dataframe(res.assign(Efficiency=100 * res['Efficiency']), hide_index=True, width="stretch",
                 column_config={'Requested': st.column_config.NumberColumn(format="%.2f"),
                                'Used': st.column_config.NumberColumn(format="%.2f"),
                                'Efficiency': st.column_config.NumberColumn(format="%.0f%%")})

    attempts = pd.DataFrame(api_get(f"/processes/{focus['id']}/attempts") or [])
    if len(attempts) > 1:
        st.markdown("**Attempts**")
        st.dataframe(attempts[['attempt', 'final_status', 'exit_code', 'memory_requested', 'peak_rss', 'realtime']],
                     hide_index=True, width="stretch")

    params = pd.DataFrame(api_get(f"/workflows/{run['id']}/parameters") or [])
    if not params.empty:
        params = params[params['process_execution_id'] == focus['id']]
    st.markdown(f"**Parameters** ({len(params)})")
    if not params.empty:
        st.dataframe(params[['parameter_name', 'parameter_value']].sort_values('parameter_name'),
                     hide_index=True, width="stretch",
                     column_config={'parameter_name': 'Parameter', 'parameter_value': 'Value'})

    files = pd.DataFrame(api_get(f"/workflows/{run['id']}/files") or [])
    if files.empty:
        st.info("No file rows for this task: run lineage-import.")
        return
    files = files[files['process_execution_id'] == focus['id']]
    names = dict(zip(tasks['id'], tasks['task_name']))
    produced_by, used_by = {}, {}
    if not edges.empty:
        for e in edges[edges['consumer_id'] == focus['id']].itertuples():
            name = names.get(e.producer_id)
            if name is None:  # a task of another run
                other = api_get(f"/processes/{e.producer_id}") or {}
                name = other.get('task_name') or other.get('process_name') or e.producer_id
            where = '' if e.producer_workflow_id == run['id'] else f" [{run_labels.get(e.producer_workflow_id, '?')}]"
            produced_by[e.filename] = name + where
        out = edges[edges['producer_id'] == focus['id']]
        here = out[out['matched_by'] == 'path']['filename'].value_counts()            # same path, this run
        elsewhere = out[out['matched_by'] == 'content']['sha256'].value_counts()      # same content, other runs
        used_by = {'here': here.to_dict(), 'elsewhere': elsewhere.to_dict()}
    for direction, title in (('input', 'Inputs'), ('output', 'Outputs')):
        part = files[files['direction'] == direction].copy()
        st.markdown(f"**{title}** ({len(part)})")
        if part.empty:
            continue
        part['file'] = part['filename'].str.rsplit('/', n=1).str[-1]
        part['size'] = part['size_bytes'].map(lambda b: fmt_mb(b / 2**20) if pd.notna(b) else '–')
        part['sha'] = part['sha256'].str[:12]
        cols = ['file', 'size', 'sha']
        if direction == 'input':
            part['from'] = part['filename'].map(produced_by).fillna('outside the pipeline')
            cols.append('from')
        else:
            part['used here'] = part['filename'].map(used_by.get('here', {})).fillna(0).astype(int)
            part['used by other runs'] = part['sha256'].map(used_by.get('elsewhere', {})).fillna(0).astype(int)
            cols += ['used here', 'used by other runs']
        st.dataframe(part[cols + ['filename']], hide_index=True, width="stretch",
                     column_config={'file': 'File', 'size': 'Size', 'sha': 'SHA-256', 'from': 'Produced by',
                                    'filename': 'Path'})


def _file_inspector(run, run_labels):
    files = pd.DataFrame(api_get(f"/workflows/{run['id']}/files") or [])
    if files.empty:
        st.info("No file rows for this run: run lineage-import.")
        return
    query = st.text_input("Find a file of this run", placeholder="file name or SHA-256 prefix, e.g. multiqc_report")
    if not query:
        st.caption(f"{len(files)} file rows in this run. Search to see every task, in any run, that produced "
                   "or used the same content.")
        return
    hits = files[files['filename'].str.contains(query, case=False, regex=False)
                 | files['sha256'].fillna('').str.startswith(query.lower())]
    hits = hits.drop_duplicates('sha256')
    if hits.empty:
        st.info("No file of this run matches.")
        return
    labels = {r.sha256: f"{r.filename.rsplit('/', 1)[-1]} · {r.direction} · {r.sha256[:12] if r.sha256 else 'no hash'}"
              for r in hits.itertuples()}
    sha = st.selectbox(f"{len(hits)} distinct contents match", list(labels), format_func=labels.get)
    if not sha:
        st.warning("This file has no SHA-256 (it couldn't be read), so it can't be matched by content.")
        return
    st.code(sha)
    for kind, path in (("Produced by", "producers"), ("Used by", "consumers")):
        rows = pd.DataFrame(api_get(f"/files/{sha}/{path}") or [])
        st.markdown(f"**{kind}** ({len(rows)})")
        if rows.empty:
            continue
        if len(rows) > MAX_FILE_LOOKUPS:
            st.caption(f"Showing the first {MAX_FILE_LOOKUPS}.")
            rows = rows.head(MAX_FILE_LOOKUPS)
        details = [api_get(f"/processes/{pid}") or {} for pid in rows['process_execution_id']]
        rows['task'] = [d.get('task_name') or d.get('process_name') for d in details]
        rows['run'] = [run_labels.get(d.get('workflow_execution_id'), d.get('workflow_execution_id')) for d in details]
        st.dataframe(rows[['run', 'task', 'filename', 'size_bytes']], hide_index=True, width="stretch",
                     column_config={'run': 'Run', 'task': 'Task', 'filename': 'Path',
                                    'size_bytes': st.column_config.NumberColumn('Size (bytes)', format="%d")})
