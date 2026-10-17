"""Unit tests for ui/runprofile.py on a hand-made run. Run from the repo root:
    python -m pytest ui/tests   (or: python ui/tests/test_runprofile.py)"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import runprofile as prof  # noqa: E402

T0 = 1_700_000_000


def tasks():
    # A and B overlap during [5, 10); C is a killed attempt of D's task.
    rows = [
        dict(task_name='P:A (s1)', process_name='WF:SUB:A', attempt=1, final_status='COMPLETED', exit_code=0,
             start_time=T0, realtime=10, percent_cpu=150, cpus_requested=2, memory_requested=1000,
             peak_rss=500, time_requested=100, cpu_hours=10 * 1.5 / 3600, co2e_mg=1000),
        dict(task_name='P:B (s1)', process_name='WF:B', attempt=1, final_status='COMPLETED', exit_code=0,
             start_time=T0 + 5, realtime=10, percent_cpu=100, cpus_requested=1, memory_requested=2000,
             peak_rss=1900, time_requested=10.5, cpu_hours=10 / 3600, co2e_mg=500),
        dict(task_name='P:D (s2)', process_name='WF:B', attempt=1, final_status='FAILED', exit_code=137,
             start_time=T0 + 20, realtime=5, percent_cpu=100, cpus_requested=1, memory_requested=1024,
             peak_rss=1024, time_requested=60, cpu_hours=5 / 3600, co2e_mg=100),
        dict(task_name='P:D (s2)', process_name='WF:B', attempt=2, final_status='COMPLETED', exit_code=0,
             start_time=T0 + 30, realtime=5, percent_cpu=100, cpus_requested=1, memory_requested=2048,
             peak_rss=1500, time_requested=60, cpu_hours=5 / 3600, co2e_mg=100),
    ]
    return prof.add_task_metrics(pd.DataFrame(rows))


def at(series, seconds, col):
    """Value of a step series just after T0 + seconds."""
    t = pd.to_datetime(T0 + seconds, unit='s')
    return series[series['time'] <= t].iloc[-1][col]


def test_task_metrics():
    df = tasks()
    a = df.iloc[0]
    assert a['process'] == 'A' and a['subworkflow'] == 'WF:SUB'
    assert a['cpu_eff'] == 0.75 and a['memory_eff'] == 0.5 and a['time_eff'] == 0.1
    assert (a['end'] - a['start']).total_seconds() == 10
    assert list(df['finished']) == [True, True, False, True]
    assert df['queue_wait_s'].isna().all()             # no submit_time in these rows


def test_not_started_tasks():
    rows = tasks()
    rows.loc[3, 'start_time'] = 0.0                     # the API's placeholder for "no start yet"
    df = prof.add_task_metrics(rows)
    assert pd.isna(df.loc[3, 'start']) and pd.isna(df.loc[3, 'queue_wait_s'])
    startup, _ = prof.task_span(df, T0 - 60)
    assert startup == 60                                 # not pulled back to 1970
    assert prof.concurrency(df)['time'].min() == pd.to_datetime(T0, unit='s')


def test_running_task_ends_now():
    rows = tasks()
    rows.loc[1, ['final_status', 'realtime']] = ['RUNNING', 0]
    now = pd.to_datetime(T0 + 12, unit='s')
    df = prof.add_task_metrics(rows, now=now)
    assert df.loc[1, 'end'] == now
    assert at(prof.concurrency(df), 11, 'running') == 1     # B still counted as running at T0 + 11


def test_queue_wait():
    rows = tasks().assign(submit_time=lambda d: d['start_time'] - [2, 0, 5, -1])
    df = prof.add_task_metrics(rows)
    assert list(df['queue_wait_s'].fillna(-99)) == [2, 0, 5, -99]   # a negative wait (clock skew) is dropped
    assert prof.process_table(df).set_index('process_name').loc['WF:B', 'queue_wait_median_s'] == 0


def test_concurrency():
    s = prof.concurrency(tasks())
    assert at(s, 6, 'running') == 2
    assert at(s, 6, 'cpus_allocated') == 3
    assert at(s, 6, 'cpus_used') == 2.5
    assert at(s, 10, 'running') == 1      # A ended at 10, before anything else starts
    assert at(s, 16, 'running') == 0
    assert at(s, 21, 'memory_allocated_gb') == 1.0
    assert (s[['running', 'cpus_allocated']] >= 0).all().all()


def test_process_table_finished_only():
    t = prof.process_table(tasks()).set_index('process_name')
    assert t.loc['WF:B', 'tasks'] == 2               # the FAILED attempt is excluded
    assert t.loc['WF:B', 'peak_rss_max_mb'] == 1900
    assert abs(t.loc['WF:B', 'co2e_g'] - 0.6) < 1e-9
    hours = prof.process_table(tasks())['cpu_hours'].tolist()
    assert hours == sorted(hours, reverse=True)


def test_rightsizing_flags():
    r = prof.rightsizing(tasks(), 'memory').set_index('task_name')
    assert r.loc['P:B (s1)', 'flag'] == 'near limit'          # 1900 / 2000 = 0.95
    assert r.loc['P:A (s1)', 'flag'] == 'ok'                  # 0.5
    assert 'FAILED' not in set(r['final_status'])
    t = prof.rightsizing(tasks(), 'time').set_index('task_name')
    assert t.loc['P:A (s1)', 'flag'] == 'over-provisioned'    # 10 / 100


def test_attempt_chains():
    logical = pd.DataFrame([
        dict(task_name='P:A (s1)', attempts=1, succeeded=True, killed_attempts=0),
        dict(task_name='P:D (s2)', attempts=2, succeeded=True, killed_attempts=1),
    ])
    c = prof.attempt_chains(tasks(), logical)
    assert list(c['task_name']) == ['P:D (s2)']
    assert c.iloc[0]['history'] == '#1: exit 137 @ 1024 MB → #2: exit 0 @ 2048 MB'


def test_task_span():
    startup, span = prof.task_span(tasks(), T0 - 60)
    assert startup == 60      # workflow began 60 s before the first task
    assert span == 35         # first start T0 to last end T0 + 30 + 5


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name)
