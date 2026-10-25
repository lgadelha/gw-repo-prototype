"""Bayesian modelling (Optimization nav group): one BayesianRidge model per process
(api/ml), trained on completed attempts, predicting memory, time and CPUs for a given
input size, with Nextflow settings to download. Processes without a size model get
settings from their past usage. Tabs: Training, Optimizer (one process) and All
processes."""

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from common import (API_BASE_URL, api_get, api_key, clear_api_cache, empty_state, fmt_duration, fmt_mb, kpi,
                    normalize_module_name, run_colours)

PRIORITIES = {'balanced': "Balanced", 'cost': "Cost", 'performance': "Performance"}
PRIORITY_HELP = ("The setting is a percentile of the model's prediction (or of past usage, for processes "
                 "without a size model): about that share of tasks would fit. **Cost:** 80th, more risk of "
                 "running out. **Balanced:** 95th. **Performance:** 99th.")
# Per resource: model target column of the history, axis label, how to show a value.
RESOURCES = {'memory': ('peak_rss', 'Peak RSS (MB)', fmt_mb),
             'time': ('duration', 'Duration (s)', fmt_duration),
             'cpu': ('cpus_used', 'CPUs used', lambda v: f"{v:.1f}")}
TITLES = {'memory': "Memory", 'time': "Time", 'cpu': "CPUs"}


def render(all_runs: pd.DataFrame):
    st.title("Bayesian modelling")
    names = pd.DataFrame(api_get("/process_names") or [])
    if names.empty:
        empty_state("No tasks recorded yet.")
        return
    names['module'] = names['process'].map(normalize_module_name)
    models = api_get("/ml/models/") or []
    trained = {m['process_name']: m['training_samples'] for m in models if not m.get('is_fallback_model')}
    # Every process, those with a size model first
    processes = sorted(names['module'].unique(), key=lambda p: (-trained.get(p, 0), p))
    settings = _settings()
    training, optimizer, bulk = st.tabs([":material/model_training: Training", ":material/tune: Optimizer",
                                         ":material/checklist: All processes"])
    with training:
        _training(names, models, trained)
    with optimizer:
        _optimizer(processes, trained, settings, all_runs)
    with bulk:
        _all_processes(processes, trained, settings)


def _post(path: str, body: dict, timeout: int):
    """POST to the API; None after showing the error."""
    try:
        response = requests.post(f"{API_BASE_URL}{path}", json=body, timeout=timeout,
                                 headers={"Authorization": f"Bearer {api_key()}"})
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        st.error(f"API request {path} failed: {e}", icon=":material/error:")
        return None


@st.cache_data(ttl=30, show_spinner="Predicting…")
def _predict(process: str, size_gb: float, priority: str, limits: tuple, key: str):
    """/ml/optimize-for-size, cached per inputs (`key`: the API key, so caches don't mix)."""
    response = requests.post(f"{API_BASE_URL}/ml/optimize-for-size", timeout=120,
                             headers={"Authorization": f"Bearer {key}"},
                             json={'process_name': process, 'expected_disk_gb': size_gb, 'priority': priority,
                                   'resource_limits': dict(limits)})
    response.raise_for_status()
    return response.json()


def predict(process: str, size_mb: float, settings: dict):
    try:
        return _predict(process, size_mb / 1000, settings['priority'], tuple(sorted(settings['limits'].items())),
                        api_key())
    except requests.RequestException as e:
        st.error(f"Prediction for {process} failed: {e}", icon=":material/error:")
        return None


def history(process: str) -> pd.DataFrame:
    """The process's completed attempts, as the model sees them, with input size in MB."""
    tasks = pd.DataFrame((api_get(f"/ml/optimization/{process}") or {}).get('tasks') or [])
    if not tasks.empty:
        tasks = tasks.rename(columns={'disk_usage_mb': 'input_mb'})
        tasks['cpus_used'] = tasks['percent_cpu'] / 100
    return tasks


