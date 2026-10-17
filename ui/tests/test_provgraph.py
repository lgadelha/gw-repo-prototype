"""Unit tests for ui/provgraph.py. Run from the repo root:
    python -m pytest ui/tests   (or: python ui/tests/test_provgraph.py)"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import provgraph as pg  # noqa: E402
import runprofile as prof  # noqa: E402

WF = 'wf1'


def tasks():
    # Diamond A -> {B, C} -> D, plus an unconnected E. Longest by duration: A, C, D.
    rows = [dict(id=i, task_name=f'{p} (x)', process_name=f'WF:{p}', workflow_execution_id=WF,
                 final_status='COMPLETED', start_time=0, realtime=d, duration=d, percent_cpu=100,
                 cpus_requested=1, memory_requested=100, peak_rss=rss, time_requested=10, cpu_hours=d / 3600)
            for i, p, d, rss in [('a', 'A', 2, 10), ('b', 'B', 1, 20), ('c', 'C', 5, 30), ('d', 'D', 3, 40),
                                 ('e', 'E', 100, 50)]]
    rows.append(dict(rows[1], id='b0', final_status='FAILED'))  # a failed attempt of B, no edges
    return prof.add_task_metrics(pd.DataFrame(rows))


def edges():
    rows = [('a', 'b', 'f1'), ('a', 'c', 'f2'), ('b', 'd', 'f3'), ('c', 'd', 'f4'), ('c', 'd', 'f5')]
    return pd.DataFrame([dict(producer_id=p, consumer_id=c, filename=f, sha256=f, matched_by='path',
                              size_bytes=1024, producer_workflow_id=WF, consumer_workflow_id=WF)
                         for p, c, f in rows])


def test_critical_path():
    path, total = pg.critical_path(tasks(), edges())
    # E alone (100 s) is longer than A-C-D (10 s): the longest chain may be a single task.
    assert path == ['e'] and total == 100
    path, total = pg.critical_path(tasks()[tasks()['id'] != 'e'], edges())
    assert path == ['a', 'c', 'd'] and total == 10


def test_critical_path_ignores_cycles():
    e = pd.concat([edges(), edges().iloc[[0]].assign(producer_id='b', consumer_id='a')])
    path, _ = pg.critical_path(tasks(), e)  # a and b are on a cycle: skipped, no crash
    assert 'a' not in path and 'b' not in path


def test_process_graph():
    nodes, agg = pg.process_graph(tasks(), edges())
    n = nodes.set_index('process_name')
    assert n.loc['WF:B', 'tasks'] == 2 and n.loc['WF:B', 'failed'] == 1
    assert n.loc['WF:D', 'peak_rss'] == 40
    e = agg.set_index(['producer', 'consumer'])
    assert e.loc[('WF:C', 'WF:D'), 'files'] == 2 and e.loc[('WF:C', 'WF:D'), 'bytes'] == 2048


def test_dot_outputs():
    nodes, agg = pg.process_graph(tasks(), edges())
    dot = pg.process_dot(nodes, agg, 'cpu_hours', highlight={'WF:A'}, highlight_edges={('WF:A', 'WF:C')})
    assert dot.startswith('digraph G {') and dot.endswith('}')
    assert dot.count(' -> ') == 4 and dot.count('penwidth=3') == 1 and dot.count('penwidth=2.5') == 1
    t = tasks()
    t.loc[t['id'] == 'b', 'workflow_execution_id'] = 'wf0'
    e = edges().assign(matched_by=lambda d: d['producer_id'].map({'b': 'content'}).fillna('path'))
    tdot = pg.task_dot(t, e, focus='d', run_labels={'wf0': 'earlier run'})
    assert 'earlier run' in tdot and 'style=dashed' in tdot and tdot.count(' -> ') == 4
    assert '"c" -> "d" [label="2 files"]' in tdot


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name)
