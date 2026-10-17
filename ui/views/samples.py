"""Samples (PLAN-frontend.md 3.6; BioWorkbench's Domain info, generalised): the
MultiQC per-sample metrics of a run, the resources each sample's tasks used, and
resource use against a sample metric (this run, or every run in the filter)."""

import pandas as pd
import plotly.express as px
import streamlit as st

import comparison as cmp
import runprofile as prof
from common import api_get

DEFAULT_KEYS = ('fastqc_raw-total_sequences', 'fastqc_raw-percent_gc', 'star-uniquely_mapped_percent',
                'picard_mark_duplicates-PERCENT_DUPLICATION', 'salmon-percent_mapped', 'salmon-num_processed')
RESOURCES = {'cpu_hours': 'CPU-hours', 'realtime_s': 'Total realtime (s)', 'peak_rss_max_mb': 'Max peak RSS (MB)',
             'co2e_g': 'CO2e (g)', 'tasks': 'Tasks'}


def _run_samples(wf):
    """(sample x metric table, sample x resource table) for one run."""
    wide = cmp.sample_metrics_wide(pd.DataFrame(api_get(f"/workflows/{wf}/sample_metrics/") or []))
    tasks = pd.DataFrame(api_get(f"/workflows/{wf}/task_profile") or [])
    res = cmp.per_sample_resources(prof.add_task_metrics(tasks)) if not tasks.empty else pd.DataFrame()
    return wide, res


def render(run: pd.Series, filtered_runs: pd.DataFrame):
    st.title(f"Samples · {run['run_label']}")
    wide, res = _run_samples(run['id'])
    if wide.empty:
        st.info("No sample metrics for this run: run multiqc-import (submit_run.py) after an nf-core pipeline.")
        return
    numeric_keys = [k for k in wide.columns if pd.api.types.is_numeric_dtype(pd.to_numeric(wide[k], errors='coerce'))
                    and pd.to_numeric(wide[k], errors='coerce').notna().any()]
    defaults = [k for k in DEFAULT_KEYS if k in wide.columns] or numeric_keys[:5]
    st.caption(f"{len(wide)} samples, {wide.shape[1]} metrics from MultiQC's General Statistics. Resources: "
               "finished tasks whose tag is the sample (nf-core's meta.id).")
    keys = st.multiselect("Metrics", list(wide.columns), default=defaults)
    table = wide[keys].join(res, how='left') if not res.empty else wide[keys]
    st.dataframe(table.rename(columns=RESOURCES).rename_axis('Sample').reset_index(), hide_index=True,
                 width="stretch")

    st.subheader("Resource use against a sample metric")
    c = st.columns(3)
    x = c[0].selectbox("Sample metric", numeric_keys,
                       index=numeric_keys.index(defaults[0]) if defaults and defaults[0] in numeric_keys else 0)
    y = c[1].selectbox("Resource", list(RESOURCES), format_func=RESOURCES.get)
    scope = c[2].radio("Runs", ["this run", "all runs in the filter"], horizontal=True)
    runs = [run['id']] if scope == "this run" else filtered_runs['id'].tolist()
    labels = dict(zip(filtered_runs['id'], filtered_runs['run_label']))
    points = []
    for wf in runs:
        w, r = (wide, res) if wf == run['id'] else _run_samples(wf)
        if w.empty or r.empty or x not in w.columns:
            continue
        joined = w[[x]].join(r[[y]], how='inner').reset_index().rename(columns={'index': 'sample'})
        points.append(joined.assign(run=labels.get(wf, run['run_label'])))
    data = pd.concat(points) if points else pd.DataFrame()
    if data.empty:
        st.info("No run in scope has both this metric and tasks tagged with its samples.")
        return
    data[x] = pd.to_numeric(data[x], errors='coerce')
    fig = px.scatter(data.dropna(subset=[x, y]), x=x, y=y, color='run', hover_name='sample',
                     labels={y: RESOURCES[y], 'run': 'Run'})
    st.plotly_chart(fig, width="stretch")
    st.caption(f"{len(data)} samples from {data['run'].nunique()} run(s).")
