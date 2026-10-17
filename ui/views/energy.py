"""Energy & CO2 (PLAN-frontend.md 3.7), from nf-co2footprint via co2-import: the
selected run's totals and equivalents, energy per process split into CPU and
memory, CO2e per sample, the carbon intensity and power draw used, and CO2e per
run over time for the runs in the filter."""

import pandas as pd
import plotly.express as px
import streamlit as st

from common import api_get, fmt_duration, page_header, kpi, empty_state, institute_colours

TOP_PROCESSES = 20


def render(run: pd.Series, filtered_runs: pd.DataFrame):
    page_header("Energy & CO2", run)
    rows = pd.DataFrame(api_get(f"/workflows/{run['id']}/co2/tasks") or [])
    if rows.empty:
        empty_state("No CO2 data for this run: run the pipeline with nf-co2footprint, then co2-import (submit_run.py).")
        _over_time(filtered_runs)
        return
    summary = api_get(f"/workflows/{run['id']}/co2/", _quiet=True)  # 404 without nf-co2footprint's summary file

    c = st.columns(5)
    kpi(c[0], "Energy", f"{rows['energy_consumption_mwh'].sum() / 1000:.2f} Wh")
    kpi(c[1], "CO2e", f"{rows['co2e_mg'].sum() / 1000:.2f} g")
    kpi(c[2], "Car equivalent", f"{summary['car_km_equivalent'] * 1000:.1f} m" if summary else '–',
        help="nf-co2footprint's summary: distance driven by an average car for the same CO2e")
    kpi(c[3], "Tree time", fmt_duration(summary['tree_sequestration_time_sec']) if summary else '–',
        help="nf-co2footprint's summary: time a tree takes to absorb the same CO2e")
    intensity = rows['carbon_intensity_gco2e_kwh']
    kpi(c[4], "Carbon intensity", f"{intensity.median():.0f}", caption="gCO2e/kWh",
        help=f"Used by nf-co2footprint; range {intensity.min():.0f}–{intensity.max():.0f}")
    models = rows.groupby('cpu_model')['powerdraw_cpu_w'].first()
    st.caption("CPU model and power draw per core assumed: "
               + " · ".join(f"{m} ({w:g} W)" for m, w in models.items()))

    per = rows.assign(process=rows['process_name'].str.rsplit(':', n=1).str[-1]).groupby('process_name').agg(
        process=('process', 'first'), cpu=('raw_energy_processor_mwh', 'sum'),
        memory=('raw_energy_memory_mwh', 'sum'), total=('energy_consumption_mwh', 'sum'),
        co2e_mg=('co2e_mg', 'sum')).sort_values('total', ascending=False)
    top = per.head(TOP_PROCESSES)
    long = top.melt(id_vars='process', value_vars=['cpu', 'memory'], var_name='part', value_name='mWh')
    fig = px.bar(long, x='process', y='mWh', color='part', title=f"Energy by process, top {len(top)} (raw, before PUE)",
                 labels={'process': 'Process', 'mWh': 'Energy (mWh)', 'part': ''})
    st.plotly_chart(fig, width="stretch")
    share = 100 * top['total'].sum() / per['total'].sum()
    st.caption(f"These {len(top)} of {len(per)} processes use {share:.0f}% of the run's energy. "
               f"Memory accounts for {100 * per['memory'].sum() / (per['cpu'].sum() + per['memory'].sum()):.0f}% "
               "of the raw energy.")

    by_sample = rows[rows['tag'].notna()].groupby('tag')['co2e_mg'].sum().sort_values(ascending=False) / 1000
    if not by_sample.empty:
        fig = px.bar(by_sample.rename('co2e_g').rename_axis('sample').reset_index(), x='sample', y='co2e_g',
                     title="CO2e per sample (tasks tagged with the sample)",
                     labels={'sample': 'Sample', 'co2e_g': 'CO2e (g)'})
        st.plotly_chart(fig, width="stretch")
        untagged = rows.loc[rows['tag'].isna(), 'co2e_mg'].sum() / 1000
        st.caption(f"Tasks without a sample tag (e.g. reference preparation): {untagged:.2f} g.")
    _over_time(filtered_runs)


def _over_time(filtered_runs):
    runs = filtered_runs[filtered_runs['co2e_g'].notna() & filtered_runs['start'].notna()]
    if len(runs) < 2:
        return
    st.subheader("Runs in the filter")
    fig = px.scatter(runs, x='start', y='co2e_g', color='institute_id', size='energy_wh',
                     color_discrete_map=institute_colours(), hover_name='run_label',
                     title="CO2e per run", labels={'start': 'Start', 'co2e_g': 'CO2e (g)', 'institute_id': 'Institute',
                                                   'energy_wh': 'Energy (Wh)'})
    st.plotly_chart(fig, width="stretch")
