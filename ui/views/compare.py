"""Compare runs (PLAN-frontend.md 3.5; MTCProv's compare_run and
correlate_parameter_runtime): parameter and version differences, per-process
resource changes against a baseline run, sample metrics side by side, and
resource use per value of an annotation across all runs."""

import pandas as pd
import plotly.express as px
import streamlit as st

import comparison as cmp
import runprofile as prof
from common import api_get

VERSION_FIELDS = {'repository': 'Repository', 'revision_id': 'Revision', 'commit_id': 'Commit',
                  'profile': 'Profile', 'nextflow_version': 'Nextflow', 'script_id': 'Main script hash',
                  'config_sha256': 'Config SHA-256'}
TOP_CHANGES = 25


def render(all_runs: pd.DataFrame, filtered_runs: pd.DataFrame):
    st.title("Compare runs")
    labels = dict(zip(all_runs['id'], all_runs['run_label']))
    chosen = [w for w in st.session_state.get('compare', []) if w in labels]
    options = list(dict.fromkeys(chosen + filtered_runs['id'].tolist()))
    chosen = st.multiselect("Runs", options, default=chosen or options[:2], format_func=labels.get)
    st.session_state['compare'] = chosen
    if len(chosen) < 2:
        st.info("Pick at least two runs (or select several rows on the Runs page).")
        return
    baseline = st.selectbox("Baseline", chosen, format_func=labels.get,
                            help="Resource changes are ratios to this run")
    short = {w: labels[w].split(' · ')[0] for w in chosen}
    if len(set(short.values())) < len(chosen):  # run names can repeat across launches
        short = {w: f"{short[w]} ({w[:6]})" for w in chosen}

    params_tab, versions_tab, resources_tab, samples_tab, annotation_tab = st.tabs(
        ["Parameters", "Versions", "Resources", "Samples", "Annotation vs resources"])
    tasks = {w: prof.add_task_metrics(pd.DataFrame(api_get(f"/workflows/{w}/task_profile") or [])) for w in chosen}
    tasks = {w: t for w, t in tasks.items() if not t.empty}
    with params_tab:
        _params(chosen, short)
    with versions_tab:
        _versions(all_runs, chosen, short, tasks)
    with resources_tab:
        _resources(tasks, baseline, short)
    with samples_tab:
        _samples(chosen, short)
    with annotation_tab:
        _annotation(chosen)


def _params(chosen, short):
    annotations = {w: pd.DataFrame(api_get(f"/workflows/{w}/annotations/") or []) for w in chosen}
    diff = cmp.param_diff(annotations)
    if diff.empty:
        st.success("The runs have the same parameters and annotations.")
        return
    genomes = diff.index.str.startswith('params.genomes.')
    if genomes.any() and not st.toggle(f"Show the genomes catalog ({int(genomes.sum())} differing entries)"):
        diff = diff[~genomes]
    st.caption(f"{len(diff)} parameters or annotations differ (empty: the run doesn't have it).")
    st.dataframe(diff.rename(columns=short).rename_axis('Key').reset_index(), hide_index=True, width="stretch")


def _versions(all_runs, chosen, short, tasks):
    runs = all_runs.set_index('id').loc[chosen]
    table = pd.DataFrame({short[w]: [runs.loc[w].get(f) for f in VERSION_FIELDS] for w in chosen},
                         index=list(VERSION_FIELDS.values()))
    table.insert(0, 'Differs', table.apply(lambda r: r.fillna('–').nunique() > 1, axis=1))
    st.dataframe(table.rename_axis('Field').reset_index(), hide_index=True, width="stretch")
    if not tasks:
        return
    diff = cmp.checksum_diff(tasks)
    total = len(set().union(*(set(t['process_name']) for t in tasks.values())))
    st.subheader("Process code")
    if diff.empty:
        st.success(f"All {total} processes ran the same code (code checksum) in every run.")
        return
    st.caption(f"{len(diff)} of {total} processes differ in code checksum or ran in only some runs "
               "(code checksums come from lineage-import).")
    shown = diff.map(lambda c: c[:12] if isinstance(c, str) else None).rename(columns=short)
    st.dataframe(shown.rename_axis('Process').reset_index(), hide_index=True, width="stretch")


