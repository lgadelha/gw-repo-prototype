"""Unit tests for ui/comparison.py. Run from the repo root:
    python -m pytest ui/tests   (or: python ui/tests/test_comparison.py)"""

import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import comparison as cmp  # noqa: E402
import runprofile as prof  # noqa: E402


def ann(**kv):
    return pd.DataFrame([dict(key=k, value_num=v if isinstance(v, (int, float)) else None,
                              value_text=None if isinstance(v, (int, float)) else v) for k, v in kv.items()])


def run_tasks(rss_a, checksum_b, extra=False):
    rows = [dict(id='a', process_name='WF:A', task_name='A', tag='s1', final_status='COMPLETED', start_time=0,
                 realtime=10, percent_cpu=100, cpus_requested=1, memory_requested=100, peak_rss=rss_a,
                 time_requested=60, cpu_hours=10 / 3600, co2e_mg=1000, code_checksum='c1'),
            dict(id='b', process_name='WF:B', task_name='B', tag='s1', final_status='COMPLETED', start_time=0,
                 realtime=20, percent_cpu=50, cpus_requested=1, memory_requested=100, peak_rss=50,
                 time_requested=60, cpu_hours=10 / 3600, co2e_mg=500, code_checksum=checksum_b),
            dict(id='b0', process_name='WF:B', task_name='B', tag='s2', final_status='FAILED', start_time=0,
                 realtime=5, percent_cpu=50, cpus_requested=1, memory_requested=100, peak_rss=999,
                 time_requested=60, cpu_hours=1, co2e_mg=1, code_checksum=checksum_b)]
    if extra:
        rows.append(dict(rows[0], id='c', process_name='WF:C', code_checksum='c9'))
    return prof.add_task_metrics(pd.DataFrame(rows))


def test_param_diff():
    d = cmp.param_diff({'r1': ann(**{'params.aligner': 'star', 'params.max': 2.0, 'campaign': 'x'}),
                        'r2': ann(**{'params.aligner': 'salmon', 'params.max': 2.0})})
    assert list(d.index) == ['campaign', 'params.aligner']          # params.max equal: not shown
    assert d.loc['params.aligner', 'r2'] == 'salmon' and pd.isna(d.loc['campaign', 'r2'])
    assert cmp._annotation_values(ann(n=2.0))['n'] == '2'


def test_checksum_diff():
    d = cmp.checksum_diff({'r1': run_tasks(10, 'b1'), 'r2': run_tasks(10, 'b2', extra=True)})
    assert list(d.index) == ['WF:B', 'WF:C']
    assert d.loc['WF:B', 'r1'] == 'b1' and pd.isna(d.loc['WF:C', 'r1'])


def test_resource_change():
    t = cmp.resource_change({'r1': run_tasks(100, 'b'), 'r2': run_tasks(400, 'b')}, 'r1', 'peak_rss')
    assert t.index[0] == 'WF:A' and t.loc['WF:A', 'ratio:r2'] == 4
    assert t.loc['WF:B', 'r1'] == 50                     # the FAILED attempt (999 MB) is ignored
    assert math.isclose(t.loc['WF:B', 'ratio:r2'], 1)


def test_samples():
    m = pd.DataFrame([dict(sample='s1', key='reads', value_num=100.0, value_text=None),
                      dict(sample='s1', key='lib', value_num=None, value_text='ISR'),
                      dict(sample='s2', key='reads', value_num=50.0, value_text=None)])
    w = cmp.sample_metrics_wide(m)
    assert w.loc['s1', 'reads'] == 100 and w.loc['s1', 'lib'] == 'ISR' and pd.isna(w.loc['s2', 'lib'])
    r = cmp.per_sample_resources(run_tasks(10, 'b'))
    assert list(r.index) == ['s1'] and r.loc['s1', 'tasks'] == 2      # s2's only task failed
    assert math.isclose(r.loc['s1', 'co2e_g'], 1.5)


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name)