def _training(names, models, trained):
    per_module = names.groupby('module').agg(runs=('runs', 'max'), tasks=('tasks', 'sum')).reset_index()
    per_module['samples'] = per_module['module'].map(trained)
    per_module['model'] = per_module['samples'].notna()
    created = pd.to_datetime(pd.Series([m.get('created_at') for m in models]), errors='coerce').max()

    c = st.columns(4)
    kpi(c[0], "Processes with a model", f"{len(trained)} of {len(per_module)}")
    kpi(c[1], "Training samples", int(sum(trained.values())), "completed attempts with an input size")
    kpi(c[2], "Without a model", int((~per_module['model']).sum()), "settings from past usage instead")
    kpi(c[3], "Last trained", created.strftime('%Y-%m-%d') if pd.notna(created) else "never",
        created.strftime('%H:%M') if pd.notna(created) else None)

    st.caption("One model per process with at least 10 completed attempts that have an input size (from "
               "lineage-import), predicting peak memory, duration and CPUs used from the input size and the "
               "process's usage profile. Training replaces all models. Processes without one, or whose input "
               "sizes barely vary, get a percentile of their past usage instead.")
    if st.button("Train models", type="primary", icon=":material/model_training:"):
        with st.spinner("Training models…"):
            result = _post("/ml/train", {}, timeout=600)
        if result and result.get('success'):
            st.session_state['ml_trained_message'] = result.get('message')
            clear_api_cache()
            st.rerun()
        elif result:
            st.error(f"Training failed: {result.get('error')}", icon=":material/error:")
    if message := st.session_state.pop('ml_trained_message', None):
        st.success(message, icon=":material/check_circle:")

    st.dataframe(per_module.sort_values(['model', 'samples', 'tasks'], ascending=False), hide_index=True,
                 width="stretch",
                 column_config={'module': 'Process', 'runs': 'Runs', 'tasks': 'Attempts (all states)',
                                'samples': st.column_config.NumberColumn('Training samples', format="%d"),
                                'model': st.column_config.CheckboxColumn('Model')})


def _settings() -> dict:
    with st.expander("Prediction settings", icon=":material/settings:"):
        c = st.columns(4)
        priority = c[0].segmented_control("Priority", list(PRIORITIES), format_func=PRIORITIES.get,
                                          default='balanced', key='ml_priority', help=PRIORITY_HELP) or 'balanced'
        cpus = c[1].number_input("Max CPUs", 1, 1024, 32, key='ml_max_cpus')
        memory_gb = c[2].number_input("Max memory (GB)", 0.1, 4096.0, 128.0, step=1.0, key='ml_max_memory')
        hours = c[3].number_input("Max time (hours)", 0.1, 720.0, 24.0, step=1.0, key='ml_max_hours')
        st.caption("For the Optimizer and All processes tabs. The limits are hard caps, as "
                   "`process.resourceLimits` in a Nextflow config.")
    return {'priority': priority,
            'limits': {'max_cpus': int(cpus), 'max_memory_mb': memory_gb * 1024, 'max_duration_sec': int(hours * 3600)}}


