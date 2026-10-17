"""Pure computations behind the provenance explorer (no Streamlit): critical path,
process-level aggregation of the task graph, and Graphviz DOT output.

`tasks` are rows of /workflows/{id}/task_profile passed through
runprofile.add_task_metrics; `edges` are rows of /workflows/{id}/graph
(producer_id, consumer_id, filename, sha256, matched_by, size_bytes,
producer_workflow_id, consumer_workflow_id)."""

from collections import defaultdict

import pandas as pd

# Node fill colours, low -> high (ColorBrewer YlOrRd, 5 classes).
PALETTE = ['#ffffb2', '#fecc5c', '#fd8d3c', '#f03b20', '#bd0026']

METRICS = {
    # name: (label, aggregation over a process's tasks, column, format)
    'cpu_hours': ('CPU-hours', 'sum', 'cpu_hours', '{:.3f} CPU-h'),
    'realtime': ('Total realtime', 'sum', 'realtime', '{:.0f} s'),
    'peak_rss': ('Max peak RSS', 'max', 'peak_rss', '{:.0f} MB'),
    'failed': ('Failed attempts', 'sum', 'failed_attempt', '{:.0f} failed'),
}


def internal_edges(edges: pd.DataFrame, workflow_id: str) -> pd.DataFrame:
    """Edges with both ends in the run."""
    if edges.empty:
        return edges
    return edges[(edges['producer_workflow_id'] == workflow_id) & (edges['consumer_workflow_id'] == workflow_id)]


def critical_path(tasks: pd.DataFrame, edges: pd.DataFrame) -> tuple:
    """Longest chain of dependent tasks, weighted by Nextflow's `duration` (submit
    to complete). A consumer is submitted only after its producers complete, so the
    chain's total is at most the run's wall time. Returns (task ids in order, total s)."""
    weight = dict(zip(tasks['id'], tasks['duration'].fillna(0)))
    preds, succs = defaultdict(set), defaultdict(set)
    for p, c in edges[['producer_id', 'consumer_id']].drop_duplicates().itertuples(index=False):
        if p in weight and c in weight and p != c:
            preds[c].add(p)
            succs[p].add(c)
    # Kahn's algorithm: a topological order; nodes left over would be on a cycle (ignored).
    indegree = {n: len(preds[n]) for n in weight}
    order = [n for n, d in indegree.items() if d == 0]
    best, back = {}, {}
    i = 0
    while i < len(order):
        n = order[i]
        i += 1
        prev = max(preds[n], key=lambda p: best.get(p, 0), default=None)
        best[n] = weight[n] + (best[prev] if prev is not None else 0)
        back[n] = prev
        for s in succs[n]:
            indegree[s] -= 1
            if indegree[s] == 0:
                order.append(s)
    if not best:
        return [], 0.0
    node = max(best, key=best.get)
    total = best[node]
    path = []
    while node is not None:
        path.append(node)
        node = back[node]
    return path[::-1], total


def process_graph(tasks: pd.DataFrame, edges: pd.DataFrame) -> tuple:
    """Process-level graph of a run: one node per process (task count and every
    METRICS value) and one edge per producer->consumer process pair (files, bytes)."""
    df = tasks.assign(failed_attempt=(~tasks['finished']).astype(int))
    grouped = df.groupby('process_name')
    nodes = pd.DataFrame({'process': grouped['process'].first(), 'tasks': grouped.size()})
    for name, (_, how, col, _) in METRICS.items():
        nodes[name] = grouped[col].agg(how)
    nodes = nodes.reset_index()
    if edges.empty:
        return nodes, pd.DataFrame(columns=['producer', 'consumer', 'files', 'bytes'])
    proc = dict(zip(tasks['id'], tasks['process_name']))
    e = edges.assign(producer=edges['producer_id'].map(proc), consumer=edges['consumer_id'].map(proc)).dropna(
        subset=['producer', 'consumer'])
    e = e[e['producer'] != e['consumer']]
    agg = e.groupby(['producer', 'consumer']).agg(files=('filename', 'size'),
                                                  bytes=('size_bytes', 'sum')).reset_index()
    return nodes, agg


