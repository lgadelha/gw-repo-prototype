"""Process analytics across all runs: the former Analytics & Dashboard page.

Moved unchanged from ui/app.py (phase 1 of PLAN-frontend.md), except
`use_container_width=True` -> `width="stretch"`. `API_KEY` is set by app.py
before each render."""

import json
import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from common import API_BASE_URL, finished, normalize_module_name  # noqa: F401

API_KEY = ""


def render_resource_charts(df: pd.DataFrame):
    st.title("📊 Dashboard - Execution Metrics")
    st.write("View **historical execution data** from your submitted workflow.")
    
    # ============================================
    # DETAILED DATA TABLE - ALL RUNS
    # ============================================
    if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
        # Sanitize process names
        display_df = df.copy()
        if 'process_name' in display_df.columns:
            display_df['process_name'] = display_df['process_name'].apply(
                lambda x: re.sub(r'\s*\(.*\)', '', str(x)) if isinstance(x, str) else str(x)
            )
        
        # Convert bytes to MB for easier reading
        if 'read_bytes' in display_df.columns:
            display_df['read_bytes'] = (display_df['read_bytes'] / (1024 * 1024)).round(2)
        if 'write_bytes' in display_df.columns:
            display_df['write_bytes'] = (display_df['write_bytes'] / (1024 * 1024)).round(2)
        
        # Create formatted column names with units
        column_labels = {
            'process_name': 'Process Name',
            'workflow_name': 'Workflow Name',
            'institute_id': 'Institute ID',
            'duration': 'Duration (s)',
            'peak_rss': 'Peak Memory (MB)',
            'peak_vmem': 'Peak VMem (MB)',
            'percent_cpu': 'CPU Utilization (%)',
            'cpus_requested': 'CPUs Requested (cores)',
            'memory_requested': 'Memory Requested (MB)',
            'time_requested': 'Time Requested (s)',
            'input_mb': 'Input Size (MB)',
            'final_status': 'Status',
            'read_bytes': 'Data Read (MB)',
            'write_bytes': 'Data Written (MB)',
            'start_time': 'Start Time',
            'short_name': 'Process',
            'normalized_name': 'Normalized Process'
        }
        
        # Select and order columns for display
        display_columns = [
            'process_name', 'workflow_name', 'final_status', 'duration',
            'peak_rss', 'peak_vmem', 'percent_cpu', 'cpus_requested',
            'memory_requested', 'time_requested', 'input_mb',
            'read_bytes', 'write_bytes'
        ]
        available_columns = [col for col in display_columns if col in display_df.columns]
        
        # Rename columns for display
        display_df = display_df.rename(columns=column_labels)
        display_column_labels = [column_labels.get(col, col) for col in available_columns]
        
        st.subheader("📋 Detailed Execution Data (All Runs)")
        st.dataframe(
            display_df[display_column_labels],
            width="stretch",
            height=500
        )
        st.info(f"Total runs: {len(display_df)}")
    else:
        st.info("No detailed data available")
    
    st.divider()
    
    # ============================================
    # SUMMARY STATISTICS
    # ============================================
    st.subheader("📈 Summary Statistics by Process")
    
    if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
        display_df = finished(df).copy()
        if 'process_name' in display_df.columns:
            # Clean process names (remove instance suffixes)
            display_df['process_name'] = display_df['process_name'].apply(
                lambda x: re.sub(r'\s*\(.*\)', '', str(x)) if isinstance(x, str) else str(x)
            )
            # Get short name (remove prefix)
            display_df['short_name'] = display_df['process_name'].apply(
                lambda x: x.split(':')[-1] if isinstance(x, str) else str(x)
            )
            # Normalize (merge variants: BCFTOOLS_FILTER_QUERY_FP → BCFTOOLS_FILTER)
            display_df['normalized_name'] = display_df['short_name'].apply(normalize_process_name)
        else:
            display_df['normalized_name'] = 'Unknown'
        
        numeric_metrics = {
            'duration': ('Duration', 's'),
            'peak_rss': ('Memory Used', 'MB'),
            'percent_cpu': ('CPU Utilization', '%'),
            'input_mb': ('Input Size', 'MB'),
            'cpus_requested': ('CPUs Requested', 'cores'),
            'memory_requested': ('Memory Requested', 'MB'),
            'time_requested': ('Time Requested', 's')
        }
        
        for metric, (label, unit) in numeric_metrics.items():
            if metric in display_df.columns and not display_df[metric].isna().all():
                with st.expander(f"{label} ({unit})"):
                    stats_df = display_df.groupby('normalized_name')[metric].agg([
                        'count', 'mean', 'std', 'min', 'median', 'max'
                    ]).round(2)
                    stats_df.columns = ['Runs', 'Average', 'Std Dev', 'Min', 'Median', 'Max']
                    stats_df = stats_df.sort_values('Average', ascending=False)
                    st.dataframe(stats_df, width="stretch")
    
    st.divider()
    
    # ============================================
    # VISUALIZATIONS
    # ============================================
    st.subheader("📉 Process Resource Visualizations")
    
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        st.warning("No data found to display.")
        return
        
    if 'process_name' not in df.columns or 'duration' not in df.columns:
        st.error(f"Missing columns! Available columns: {list(df.columns)}")
        return

    work_df = finished(df).copy()
    
    # Sanitize process names everywhere
    work_df['process_name'] = work_df['process_name'].apply(
        lambda x: re.sub(r'\s*\(.*\)', '', str(x)) if isinstance(x, str) else str(x)
    )
    
    work_df['short_name'] = work_df['process_name'].apply(
        lambda x: x.split(':')[-1] if isinstance(x, str) else str(x)
    )
    
    # Normalize process names (merge variants)
    work_df['normalized_name'] = work_df['short_name'].apply(normalize_process_name)
    
    if 'start_time' in work_df.columns:
        work_df['start_time'] = pd.to_datetime(work_df['start_time'], unit='s', errors='coerce')

    all_processes = work_df['normalized_name'].unique().tolist()
    
    if not all_processes:
        st.info("No process names found.")
        return

    selected_processes = st.multiselect(
        "Select Processes to Display", 
        options=all_processes, 
        default=all_processes[:10] if len(all_processes) >= 10 else all_processes
    )
    
    if not selected_processes:
        st.info("Please select at least one process.")
        return

    filtered_df = work_df[work_df['normalized_name'].isin(selected_processes)]
    
    if filtered_df.empty:
        st.info("No data matches the selected filters.")
        return

    numeric_cols = ['duration', 'peak_rss', 'percent_cpu', 'realtime', 'peak_vmem', 
                    'storage_requested', 'time_requested', 'input_mb', 
                    'read_bytes', 'write_bytes', 'peak_vmem_mb', 'peak_rss_mb']
    available_numeric = [col for col in numeric_cols if col in filtered_df.columns]
    
    avg_df = filtered_df.groupby('normalized_name')[available_numeric].mean().reset_index()

    metric_labels = {
        'duration': 'Average Duration (seconds)',
        'peak_rss': 'Average Peak RSS (MB)',
        'percent_cpu': 'Average CPU Percentage (%)',
        'realtime': 'Average Realtime (seconds)',
        'peak_vmem': 'Average Peak Virtual Memory (MB)',
        'storage_requested': 'Average Storage Requested (MB)',
        'time_requested': 'Average Time Requested (seconds)',
        'input_mb': 'Average Input Size (MB)',
        'read_bytes': 'Average Data Read (MB)',
        'write_bytes': 'Average Data Written (MB)',
        'peak_vmem_mb': 'Average Peak VMEM (MB)',
        'peak_rss_mb': 'Average Peak RSS (MB)'
    }

    selected_metric = st.selectbox(
        "Select Metric to Visualize",
        options=available_numeric,
        format_func=lambda x: metric_labels.get(x, x)
    )

    fig = px.bar(
        avg_df, 
        x='normalized_name', 
        y=selected_metric,
        title=f"Average {metric_labels.get(selected_metric, selected_metric)} by Process",
        labels={'normalized_name': 'Process', selected_metric: metric_labels.get(selected_metric, selected_metric)}
    )
    fig.update_layout(xaxis_tickangle=-45, margin=dict(b=100))
    st.plotly_chart(fig, width="stretch")
    
    # Enhanced CPU Visualization
    if 'percent_cpu' in filtered_df.columns and not filtered_df['percent_cpu'].isna().all():
        st.subheader("CPU Utilization Distribution")
        
        cpu_data = filtered_df[['normalized_name', 'percent_cpu']].dropna()
        if not cpu_data.empty:
            fig_cpu = px.box(
                cpu_data,
                x='normalized_name',
                y='percent_cpu',
                title='CPU Utilization Distribution by Process (shows variability and outliers)',
                labels={'normalized_name': 'Process', 'percent_cpu': 'CPU Utilization (%)'}
            )
            fig_cpu.update_layout(xaxis_tickangle=-45, margin=dict(b=100))
            st.plotly_chart(fig_cpu, width="stretch")
            
            # CPU utilization histogram
            fig_hist = px.histogram(
                filtered_df,
                x='percent_cpu',
                color='normalized_name',
                title='CPU Utilization Histogram',
                labels={'percent_cpu': 'CPU Utilization (%)', 'count': 'Count'}
            )
            fig_hist.update_layout(margin=dict(b=100))
            st.plotly_chart(fig_hist, width="stretch")
    
    # Disk Usage Visualization
    disk_cols = [col for col in ['input_mb', 'read_bytes', 'write_bytes'] if col in filtered_df.columns]
    if disk_cols and not all(filtered_df[col].isna().all() for col in disk_cols):
        st.subheader("Disk I/O & Storage Metrics")
        
        # Disk usage bar chart
        if 'input_mb' in filtered_df.columns and not filtered_df['input_mb'].isna().all():
            disk_df = filtered_df[['normalized_name', 'input_mb']].dropna()
            if not disk_df.empty:
                fig_disk = px.bar(
                    disk_df.groupby('normalized_name')['input_mb'].mean().reset_index(),
                    x='normalized_name',
                    y='input_mb',
                    title='Average Input Size by Process (MB)',
                    labels={'normalized_name': 'Process', 'input_mb': 'Input Size (MB)'}
                )
                fig_disk.update_layout(xaxis_tickangle=-45, margin=dict(b=100))
                st.plotly_chart(fig_disk, width="stretch")
        
        # Read/Write bytes comparison
        if 'read_bytes' in filtered_df.columns and 'write_bytes' in filtered_df.columns:
            io_df = filtered_df[['normalized_name', 'read_bytes', 'write_bytes']].dropna()
            if not io_df.empty:
                # Convert to MB for easier reading
                io_df = io_df.copy()
                io_df['read_bytes'] = io_df['read_bytes'] / (1024 * 1024)
                io_df['write_bytes'] = io_df['write_bytes'] / (1024 * 1024)
                
                io_avg = io_df.groupby('normalized_name')[['read_bytes', 'write_bytes']].mean().reset_index()
                io_melted = io_avg.melt(id_vars=['normalized_name'], value_vars=['read_bytes', 'write_bytes'],
                                       var_name='IO Type', value_name='MB')
                
                # Format labels
                io_labels = {'read_bytes': 'Data Read (MB)', 'write_bytes': 'Data Written (MB)'}
                io_melted['IO Type'] = io_melted['IO Type'].map(io_labels)
                
                fig_io = px.bar(
                    io_melted,
                    x='normalized_name',
                    y='MB',
                    color='IO Type',
                    title='Average I/O by Process (MB)',
                    barmode='group',
                    labels={'normalized_name': 'Process', 'IO Type': 'Operation Type'}
                )
                fig_io.update_layout(xaxis_tickangle=-45, margin=dict(b=100))
                st.plotly_chart(fig_io, width="stretch")