def _resources(tasks, baseline, short):
    if baseline not in tasks or len(tasks) < 2:
        st.info("Resource comparison needs task data for the baseline and at least one other run.")
        return
    resource = st.radio("Resource", list(cmp.RESOURCE_MEDIANS), horizontal=True,
                        format_func=lambda r: cmp.RESOURCE_MEDIANS[r][2])
    table = cmp.resource_change(tasks, baseline, resource)
    ratio_cols = [c for c in table.columns if c.startswith('ratio:')]
    ratios = table[ratio_cols].dropna(how='all').head(TOP_CHANGES)
    if ratios.empty:
        st.info("No process ran in both the baseline and another run.")
        return
    long = ratios.rename(columns=lambda c: short[c.split(':', 1)[1]]).rename_axis('process_name').reset_index() \
        .melt(id_vars='process_name', var_name='run', value_name='ratio').dropna()
    long['process'] = long['process_name'].str.rsplit(':', n=1).str[-1]
    fig = px.bar(long, y='process', x='ratio', color='run', barmode='group', orientation='h', log_x=True,
                 hover_data={'process_name': True},
                 title=f"{cmp.RESOURCE_MEDIANS[resource][2]} relative to {short[baseline]} "
                       f"(top {len(ratios)} changes)",
                 labels={'ratio': 'Ratio to baseline (log)', 'process': '', 'run': 'Run'})
    fig.add_vline(x=1, line_dash='dash', line_color='grey')
    fig.update_layout(height=max(400, 22 * len(ratios) * max(1, len(ratio_cols))),
                      yaxis={'categoryorder': 'array', 'categoryarray': long['process'].unique()[::-1]})
    st.plotly_chart(fig, width="stretch")
    st.caption("Finished attempts only. Ratios above 1: more than the baseline.")
    st.dataframe(table.rename(columns=lambda c: f"× {short[c[6:]]}" if c.startswith('ratio:') else short.get(c, c))
                 .rename_axis('Process').reset_index(), hide_index=True, width="stretch")


def _samples(chosen, short):
    wide = {w: cmp.sample_metrics_wide(pd.DataFrame(api_get(f"/workflows/{w}/sample_metrics/") or []))
            for w in chosen}
    wide = {w: t for w, t in wide.items() if not t.empty}
    if len(wide) < 2:
        st.info("Sample metrics (multiqc-import) exist for fewer than two of these runs.")
        return
    shared = set.intersection(*(set(t.index) for t in wide.values()))
    keys = sorted(set.intersection(*(set(t.columns) for t in wide.values())))
    if not shared or not keys:
        st.info("These runs share no sample names or metrics.")
        return
    key = st.selectbox("Metric", keys, index=next((i for i, k in enumerate(keys) if 'total_sequences' in k), 0))
    table = pd.DataFrame({short[w]: t.loc[sorted(shared), key] for w, t in wide.items()})
    st.dataframe(table.rename_axis('Sample').reset_index(), hide_index=True, width="stretch")
    numeric = table.apply(pd.to_numeric, errors='coerce')
    if numeric.notna().any().any():
        fig = px.bar(numeric.rename_axis('sample').reset_index().melt(id_vars='sample', var_name='run'),
                     x='sample', y='value', color='run', barmode='group', title=key,
                     labels={'sample': 'Sample', 'value': key, 'run': 'Run'})
        st.plotly_chart(fig, width="stretch")


def _annotation(chosen):
    keys = set()
    for w in chosen:
        keys |= {a['key'] for a in api_get(f"/workflows/{w}/annotations/") or []
                 if not a['key'].startswith('params.genomes.')}
    if not keys:
        st.info("These runs have no annotations.")
        return
    keys = sorted(keys)
    c = st.columns(2)
    key = c[0].selectbox("Annotation", keys, index=keys.index('campaign') if 'campaign' in keys else 0)
    process = c[1].text_input("Process (substring)", placeholder="e.g. STAR_ALIGN")
    st.caption("Across every run that has this annotation, not only the selected ones (MTCProv's "
               "correlate_parameter_runtime). Finished attempts only.")
    if not process:
        st.info("Enter a process to compare its resource use per annotation value.")
        return
    rows = pd.DataFrame(api_get("/compare_runs", key=key, process_name=process) or [])
    if rows.empty:
        st.info("No finished tasks of a matching process in runs with this annotation.")
        return
    rows['process'] = rows['process_name'].str.rsplit(':', n=1).str[-1]
    fig = px.bar(rows, x='value', y='mean_peak_rss_mb', color='process', barmode='group',
                 hover_data=['runs', 'tasks', 'max_peak_rss_mb', 'mean_realtime_s'],
                 labels={'value': key, 'mean_peak_rss_mb': 'Mean peak RSS (MB)', 'process': 'Process'})
    st.plotly_chart(fig, width="stretch")
    st.dataframe(rows.drop(columns='process'), hide_index=True, width="stretch")
