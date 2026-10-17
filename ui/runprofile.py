"""Pure computations behind the run profile views (no Streamlit): per-task derived
metrics, concurrency over time, the per-process table, right-sizing and retries.

Units follow the API: realtime and time_requested in seconds, peak_rss and
memory_requested in MB, percent_cpu raw (110 = 1.1 cores), start_time in epoch
seconds, energy in mWh, CO2e in mg."""

import numpy as np
import pandas as pd

FINISHED_STATUSES = ('COMPLETED', 'CACHED')

# Efficiency thresholds for the right-sizing flags.
OVER_PROVISIONED = 0.25
NEAR_LIMIT = 0.90

RESOURCES = {
    # name: (requested column, used column, unit)
    'memory': ('memory_requested', 'peak_rss', 'MB'),
    'cpu': ('cpus_requested', 'cores_used', 'cores'),
    'time': ('time_requested', 'realtime', 's'),
}


def _ratio(used: pd.Series, requested: pd.Series) -> pd.Series:
    return used / requested.where(requested > 0)


def add_task_metrics(tasks: pd.DataFrame) -> pd.DataFrame:
    """Derived per-task columns: start/end datetimes (end = start + realtime),
    cores used, CPU/memory/time efficiency (used / requested), process short name
    and subworkflow, input/output MB, CO2e in g."""
    df = tasks.copy()
    df['start'] = pd.to_datetime(df['start_time'], unit='s', errors='coerce')
    df['end'] = df['start'] + pd.to_timedelta(df['realtime'].fillna(0), unit='s')
    df['cores_used'] = df['percent_cpu'] / 100.0
    for name, (requested, used, _) in RESOURCES.items():
        df[f'{name}_eff'] = _ratio(df[used], df[requested])
    parts = df['process_name'].fillna('UNKNOWN').str.rsplit(':', n=1)
    df['process'] = parts.str[-1]
    df['subworkflow'] = parts.map(lambda p: p[0] if len(p) > 1 else '')
    for col in ('input_bytes', 'output_bytes'):
        if col not in df.columns:
            df[col] = np.nan
    df['input_mb'] = df['input_bytes'] / (1024 * 1024)
    df['output_mb'] = df['output_bytes'] / (1024 * 1024)
    df['co2e_g'] = df['co2e_mg'] / 1000.0 if 'co2e_mg' in df.columns else np.nan
    df['finished'] = df['final_status'].isin(FINISHED_STATUSES)
    return df


def concurrency(tasks: pd.DataFrame) -> pd.DataFrame:
    """Step series over time of running tasks, CPUs allocated and used, and memory
    allocated and peak (GB), from each task's [start, end) interval. Memory used is
    the sum of the running tasks' peaks, an upper bound."""
    df = tasks[tasks['start'].notna() & (tasks['end'] > tasks['start'])]
    if df.empty:
        return pd.DataFrame(columns=['time', 'running', 'cpus_allocated', 'cpus_used',
                                     'memory_allocated_gb', 'memory_peak_gb'])
    deltas = pd.DataFrame({
        'running': 1.0,
        'cpus_allocated': df['cpus_requested'].fillna(0),
        'cpus_used': df['cores_used'].fillna(0),
        'memory_allocated_gb': df['memory_requested'].fillna(0) / 1024,
        'memory_peak_gb': df['peak_rss'].fillna(0) / 1024,
    })
    starts = deltas.assign(time=df['start'].values, order=1)
    ends = (-deltas).assign(time=df['end'].values, order=0)  # ends first at equal times
    events = pd.concat([starts, ends]).sort_values(['time', 'order'])
    series = events.drop(columns='order').groupby('time').sum().cumsum()
    return series.round(9).reset_index()  # cumulative float sums leave -1e-16 residues at zero


def process_table(tasks: pd.DataFrame) -> pd.DataFrame:
    """One row per process over its finished attempts, ordered by CPU-hours."""
    df = tasks[tasks['finished']]
    if df.empty:
        return pd.DataFrame()
    grouped = df.groupby('process_name')
    table = pd.DataFrame({
        'process': grouped['process'].first(),
        'subworkflow': grouped['subworkflow'].first(),
        'tasks': grouped.size(),
        'realtime_median_s': grouped['realtime'].median(),
        'realtime_max_s': grouped['realtime'].max(),
        'cpu_percent_median': grouped['percent_cpu'].median(),
        'cpu_eff_median': grouped['cpu_eff'].median(),
        'peak_rss_median_mb': grouped['peak_rss'].median(),
        'peak_rss_max_mb': grouped['peak_rss'].max(),
        'memory_eff_median': grouped['memory_eff'].median(),
        'time_eff_median': grouped['time_eff'].median(),
        'input_mb': grouped['input_mb'].sum(min_count=1),
        'output_mb': grouped['output_mb'].sum(min_count=1),
        'cpu_hours': grouped['cpu_hours'].sum(min_count=1),
        'co2e_g': grouped['co2e_g'].sum(min_count=1),
    })
    return table.sort_values('cpu_hours', ascending=False).reset_index()


def rightsizing(tasks: pd.DataFrame, resource: str) -> pd.DataFrame:
    """Finished tasks with a request for `resource`: requested, used, efficiency
    and a flag (over-provisioned below 25 %, near the limit above 90 %)."""
    requested, used, _ = RESOURCES[resource]
    df = tasks[tasks['finished'] & (tasks[requested] > 0) & tasks[used].notna()].copy()
    df['requested'] = df[requested]
    df['used'] = df[used]
    df['efficiency'] = df[f'{resource}_eff']
    df['flag'] = np.select(
        [df['efficiency'] < OVER_PROVISIONED, df['efficiency'] > NEAR_LIMIT],
        ['over-provisioned', 'near limit'], default='ok')
    return df


def attempt_chains(tasks: pd.DataFrame, logical: pd.DataFrame) -> pd.DataFrame:
    """Logical tasks that were retried or never succeeded, each with its attempts
    in start order as 'attempt: exit code @ memory requested'."""
    if logical.empty:
        return pd.DataFrame()
    shown = logical[(logical['attempts'] > 1) | ~logical['succeeded']].copy()
    if shown.empty:
        return shown

    def chain(name):
        rows = tasks[tasks['task_name'] == name].sort_values('start_time')
        return ' → '.join(
            f"#{r.attempt}: exit {r.exit_code}"
            + (f" @ {r.memory_requested:.0f} MB" if pd.notna(r.memory_requested) else '')
            for r in rows.itertuples())

    shown['history'] = shown['task_name'].map(chain)
    return shown