def _optimizer(processes, trained, settings, all_runs):
    current = st.session_state.get('process') or st.query_params.get('process')
    current = normalize_module_name(current) if current else None
    process = st.selectbox("Process", processes, index=processes.index(current) if current in processes else 0,
                           format_func=lambda p: f"{p} ({trained[p]} training samples)" if p in trained
                           else f"{p} (from history)", key='ml_process')
    st.session_state['process'] = process
    st.query_params['process'] = process

    tasks = history(process)
    if tasks.empty:
        empty_state("No completed attempts of this process yet.")
        return
    sized = tasks[tasks['input_mb'].notna()]
    c = st.columns(4)
    kpi(c[0], "Completed attempts", len(tasks), f"{tasks['workflow_execution_id'].nunique()} runs")
    kpi(c[1], "With an input size", len(sized))
    if sized.empty:
        kpi(c[2], "Median input size", "–", "run lineage-import for its runs")
    else:
        lo, hi = sized['input_mb'].min(), sized['input_mb'].max()
        kpi(c[2], "Median input size", _fmt_size(sized['input_mb'].median()), f"{_fmt_size(lo)} – {_fmt_size(hi)}")
    kpi(c[3], "Median peak RSS", fmt_mb(tasks['peak_rss'].median()))

    size_mb = 0.0
    if not sized.empty:
        size_mb = st.number_input("Input size (MB)", min_value=0.001,
                                  value=round(float(sized['input_mb'].median()), 3), step=1.0, format="%.3f",
                                  key=f'ml_size_{process}', help="The task's sample input size: the sum of its input files, without reference "
                                       "files shared by the process's other tasks.")
    result = predict(process, size_mb, settings)
    if not result:
        return
    if not result.get('success'):
        st.error(f"Prediction failed: {result.get('error')}", icon=":material/error:")
        return
    from_history = result.get('source') == 'history'
    if not from_history and not sized.empty and (size_mb < lo or size_mb > hi):
        st.warning(f"Outside the sizes seen so far ({_fmt_size(lo)} – {_fmt_size(hi)}): the prediction "
                   "extrapolates.", icon=":material/warning:")
    for warning in result.get('warnings') or []:
        st.warning(warning, icon=":material/warning:")

    predictions = result['predictions']
    c = st.columns(3)
    for column, resource in zip(c, RESOURCES):
        kpi(column, TITLES[resource], _fmt(resource, predictions[resource]['final_with_safety']),
            _detail(resource, predictions[resource], from_history or resource == 'cpu'))

    names = ({r.id: r.run_name if isinstance(r.run_name, str) and r.run_name else r.id[:8]
              for r in all_runs.itertuples()} if not all_runs.empty else {})
    for tab, resource in zip(st.tabs(list(TITLES.values())), RESOURCES):
        with tab:
            st.plotly_chart(_history_chart(tasks if sized.empty else sized, resource, None if sized.empty else size_mb,
                                           predictions[resource], names), width="stretch")
    if from_history:
        st.caption("Points: the process's completed attempts. Diamond: their median with the 2.5–97.5 % range; "
                   "dashed line: the setting, the chosen percentile of past use.")
    else:
        st.caption("Points: the process's completed attempts with an input size. Diamond: the prediction for your "
                   "input size with its 95 % interval (CPUs: the median used); dashed line: the setting, the chosen "
                   "percentile.")

    st.subheader("Nextflow settings")
    st.code(result['nextflow_config'], language='groovy')
    st.download_button("Download config", result['nextflow_config'], file_name=f"{process}.config",
                       mime="text/plain", icon=":material/download:")


def _history_chart(tasks, resource, size_mb, prediction, names):
    """Past attempts against input size, with the prediction at `size_mb`; without input
    sizes (`size_mb` None), against their run, with the prediction beside them."""
    column, label, _ = RESOURCES[resource]
    colours = run_colours(names)
    fig = go.Figure()
    for wf, group in tasks.groupby('workflow_execution_id'):
        name = names.get(wf, wf[:8])
        x = group['input_mb'] if size_mb is not None else [name] * len(group)
        fig.add_trace(go.Scatter(x=x, y=group[column], mode='markers', name=name,
                                 marker=dict(color=colours.get(name)), text=group['task_name'],
                                 hovertemplate="%{text}<br>%{x:.1f} MB → %{y:.1f}<extra></extra>"))
    ci = prediction['confidence_interval']
    fig.add_trace(go.Scatter(x=[size_mb if size_mb is not None else 'Prediction'], y=[prediction['prediction']],
                             mode='markers', name='Prediction',
                             marker=dict(symbol='diamond', size=13, color='#0f172a', line=dict(width=1, color='white')),
                             error_y=dict(type='data', symmetric=False, array=[ci['upper'] - prediction['prediction']],
                                          arrayminus=[prediction['prediction'] - ci['lower']])))
    setting = prediction['final_with_safety']
    if not prediction.get('enforced_minimum'):
        fig.add_hline(y=setting, line_dash='dash', line_color='#64748b',
                      annotation_text=f"setting {_fmt(resource, setting)}", annotation_position='top left')
    fig.update_layout(xaxis_title="Input size (MB)" if size_mb is not None else "Run", yaxis_title=label, legend_title_text="Run", height=420,
                      margin=dict(t=30))
    return fig