@st.cache_data(ttl=30)
def fetch_process_data(api_key_val):
    headers = {
        "Authorization": f"Bearer {api_key_val}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(f"{API_BASE_URL}/processes/", headers=headers, timeout=30)
        if response.status_code == 200:
            df = pd.DataFrame(response.json())
            io = requests.get(f"{API_BASE_URL}/processes/io_bytes", headers=headers, timeout=30)
            if not df.empty and io.status_code == 200 and io.json():
                io_df = pd.DataFrame(io.json()).rename(columns={'process_execution_id': 'id'})
                df = df.merge(io_df, on='id', how='left')
                df['input_mb'] = df['input_bytes'] / (1024 * 1024)
                df['output_mb'] = df['output_bytes'] / (1024 * 1024)
            return df
        else:
            st.error(f"API Error - Status Code: {response.status_code}")
            st.text(response.text)
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Connection failed: {e}")
        return pd.DataFrame()


def normalize_process_name(name):
    """
    Normalize process name using nfcore_modules.py logic.
    Uses nf-core.cache file for official module names.
    """
    return normalize_module_name(name)


def render_analytics():
    st.title("📈 Analytics - Resource Usage vs Input Size (Per Process)")
    st.write("""
    **Analyze correlations between disk usage and resource consumption for individual processes**
    
    Select a process to view its resource usage patterns.
    """)
    
    # Fetch data
    df = finished(fetch_process_data(API_KEY))
    
    if df.empty:
        st.info("No data available. Submit workflow data first.")
        return
    
    # Sanitize and normalize process names
    df = df.copy()
    if 'input_mb' not in df.columns:
        df['input_mb'] = float('nan')  # no file rows yet: run lineage-import (submit_run.py)
    if 'process_name' in df.columns:
        df['process_name'] = df['process_name'].apply(
            lambda x: re.sub(r'\s*\(.*\)', '', str(x)) if isinstance(x, str) else str(x)
        )
        df['short_name'] = df['process_name'].apply(
            lambda x: x.split(':')[-1] if isinstance(x, str) else str(x)
        )
        # Apply normalization to merge variants
        df['normalized_name'] = df['short_name'].apply(normalize_process_name)
    else:
        df['normalized_name'] = 'Unknown'
    
    # Get unique normalized process names
    processes = sorted(df['normalized_name'].unique().tolist())
    
    if not processes:
        st.info("No processes found.")
        return
    
    # Process selector dropdown
    selected_process = st.selectbox("Select Process", options=processes)
    
    if not selected_process:
        st.info("Please select a process.")
        return
    
    # Filter data for selected normalized process
    df_process = df[df['normalized_name'] == selected_process].copy()
    
    # Filter rows with valid input_mb and required metrics
    required_cols = ['input_mb', 'peak_rss', 'cpus_requested', 'percent_cpu', 'duration']
    df_valid = df_process.dropna(subset=required_cols)
    df_valid = df_valid[df_valid['input_mb'] > 0]
    
    if df_valid.empty:
        st.warning(f"No data with valid input sizes (run lineage-import / submit_run.py) found for {selected_process}.")
        return
    
    # Calculate actual CPU cores used: (percent_cpu / 100) * cpus_requested
    # Note: percent_cpu is RAW usage (cumulative across all cores)
    # Example: 1877% = 18.77 CPU-cores of compute time
    df_valid['actual_cpu_used'] = df_valid['percent_cpu'] / 100.0
    
    # Convert I/O from bytes to MB for consistency
    if 'read_bytes' in df_valid.columns:
        df_valid['read_bytes_mb'] = df_valid['read_bytes'] / (1024 * 1024)
    else:
        df_valid['read_bytes_mb'] = 0.0
    
    if 'write_bytes' in df_valid.columns:
        df_valid['write_bytes_mb'] = df_valid['write_bytes'] / (1024 * 1024)
    else:
        df_valid['write_bytes_mb'] = 0.0
    
    df_valid['io_total_mb'] = df_valid['read_bytes_mb'] + df_valid['write_bytes_mb']
    
    # Calculate memory-to-disk and I/O intensity ratios
    df_valid['mem_input_ratio'] = df_valid['peak_rss'] / (df_valid['input_mb'] + 0.001)
    df_valid['io_intensity'] = df_valid['io_total_mb'] / (df_valid['input_mb'] + 0.001)
    
    st.info(f"Showing {len(df_valid)} runs for **{selected_process}**")
    
    # User options
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        show_trendlines = st.checkbox("Show trendlines with R² values", value=False)
    with col_opt2:
        log_scale = st.checkbox("Use log scale", value=False)
    
    st.divider()
    
    # ============================================
    # PLOT 1: Memory (peak_rss) vs Input Size
    # ============================================
    st.subheader(f"📊 Memory vs Input Size - {selected_process}")
    
    fig_mem = px.scatter(
        df_valid,
        x='input_mb',
        y='peak_rss',
        labels={'input_mb': 'Input Size (MB)', 'peak_rss': 'Memory (MB)'},
        title=f'Memory Usage vs Input Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'input_mb': ':.1f', 'peak_rss': ':.1f'}
    )
    fig_mem.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_mem, width="stretch")
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['input_mb'],
                df_valid['peak_rss']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    st.divider()
    
    # ============================================
    # PLOT 2: Actual CPU Cores Used vs Input Size
    # ============================================
    st.subheader(f"💻 Actual CPU Cores Used vs Input Size - {selected_process}")
    st.write("*Calculated as: percent_cpu / 100 (RAW CPU usage across all cores)*")
    
    fig_cpu = px.scatter(
        df_valid,
        x='input_mb',
        y='actual_cpu_used',
        labels={'input_mb': 'Input Size (MB)', 'actual_cpu_used': 'Actual CPU Cores Used'},
        title=f'Actual CPU Cores Used vs Input Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=False,  # Don't use log scale for CPU cores
        hover_data={'input_mb': ':.1f', 'actual_cpu_used': ':.2f', 'cpus_requested': ':.0f', 'percent_cpu': ':.1f'}
    )
    fig_cpu.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_cpu, width="stretch")
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['input_mb'],
                df_valid['actual_cpu_used']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    st.divider()
    
    # ============================================
    # PLOT 3: Duration vs Input Size
    # ============================================
    st.subheader(f"⏱️ Duration vs Input Size - {selected_process}")
    
    fig_dur = px.scatter(
        df_valid,
        x='input_mb',
        y='duration',
        labels={'input_mb': 'Input Size (MB)', 'duration': 'Duration (seconds)'},
        title=f'Duration vs Input Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'input_mb': ':.1f', 'duration': ':.1f'}
    )
    fig_dur.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_dur, width="stretch")
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['input_mb'],
                df_valid['duration']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    st.divider()
    
    # ============================================
    # I/O VISUALIZATION SECTION
    # ============================================
    st.subheader("💾 I/O Operations Analysis")
    st.write("*Data read/written from work directory scanning*")
    
    # ============================================
    # PLOT 4: Data Read vs Input Size
    # ============================================
    st.subheader(f"📥 Data Read vs Input Size - {selected_process}")
    
    fig_read = px.scatter(
        df_valid,
        x='input_mb',
        y='read_bytes_mb',
        labels={'input_mb': 'Input Size (MB)', 'read_bytes_mb': 'Data Read (MB)'},
        title=f'Data Read vs Input Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'input_mb': ':.1f', 'read_bytes_mb': ':.1f'}
    )
    fig_read.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_read, width="stretch")
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['input_mb'],
                df_valid['read_bytes_mb']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    st.divider()
    
    # ============================================
    # PLOT 5: Data Written vs Input Size
    # ============================================
    st.subheader(f"📤 Data Written vs Input Size - {selected_process}")
    
    fig_write = px.scatter(
        df_valid,
        x='input_mb',
        y='write_bytes_mb',
        labels={'input_mb': 'Input Size (MB)', 'write_bytes_mb': 'Data Written (MB)'},
        title=f'Data Written vs Input Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'input_mb': ':.1f', 'write_bytes_mb': ':.1f'}
    )
    fig_write.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_write, width="stretch")
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['input_mb'],
                df_valid['write_bytes_mb']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    st.divider()
    
    # ============================================
    # PLOT 6: Memory vs I/O (Memory-Heavy Detection)
    # ============================================
    st.subheader(f"🧠 Memory vs I/O - {selected_process}")
    st.write("*High memory with low I/O = memory-intensive computation*")
    
    fig_mem_io = px.scatter(
        df_valid,
        x='io_total_mb',
        y='peak_rss',
        labels={'io_total_mb': 'Total I/O (MB)', 'peak_rss': 'Memory (MB)'},
        title=f'Memory vs I/O ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'io_total_mb': ':.1f', 'peak_rss': ':.1f', 'input_mb': ':.1f'}
    )
    fig_mem_io.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_mem_io, width="stretch")
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['io_total_mb'],
                df_valid['peak_rss']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    # Memory-heavy and I/O intensity classification
    avg_mem_input_ratio = df_valid['mem_input_ratio'].mean()
    avg_io_intensity = df_valid['io_intensity'].mean()
    
    col_class1, col_class2 = st.columns(2)
    
    with col_class1:
        if avg_mem_input_ratio > 10:
            st.warning(f"⚠️ **Memory-Heavy Process**")
            st.write(f"Memory/Input ratio: **{avg_mem_input_ratio:.1f}×**")
            st.write("""
            This process loads large amounts of data into RAM without writing to disk.
            
            **ML Impact**: The model will learn to use I/O features (read_bytes, write_bytes) 
            to predict memory requirements for such processes.
            """)
        elif avg_mem_input_ratio < 0.5:
            st.info(f"ℹ️ **Input-Heavy Process**")
            st.write(f"Memory/Input ratio: **{avg_mem_input_ratio:.2f}×**")
            st.write("This process is I/O-intensive with minimal memory usage.")
        else:
            st.success(f"✅ **Balanced Process**")
            st.write(f"Memory/Input ratio: **{avg_mem_input_ratio:.2f}×**")
            st.write("Memory and disk usage are well-proportioned.")
    
    with col_class2:
        if avg_io_intensity > 5:
            st.warning(f"⚠️ **I/O-Intensive Process**")
            st.write(f"I/O Intensity: **{avg_io_intensity:.1f}×**")
            st.write("This process reads/writes significantly more data than its input size.")
        elif avg_io_intensity < 0.5:
            st.info(f"ℹ️ **Compute-Intensive Process**")
            st.write(f"I/O Intensity: **{avg_io_intensity:.2f}×**")
            st.write("This process is computation-heavy with minimal I/O operations.")
        else:
            st.success(f"✅ **Balanced I/O**")
            st.write(f"I/O Intensity: **{avg_io_intensity:.2f}×**")
            st.write("I/O operations are proportional to data size.")
