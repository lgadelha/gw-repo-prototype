"""Pure computations behind Compare runs and Samples (no Streamlit).

Run-keyed inputs are dicts {workflow id: DataFrame}; task frames are rows of
/workflows/{id}/task_profile passed through runprofile.add_task_metrics."""

import numpy as np
import pandas as pd

RESOURCE_MEDIANS = {
    # name: (column, aggregation, label)
    'realtime': ('realtime', 'median', 'Median realtime (s)'),
    'peak_rss': ('peak_rss', 'median', 'Median peak RSS (MB)'),
    'cpu_hours': ('cpu_hours', 'sum', 'CPU-hours'),
}


def _annotation_values(df: pd.DataFrame) -> pd.Series:
    """key -> value as text (numbers formatted without a trailing .0)."""
    if df.empty:
        return pd.Series(dtype=object)
    num = df['value_num'].map(lambda v: f"{v:g}" if pd.notna(v) else None)
    return pd.Series(df['value_text'].where(df['value_text'].notna(), num).values, index=df['key'])


def param_diff(annotations: dict) -> pd.DataFrame:
    """Annotation keys (params.* and others) whose values differ between the runs,
    or that only some runs have. One column per run, None where a run lacks the key."""
    table = pd.DataFrame({wf: _annotation_values(df) for wf, df in annotations.items()})
    if table.empty:
        return table
    differs = table.apply(lambda row: row.fillna('\0missing').nunique() > 1, axis=1)
    return table[differs].sort_index()


def checksum_diff(tasks: dict) -> pd.DataFrame:
    """Processes whose code checksum differs between the runs, or that only some runs
    ran. One column per run with the checksum (comma-joined if a process had several)."""
    cols = {}
    for wf, df in tasks.items():
        cols[wf] = df.groupby('process_name')['code_checksum'].agg(
            lambda s: ','.join(sorted(set(s.dropna()))) or None)
    table = pd.DataFrame(cols)
    if table.empty:
        return table
    differs = table.apply(lambda row: row.fillna('\0missing').nunique() > 1, axis=1)
    return table[differs].sort_index()


def resource_change(tasks: dict, baseline: str, resource: str) -> pd.DataFrame:
    """Per process (finished attempts), the resource in each run and its ratio to the
    baseline run; ordered by the largest change (|log ratio|) first."""
    col, how, _ = RESOURCE_MEDIANS[resource]
    table = pd.DataFrame({wf: df[df['finished']].groupby('process_name')[col].agg(how)
                          for wf, df in tasks.items()})
    if table.empty or baseline not in table:
        return pd.DataFrame()
    base = table[baseline].where(table[baseline] > 0)
    ratios = table.drop(columns=baseline).div(base, axis=0)
    out = table.join(ratios.add_prefix('ratio:'))
    change = np.log(ratios.where(ratios > 0)).abs().max(axis=1)
    return out.assign(_change=change).sort_values('_change', ascending=False).drop(columns='_change')


def sample_metrics_wide(metrics: pd.DataFrame) -> pd.DataFrame:
    """Sample metric rows (sample, key, value_num, value_text) -> one row per sample,
    one column per key (numbers where numeric)."""
    if metrics.empty:
        return pd.DataFrame()
    values = metrics['value_num'].where(metrics['value_num'].notna(), metrics['value_text'])
    wide = metrics.assign(value=values).pivot_table(index='sample', columns='key', values='value',
                                                    aggfunc='first')
    wide.columns.name = None
    return wide


def per_sample_resources(tasks: pd.DataFrame) -> pd.DataFrame:
    """Resource use of each sample's tasks (finished attempts whose tag is the
    sample): tasks, CPU-hours, total realtime, max peak RSS, CO2e."""
    df = tasks[tasks['finished'] & tasks['tag'].notna()]
    if df.empty:
        return pd.DataFrame()
    g = df.groupby('tag')
    return pd.DataFrame({
        'tasks': g.size(),
        'cpu_hours': g['cpu_hours'].sum(min_count=1),
        'realtime_s': g['realtime'].sum(min_count=1),
        'peak_rss_max_mb': g['peak_rss'].max(),
        'co2e_g': g['co2e_g'].sum(min_count=1),
    }).rename_axis('sample')