def _all_processes(processes, trained, settings):
    c = st.columns([2, 1])
    strategy = c[0].radio("Input size per process", ['median', 'max', 'same'], horizontal=True,
                          format_func={'median': "Median seen", 'max': "Largest seen",
                                       'same': "The same for all"}.get, key='ml_bulk_strategy')
    same_mb = c[1].number_input("Input size (MB)", min_value=0.001, value=100.0, step=10.0, key='ml_bulk_size',
                                disabled=strategy != 'same')
    rows, configs = [], []
    progress = st.progress(0.0, text="Predicting…")
    for i, process in enumerate(sorted(processes)):
        progress.progress((i + 1) / len(processes), text=f"Predicting {process}…")
        sizes = history(process)
        sizes = sizes['input_mb'].dropna() if not sizes.empty else pd.Series(dtype=float)
        size_mb = same_mb if strategy == 'same' else (sizes.median() if strategy == 'median' else sizes.max())
        row = {'process': process, 'samples': trained.get(process), 'input_mb': size_mb}
        result = predict(process, float(size_mb) if pd.notna(size_mb) else 0.0, settings)
        if result and result.get('success'):
            p = result['predictions']
            row['source'] = "history" if result.get('source') == 'history' else "model"
            notes = [f"{TITLES[r].lower()} capped" for r in RESOURCES if p[r].get('capped_by_limit')]
            notes += ["CPU use reached the request"] if p['cpu'].get('capped_by_request') else []
            notes += ["time raised to 10 min"] if p['time'].get('enforced_minimum') else []
            uncertain = sum('uncertainty' in w for w in result.get('warnings') or [])
            notes += [f"{uncertain} uncertain"] if uncertain else []
            row.update(memory=fmt_mb(p['memory']['final_with_safety']),
                       time=fmt_duration(p['time']['final_with_safety']),
                       cpus=int(round(p['cpu']['final_with_safety'])), notes=", ".join(notes))
            configs.append(result['nextflow_config'])
        else:
            row['notes'] = (result or {}).get('error') or "prediction failed"
        rows.append(row)
    progress.empty()

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={'process': 'Process', 'samples': st.column_config.NumberColumn('Samples', format="%d"),
                                'input_mb': st.column_config.NumberColumn('Input size (MB)', format="%.1f"),
                                'source': 'From',
                                'memory': 'Memory', 'time': 'Time', 'cpus': 'CPUs', 'notes': 'Notes'})
    if configs:
        combined = "\n".join(configs)
        st.download_button(f"Download config ({len(configs)} processes)", combined, file_name="optimized.config",
                           mime="text/plain", icon=":material/download:", type="primary")
        with st.expander("Config", icon=":material/code:"):
            st.code(combined, language='groovy')


def _fmt(resource, value):
    return RESOURCES[resource][2](value) if resource != 'cpu' else str(int(round(value)))


def _fmt_size(mb):
    return fmt_mb(mb) if mb >= 1 else f"{mb * 1000:.0f} kB"


def _detail(resource, p, from_history=False):
    ci = p['confidence_interval']
    show = RESOURCES[resource][2]
    if from_history:
        parts = [f"Median {show(p['prediction'])} over {p['samples']} attempts, 95 % range {show(ci['lower'])} – "
                 f"{show(ci['upper'])}", f"setting at the {p['quantile'] * 100:.0f}th percentile of past use"]
    else:
        parts = [f"Predicted {show(p['prediction'])}, 95 % interval {show(ci['lower'])} – {show(ci['upper'])}",
                 f"setting at the {p['quantile']:.0%} upper bound"]
    if p.get('enforced_minimum'):
        parts.append("raised to the 10 min minimum")
    if p.get('capped_by_limit'):
        parts.append("capped at the limit")
    if p.get('capped_by_request'):
        parts.append(f"use reached the {p['requested']} requested: it might use more if given more")
    return "; ".join(parts)