def _colour(value, lo, hi) -> str:
    if pd.isna(value) or hi <= lo:
        return PALETTE[0]
    return PALETTE[min(int((value - lo) / (hi - lo) * len(PALETTE)), len(PALETTE) - 1)]


def _q(text) -> str:
    """A quoted DOT id or label; `\\n` in labels is left as a DOT line break."""
    return '"' + str(text).replace('"', '\\"') + '"'


def _size(n) -> str:
    if pd.isna(n):
        return ''
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f"{n:.0f} {unit}" if unit == 'B' else f"{n:.1f} {unit}"
        n /= 1024


def process_dot(nodes: pd.DataFrame, edges: pd.DataFrame, metric: str, highlight: set = frozenset(),
                highlight_edges: set = frozenset()) -> str:
    """DOT for the process graph: fill colour by `metric`, bold red outline and
    edges for `highlight` (processes) / `highlight_edges` ((producer, consumer))."""
    fmt = METRICS[metric][3]
    lo, hi = nodes[metric].min(), nodes[metric].max()
    lines = ['digraph G {', 'rankdir=TB; nodesep=0.25; ranksep=0.35;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
             'edge [color="#888888", fontname="Helvetica", fontsize=8];']
    for r in nodes.itertuples(index=False):
        value = getattr(r, metric)
        text = f"{r.process}\\n{r.tasks} task{'s' if r.tasks != 1 else ''} · " + (
            fmt.format(value) if pd.notna(value) else '–')
        extra = ', color="#d7191c", penwidth=3' if r.process_name in highlight else ''
        lines.append(f'{_q(r.process_name)} [label="{text}", fillcolor="{_colour(value, lo, hi)}", '
                     f'tooltip={_q(r.process_name)}{extra}];')
    for r in edges.itertuples(index=False):
        extra = ', color="#d7191c", penwidth=2.5' if (r.producer, r.consumer) in highlight_edges else ''
        lines.append(f'{_q(r.producer)} -> {_q(r.consumer)} [label="{r.files} file{"s" if r.files != 1 else ""}'
                     f'{" · " + _size(r.bytes) if r.bytes else ""}"{extra}];')
    lines.append('}')
    return '\n'.join(lines)


def task_dot(tasks: pd.DataFrame, edges: pd.DataFrame, focus: str, run_labels: dict) -> str:
    """DOT for a task-level subgraph: `tasks` (rows with id, task_name,
    workflow_execution_id, final_status) and the `edges` among them. The focus task
    is highlighted; tasks from other runs get the run in their label; edges matched
    by content (across runs) are dashed."""
    lines = ['digraph G {', 'rankdir=TB; nodesep=0.2; ranksep=0.3;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=9, fillcolor="#f0f0f0"];',
             'edge [color="#888888", fontname="Helvetica", fontsize=7];']
    focus_wf = tasks.loc[tasks['id'] == focus, 'workflow_execution_id']
    focus_wf = focus_wf.iloc[0] if not focus_wf.empty else None
    for r in tasks.itertuples(index=False):
        text = str(r.task_name)
        if r.workflow_execution_id != focus_wf:
            text += f"\\n[{run_labels.get(r.workflow_execution_id, r.workflow_execution_id[:8])}]"
        fill = '#fdae61' if r.id == focus else ('#f4a6a6' if r.final_status not in ('COMPLETED', 'CACHED')
                                               else '#f0f0f0')
        style = ', penwidth=2.5' if r.id == focus else ''
        lines.append(f'{_q(r.id)} [label={_q(text)}, fillcolor="{fill}"{style}];')
    pairs = edges.groupby(['producer_id', 'consumer_id', 'matched_by']).size().reset_index(name='files')
    for r in pairs.itertuples(index=False):
        attrs = []
        if r.files > 1:
            attrs.append(f'label="{r.files} files"')
        if r.matched_by == 'content':
            attrs.append('style=dashed, color="#2b83ba"')
        lines.append(f'{_q(r.producer_id)} -> {_q(r.consumer_id)} [{", ".join(attrs)}];')
    lines.append('}')
    return '\n'.join(lines)
