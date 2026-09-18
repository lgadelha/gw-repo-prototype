import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import requests
import os
import re
import json

st.set_page_config(page_title="GW-RePO (Genomic Workflow Resource and Parameter Optimization)", layout="wide")

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost")
env_api_key = os.getenv("API_KEY", "")

with st.sidebar:
    st.subheader("Authentication")
    API_KEY = st.text_input("API Key", value=env_api_key, type="password")
    
    st.divider()
    
    st.subheader("Navigation")
    page = st.selectbox(
        "Select page",
        ["Workflow Summaries", "Dashboard", "Analytics", "ML Training", "Optimizations", "Model Performance"],
        index=0,
        label_visibility="collapsed"
    )

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
            'disk_usage_mb': 'Disk Usage (MB)',
            'read_bytes': 'Data Read (MB)',
            'write_bytes': 'Data Written (MB)',
            'start_time': 'Start Time',
            'short_name': 'Process',
            'normalized_name': 'Normalized Process'
        }
        
        # Select and order columns for display
        display_columns = [
            'process_name', 'workflow_name', 'duration',
            'peak_rss', 'peak_vmem', 'percent_cpu', 'cpus_requested',
            'memory_requested', 'time_requested', 'disk_usage_mb',
            'read_bytes', 'write_bytes'
        ]
        available_columns = [col for col in display_columns if col in display_df.columns]
        
        # Rename columns for display
        display_df = display_df.rename(columns=column_labels)
        display_column_labels = [column_labels.get(col, col) for col in available_columns]
        
        st.subheader("📋 Detailed Execution Data (All Runs)")
        st.dataframe(
            display_df[display_column_labels],
            use_container_width=True,
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
        display_df = df.copy()
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
            'disk_usage_mb': ('Disk Usage', 'MB'),
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
                    st.dataframe(stats_df, use_container_width=True)
    
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

    work_df = df.copy()
    
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
                    'storage_requested', 'time_requested', 'disk_usage_mb', 
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
        'disk_usage_mb': 'Average Disk Usage (MB)',
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
    st.plotly_chart(fig, use_container_width=True)
    
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
            st.plotly_chart(fig_cpu, use_container_width=True)
            
            # CPU utilization histogram
            fig_hist = px.histogram(
                filtered_df,
                x='percent_cpu',
                color='normalized_name',
                title='CPU Utilization Histogram',
                labels={'percent_cpu': 'CPU Utilization (%)', 'count': 'Count'}
            )
            fig_hist.update_layout(margin=dict(b=100))
            st.plotly_chart(fig_hist, use_container_width=True)
    
    # Disk Usage Visualization
    disk_cols = [col for col in ['disk_usage_mb', 'read_bytes', 'write_bytes'] if col in filtered_df.columns]
    if disk_cols and not all(filtered_df[col].isna().all() for col in disk_cols):
        st.subheader("Disk I/O & Storage Metrics")
        
        # Disk usage bar chart
        if 'disk_usage_mb' in filtered_df.columns and not filtered_df['disk_usage_mb'].isna().all():
            disk_df = filtered_df[['normalized_name', 'disk_usage_mb']].dropna()
            if not disk_df.empty:
                fig_disk = px.bar(
                    disk_df.groupby('normalized_name')['disk_usage_mb'].mean().reset_index(),
                    x='normalized_name',
                    y='disk_usage_mb',
                    title='Average Disk Usage by Process (MB)',
                    labels={'normalized_name': 'Process', 'disk_usage_mb': 'Disk Usage (MB)'}
                )
                fig_disk.update_layout(xaxis_tickangle=-45, margin=dict(b=100))
                st.plotly_chart(fig_disk, use_container_width=True)
        
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
                st.plotly_chart(fig_io, use_container_width=True)


@st.cache_data
def fetch_process_data(api_key_val):
    headers = {
        "Authorization": f"Bearer {api_key_val}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(f"{API_BASE_URL}/processes/", headers=headers)
        if response.status_code == 200:
            data = response.json()
            return pd.DataFrame(data)
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
    import sys
    sys.path.append('api')
    from nfcore_modules import normalize_module_name
    return normalize_module_name(name)


def render_analytics():
    st.title("📈 Analytics - Resource Usage vs Disk Size (Per Process)")
    st.write("""
    **Analyze correlations between disk usage and resource consumption for individual processes**
    
    Select a process to view its resource usage patterns.
    """)
    
    # Fetch data
    df = fetch_process_data(API_KEY)
    
    if df.empty:
        st.info("No data available. Submit workflow data first.")
        return
    
    # Sanitize and normalize process names
    df = df.copy()
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
    
    # Filter rows with valid disk_usage_mb and required metrics
    required_cols = ['disk_usage_mb', 'peak_rss', 'cpus_requested', 'percent_cpu', 'duration']
    df_valid = df_process.dropna(subset=required_cols)
    df_valid = df_valid[df_valid['disk_usage_mb'] > 0]
    
    if df_valid.empty:
        st.warning(f"No data with valid disk_usage_mb values found for {selected_process}.")
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
    df_valid['mem_disk_ratio'] = df_valid['peak_rss'] / (df_valid['disk_usage_mb'] + 0.001)
    df_valid['io_intensity'] = df_valid['io_total_mb'] / (df_valid['disk_usage_mb'] + 0.001)
    
    st.info(f"Showing {len(df_valid)} runs for **{selected_process}**")
    
    # User options
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        show_trendlines = st.checkbox("Show trendlines with R² values", value=False)
    with col_opt2:
        log_scale = st.checkbox("Use log scale", value=False)
    
    st.divider()
    
    # ============================================
    # PLOT 1: Memory (peak_rss) vs Disk Size
    # ============================================
    st.subheader(f"📊 Memory vs Disk Size - {selected_process}")
    
    fig_mem = px.scatter(
        df_valid,
        x='disk_usage_mb',
        y='peak_rss',
        labels={'disk_usage_mb': 'Disk Usage (MB)', 'peak_rss': 'Memory (MB)'},
        title=f'Memory Usage vs Disk Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'disk_usage_mb': ':.1f', 'peak_rss': ':.1f'}
    )
    fig_mem.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_mem, use_container_width=True)
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['disk_usage_mb'],
                df_valid['peak_rss']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    st.divider()
    
    # ============================================
    # PLOT 2: Actual CPU Cores Used vs Disk Size
    # ============================================
    st.subheader(f"💻 Actual CPU Cores Used vs Disk Size - {selected_process}")
    st.write("*Calculated as: percent_cpu / 100 (RAW CPU usage across all cores)*")
    
    fig_cpu = px.scatter(
        df_valid,
        x='disk_usage_mb',
        y='actual_cpu_used',
        labels={'disk_usage_mb': 'Disk Usage (MB)', 'actual_cpu_used': 'Actual CPU Cores Used'},
        title=f'Actual CPU Cores Used vs Disk Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=False,  # Don't use log scale for CPU cores
        hover_data={'disk_usage_mb': ':.1f', 'actual_cpu_used': ':.2f', 'cpus_requested': ':.0f', 'percent_cpu': ':.1f'}
    )
    fig_cpu.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_cpu, use_container_width=True)
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['disk_usage_mb'],
                df_valid['actual_cpu_used']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    st.divider()
    
    # ============================================
    # PLOT 3: Duration vs Disk Size
    # ============================================
    st.subheader(f"⏱️ Duration vs Disk Size - {selected_process}")
    
    fig_dur = px.scatter(
        df_valid,
        x='disk_usage_mb',
        y='duration',
        labels={'disk_usage_mb': 'Disk Usage (MB)', 'duration': 'Duration (seconds)'},
        title=f'Duration vs Disk Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'disk_usage_mb': ':.1f', 'duration': ':.1f'}
    )
    fig_dur.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_dur, use_container_width=True)
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['disk_usage_mb'],
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
    # PLOT 4: Data Read vs Disk Size
    # ============================================
    st.subheader(f"📥 Data Read vs Disk Size - {selected_process}")
    
    fig_read = px.scatter(
        df_valid,
        x='disk_usage_mb',
        y='read_bytes_mb',
        labels={'disk_usage_mb': 'Disk Usage (MB)', 'read_bytes_mb': 'Data Read (MB)'},
        title=f'Data Read vs Disk Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'disk_usage_mb': ':.1f', 'read_bytes_mb': ':.1f'}
    )
    fig_read.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_read, use_container_width=True)
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['disk_usage_mb'],
                df_valid['read_bytes_mb']
            )
            r_squared = r_value ** 2
            st.write(f"**Correlation (R²): {r_squared:.3f}** - {'Strong' if r_squared > 0.7 else 'Moderate' if r_squared > 0.4 else 'Weak'} correlation")
        except Exception:
            pass
    
    st.divider()
    
    # ============================================
    # PLOT 5: Data Written vs Disk Size
    # ============================================
    st.subheader(f"📤 Data Written vs Disk Size - {selected_process}")
    
    fig_write = px.scatter(
        df_valid,
        x='disk_usage_mb',
        y='write_bytes_mb',
        labels={'disk_usage_mb': 'Disk Usage (MB)', 'write_bytes_mb': 'Data Written (MB)'},
        title=f'Data Written vs Disk Size ({len(df_valid)} runs)',
        trendline='ols' if show_trendlines else None,
        log_x=log_scale,
        log_y=log_scale,
        hover_data={'disk_usage_mb': ':.1f', 'write_bytes_mb': ':.1f'}
    )
    fig_write.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_write, use_container_width=True)
    
    if show_trendlines and len(df_valid) > 2:
        try:
            from scipy import stats
            slope, intercept, r_value, p_value, std_err = stats.linregress(
                df_valid['disk_usage_mb'],
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
        hover_data={'io_total_mb': ':.1f', 'peak_rss': ':.1f', 'disk_usage_mb': ':.1f'}
    )
    fig_mem_io.update_layout(height=500, showlegend=False)
    st.plotly_chart(fig_mem_io, use_container_width=True)
    
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
    avg_mem_disk_ratio = df_valid['mem_disk_ratio'].mean()
    avg_io_intensity = df_valid['io_intensity'].mean()
    
    col_class1, col_class2 = st.columns(2)
    
    with col_class1:
        if avg_mem_disk_ratio > 10:
            st.warning(f"⚠️ **Memory-Heavy Process**")
            st.write(f"Memory/Disk ratio: **{avg_mem_disk_ratio:.1f}×**")
            st.write("""
            This process loads large amounts of data into RAM without writing to disk.
            
            **ML Impact**: The model will learn to use I/O features (read_bytes, write_bytes) 
            to predict memory requirements for such processes.
            """)
        elif avg_mem_disk_ratio < 0.5:
            st.info(f"ℹ️ **Disk-Heavy Process**")
            st.write(f"Memory/Disk ratio: **{avg_mem_disk_ratio:.2f}×**")
            st.write("This process is I/O-intensive with minimal memory usage.")
        else:
            st.success(f"✅ **Balanced Process**")
            st.write(f"Memory/Disk ratio: **{avg_mem_disk_ratio:.2f}×**")
            st.write("Memory and disk usage are well-proportioned.")
    
    with col_class2:
        if avg_io_intensity > 5:
            st.warning(f"⚠️ **I/O-Intensive Process**")
            st.write(f"I/O Intensity: **{avg_io_intensity:.1f}×**")
            st.write("This process reads/writes significantly more data than its disk footprint.")
        elif avg_io_intensity < 0.5:
            st.info(f"ℹ️ **Compute-Intensive Process**")
            st.write(f"I/O Intensity: **{avg_io_intensity:.2f}×**")
            st.write("This process is computation-heavy with minimal I/O operations.")
        else:
            st.success(f"✅ **Balanced I/O**")
            st.write(f"I/O Intensity: **{avg_io_intensity:.2f}×**")
            st.write("I/O operations are proportional to data size.")


def render_ml_training():
    st.title("🤖 ML Training - Build Prediction Models")
    st.write("""
    **Step 2: Train ML Models**
    
    After reviewing your historical data in the Dashboard, train ML models to learn resource usage patterns. 
    These models will enable predictions for small, medium, and large dataset scenarios.
    """)
    
    if not API_KEY:
        st.warning("Please enter your API key in the sidebar to authenticate.")
        return
    
    # Cache clearing option
    col_cache1, col_cache2 = st.columns([3, 1])
    with col_cache1:
        st.write("**Having issues?** Clear cached data and reload fresh from API.")
    with col_cache2:
        if st.button("🗑️ Clear Cache", use_container_width=True):
            # Clear all cached data
            if 'training_result' in st.session_state:
                del st.session_state['training_result']
            fetch_processes.clear()
            fetch_all_optimizations.clear()
            st.rerun()
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("Training Configuration")
        
        if st.button("Start Training", type="primary", use_container_width=True):
            with st.spinner("Training models... This may take a minute."):
                headers = {
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                }
                try:
                    response = requests.post(
                        f"{API_BASE_URL}/ml/train",
                        headers=headers,
                        json={}
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        st.session_state['training_result'] = result
                        st.success(f"Training completed! {result.get('message', '')}")
                    else:
                        st.error(f"Training failed: {response.text}")
                except Exception as e:
                    st.error(f"Connection failed: {e}")
    
    with col2:
        st.subheader("Training Results")
        if 'training_result' in st.session_state:
            result = st.session_state['training_result']
            
            if result.get('success'):
                st.metric("Training Samples", result.get('training_samples', 0))
                
                model_results = result.get('model_results', {})
                
                if model_results:
                    for model_type, metrics in model_results.items():
                        if isinstance(metrics, dict):
                            r2 = metrics.get('test_r2', 0)
                            rmse = metrics.get('test_rmse', 0)
                            st.write(f"**{model_type.title()} Model**")
                            st.write(f"  - Test R²: {r2:.4f}")
                            st.write(f"  - Test RMSE: {rmse:.4f}")
                            st.write(f"  - CV R²: {metrics.get('cv_r2_mean', 0):.4f}")
                
                stats = result.get('feature_statistics', {})
                if stats:
                    st.write("**Feature Statistics:**")
                    for metric, values in list(stats.items())[:3]:
                        if isinstance(values, dict) and 'mean' in values:
                            st.write(f"- {metric}: mean={values.get('mean', 0):.1f}, range=[{values.get('min', 0):.1f}, {values.get('max', 0):.1f}]")
            else:
                st.error(f"Training failed: {result.get('error', 'Unknown error')}")
        else:
            st.info("No training results yet. Click 'Start Training' to train models.")

@st.cache_data(ttl=60)
def fetch_processes(api_key_val):
    """Fetch available module names from the API (without instance suffix)."""
    headers = {
        "Authorization": f"Bearer {api_key_val}",
        "Content-Type": "application/json"
    }
    try:
        url = f"{API_BASE_URL}/ml/processes"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get('processes', [])
        else:
            return []
    except Exception:
        return []


def fetch_all_optimizations(api_key_val):
    """Fetch all optimization recommendations for ALL processes with S/M/L scenarios."""
    headers = {
        "Authorization": f"Bearer {api_key_val}",
        "Content-Type": "application/json"
    }
    try:
        url = f"{API_BASE_URL}/ml/optimizations"
        response = requests.get(url, headers=headers, timeout=60)
        if response.status_code == 200:
            return response.json()
        else:
            return None
    except Exception:
        return None


def render_optimizations():
    st.title("⚡ Resource Optimizations - ML-Based Configurations")
    st.write("""
    **Get optimized configurations for individual processes OR download all**
    
    ML models predict resources for small, medium, and large dataset sizes.
    Select a process from dropdown to view predictions, or download all configs.
    """)
    
    if not API_KEY:
        st.warning("Please enter your API key in the sidebar to authenticate.")
        return
    
    # Refresh button
    col_refresh1, col_refresh2 = st.columns([4, 1])
    with col_refresh1:
        st.write("**Tip:** Click refresh to reload latest predictions from API")
    with col_refresh2:
        if st.button("🔄 Refresh", use_container_width=True):
            fetch_all_optimizations.clear()
            st.rerun()
    
    # Fetch all optimizations
    with st.spinner("Loading ML predictions for all processes..."):
        all_opts = fetch_all_optimizations(API_KEY)
    
    if not all_opts or not all_opts.get('success'):
        st.error("Failed to fetch optimizations. Train models first on ML Training tab.")
        return
    
    optimizations = all_opts.get('optimizations', [])
    
    if not optimizations:
        st.info("No optimizations available. Submit workflow data and train models.")
        return
    
    # ============================================
    # DROPDOWN TO SELECT PROCESS
    # ============================================
    st.subheader("🔍 Select Process to View Predictions")
    
    process_names = [opt.get('module_name', 'unknown') for opt in optimizations]
    selected_process = st.selectbox("Choose a process:", options=process_names)
    
    if selected_process:
        selected_opt = next((opt for opt in optimizations if opt.get('module_name') == selected_process), None)
        
        if selected_opt:
            scenarios = selected_opt.get('scenarios', {})
            samples = selected_opt.get('historical_samples', 0)
            
            st.write(f"**Historical Runs:** {samples}")
            
            # Show warnings if any scenarios were skipped
            warnings = scenarios.get('_warnings', {})
            if warnings:
                for warning_key, warning_msg in warnings.items():
                    st.warning(f"⚠️ {warning_msg}")
            
            col_s, col_m, col_l = st.columns(3)
            
            size_names = ['SMALL', 'MEDIUM', 'LARGE']
            for i, size_name in enumerate(size_names):
                cfg = scenarios.get(size_name, {})
                if cfg:  # Only show if scenario exists
                    with [col_s, col_m, col_l][i]:
                        st.write(f"**{size_name} Dataset**")
                        st.write(f"Data Size: ~{cfg.get('disk_size_mb', 0):.1f} MB")
                        st.write(f"**CPUs:** {cfg.get('cpus', 1)}")
                        st.write(f"**Memory:** {cfg.get('memory', 'N/A')}")
                        st.write(f"**Time:** {cfg.get('time', 'N/A')}")
                else:
                    # Scenario not available
                    with [col_s, col_m, col_l][i]:
                        st.write(f"**{size_name} Dataset**")
                        st.warning("⚠️ Not available")
                        st.write("Insufficient historical data variation")
    
    st.divider()
    
    # ============================================
    # DOWNLOAD CONFIG FILES (ONLY AVAILABLE SCENARIOS)
    # ============================================
    st.subheader("📥 Download All Configurations")
    
    import io
    
    # Check which scenarios are available across all processes
    available_sizes = []
    size_labels = {'SMALL': 'SMALL', 'MEDIUM': 'MEDIUM', 'LARGE': 'LARGE'}
    
    # Count how many processes have each scenario
    size_counts = {'SMALL': 0, 'MEDIUM': 0, 'LARGE': 0}
    for opt in optimizations:
        scenarios = opt.get('scenarios', {})
        for size in ['SMALL', 'MEDIUM', 'LARGE']:
            if scenarios.get(size):
                size_counts[size] += 1
    
    st.write(f"**Scenario availability:** SMALL: {size_counts['SMALL']}/{len(optimizations)} | MEDIUM: {size_counts['MEDIUM']}/{len(optimizations)} | LARGE: {size_counts['LARGE']}/{len(optimizations)}")
    
    config_cols = st.columns(3)
    
    # Generate configs for each available size
    for idx, size_name in enumerate(['SMALL', 'MEDIUM', 'LARGE']):
        config_lines = [
            f"// Auto-generated Nextflow configuration",
            f"// {size_name} dataset scenario",
            f"// {len(optimizations)} processes ({size_counts[size_name]} with predictions)",
            "process {"
        ]
        
        for opt in optimizations:
            module = opt.get('module_name', 'unknown')
            scenarios = opt.get('scenarios', {})
            cfg = scenarios.get(size_name, {})
            
            if cfg:  # Use ML prediction if available
                config_lines.append(f"    withName: '{module}' {{")
                config_lines.append(f"        cpus = {cfg.get('cpus', 1)}")
                config_lines.append(f"        memory = '{cfg.get('memory', '256 MB')}'")
                config_lines.append(f"        time = '{cfg.get('time', '1h')}'")
                config_lines.append("    }")
            # Skip processes without this scenario
        
        config_lines.append("}")
        config_text = "\n".join(config_lines)
        
        with config_cols[idx]:
            if size_counts[size_name] > 0:
                st.download_button(
                    label=f"⬇️ {size_name}.config ({size_counts[size_name]})",
                    data=config_text,
                    file_name=f"{size_name.lower()}.config",
                    mime="text/plain",
                    use_container_width=True
                )
            else:
                st.info(f"ℹ️ {size_name}.config\nNot available\n(Insufficient data)")
    
    st.info(f"Each file contains ALL {len(optimizations)} processes for that dataset size.")


def render_model_performance():
    st.title("📈 ML Model Performance")
    st.write("""
    **Global ML models trained on ALL processes**
    
    The system trains 3 global models (Memory, Time, CPU) that learn patterns across ALL processes.
    These models use process identity as a feature to make process-specific predictions.
    """)
    
    if not API_KEY:
        st.warning("Please enter your API key in the sidebar to authenticate.")
        return
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Fetch optimizations to show process coverage
    try:
        opts_response = requests.get(f"{API_BASE_URL}/ml/optimizations", headers=headers)
        optimizations = []
        if opts_response.status_code == 200:
            opts_data = opts_response.json()
            optimizations = opts_data.get('optimizations', [])
    except Exception:
        optimizations = []
    
    try:
        response = requests.get(f"{API_BASE_URL}/ml/models", headers=headers)
        
        if response.status_code == 200:
            models = response.json()
            
            if not models:
                st.info("No trained models found. Train models first on the ML Training page.")
                return
            
            # Get latest models only (highest ID for each type)
            latest_models = {}
            for m in models:
                model_name = m.get('model_name', '')
                if 'memory' in model_name.lower():
                    key = 'memory'
                elif 'time' in model_name.lower():
                    key = 'time'
                elif 'cpu' in model_name.lower():
                    key = 'cpu'
                else:
                    continue  # Skip unknown models like Training_Stats
                
                # Keep the one with highest ID (most recent)
                if key not in latest_models or m.get('id', 0) > latest_models[key].get('id', 0):
                    latest_models[key] = m
            
            st.subheader("📊 Global Model Performance")
            
            # Create 3 columns for 3 models
            col_mem, col_time, col_cpu = st.columns(3)
            
            for key, model in latest_models.items():
                model_name = model.get('model_name', 'unknown')
                accuracy = json.loads(model.get('accuracy_metrics', '{}'))
                
                resource_type = key.upper()
                if resource_type == 'MEMORY':
                    col = col_mem
                    icon = '💾'
                elif resource_type == 'TIME':
                    col = col_time
                    icon = '⏱️'
                else:
                    col = col_cpu
                    icon = '💻'
                
                with col:
                    st.subheader(f"{icon} {resource_type} Model")
                    
                    r2 = accuracy.get('test_r2', 0)
                    rmse = accuracy.get('test_rmse', 0)
                    cv_r2 = accuracy.get('cv_r2_mean', 0)
                    
                    # Color based on R²
                    if r2 > 0.9:
                        st.success(f"**R²: {r2:.4f}** ✅ Excellent")
                    elif r2 > 0.7:
                        st.info(f"**R²: {r2:.4f}** 👍 Good")
                    else:
                        st.warning(f"**R²: {r2:.4f}** ⚠️ Moderate")
                    
                    st.metric("RMSE", f"{rmse:.2f}")
                    st.metric("CV R²", f"{cv_r2:.4f}")
                    
                    # Feature importance
                    feature_imp = accuracy.get('feature_importance', {})
                    if feature_imp:
                        with st.expander("🔍 Top Features"):
                            top_features = sorted(feature_imp.items(), key=lambda x: x[1], reverse=True)[:5]
                            for feat, imp in top_features:
                                # Clean up feature names
                                feat_clean = feat.replace('process_base_', '').replace('_', ' ').title()
                                st.write(f"  - {feat_clean}: {imp:.1%}")
            
            st.divider()
            
            # Process Coverage Section with Dropdown
            st.subheader("📋 Process Coverage - Explore by Process")
            st.write(f"**Global models trained on {len(optimizations)} processes**")
            
            if optimizations:
                # Dropdown to select process
                process_names = [o.get('module_name', 'Unknown') for o in optimizations]
                selected_process = st.selectbox(
                    "Select a process to view its training data:",
                    options=sorted(process_names),
                    key="model_perf_process_select"
                )
                
                # Find selected process data
                selected_opt = next((o for o in optimizations if o.get('module_name') == selected_process), None)
                
                if selected_opt:
                    samples = selected_opt.get('historical_samples', 0)
                    scenarios = selected_opt.get('scenarios', {})
                    scenario_count = len([s for s in ['SMALL', 'MEDIUM', 'LARGE'] if s in scenarios])
                    
                    col_info1, col_info2, col_info3 = st.columns(3)
                    with col_info1:
                        st.metric("Historical Samples", samples)
                    with col_info2:
                        st.metric("Scenarios Available", scenario_count)
                    with col_info3:
                        confidence = "High" if samples >= 10 else ("Medium" if samples >= 5 else "Low")
                        st.metric("Confidence", confidence)
                    
                    # Show scenario predictions
                    if scenarios:
                        st.write("**Predictions by Scenario:**")
                        scenario_data = []
                        for size_name in ['SMALL', 'MEDIUM', 'LARGE']:
                            if size_name in scenarios:
                                scen = scenarios[size_name]
                                scenario_data.append({
                                    'Scenario': size_name,
                                    'Memory': scen.get('memory', 'N/A'),
                                    'Time': scen.get('time', 'N/A'),
                                    'CPUs': scen.get('cpus', 'N/A'),
                                    'Disk (MB)': scen.get('disk_size_mb', 0)
                                })
                        
                        import pandas as pd
                        df_scenarios = pd.DataFrame(scenario_data)
                        st.dataframe(df_scenarios, use_container_width=True)
                    else:
                        st.warning("No scenario predictions available for this process")
                
                st.divider()
                
                # All processes summary table
                with st.expander("📊 View All Processes Summary"):
                    # Sort by sample count
                    sorted_opts = sorted(optimizations, key=lambda x: x.get('historical_samples', 0), reverse=True)
                    
                    process_data = []
                    for opt in sorted_opts:
                        scenarios = opt.get('scenarios', {})
                        scenario_count = len([s for s in ['SMALL', 'MEDIUM', 'LARGE'] if s in scenarios])
                        process_data.append({
                            'Process': opt.get('module_name', 'Unknown'),
                            'Samples': opt.get('historical_samples', 0),
                            'Scenarios': scenario_count
                        })
                    
                    df_processes = pd.DataFrame(process_data)
                    st.dataframe(df_processes, use_container_width=True, height=400)
                    
                    # Summary stats
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Total Processes", len(optimizations))
                    with col2:
                        avg_samples = sum(o.get('historical_samples', 0) for o in optimizations) / len(optimizations)
                        st.metric("Avg Samples/Process", f"{avg_samples:.1f}")
                    with col3:
                        high_conf = sum(1 for o in optimizations if o.get('historical_samples', 0) >= 10)
                        st.metric("High Confidence (≥10 samples)", high_conf)
        else:
            st.error(f"Failed to fetch models: {response.text}")
    except Exception as e:
        st.error(f"Connection failed: {e}")


def render_workflow_summaries():
    """Workflow Summaries page showing workflow-level metrics."""
    st.title("📊 Workflow Summaries")
    st.write("**Workflow-level execution metrics and visualizations**")
    
    if not API_KEY:
        st.warning("Please enter your API key in the sidebar to authenticate.")
        return
    
    headers = {"Authorization": f"Bearer {API_KEY}"}
    
    try:
        response = requests.get(f"{API_BASE_URL}/workflows/", headers=headers)
        
        if response.status_code == 200:
            workflows = response.json()
            
            if not workflows:
                st.info("No workflow data available. Submit workflow runs first.")
                return
            
            st.metric("Total Workflow Runs", len(workflows))
            
            df = pd.DataFrame(workflows)
            
            if not df.empty:
                st.subheader("📋 Workflow Execution Table")
                display_cols = [col for col in ['id', 'run_name', 'final_state', 'wall_clock_sec', 'peak_cpu_percent', 'peak_memory_mb', 'max_concurrent_processes'] if col in df.columns]
                if display_cols:
                    st.dataframe(df[display_cols], use_container_width=True)
                
                st.divider()
                st.subheader("📊 Workflow Metrics Visualizations")
                
                if 'final_state' in df.columns:
                    col1, col2 = st.columns(2)
                    with col1:
                        status_counts = df['final_state'].value_counts()
                        fig = px.pie(values=status_counts.values, names=status_counts.index, title='Workflow Status Distribution')
                        st.plotly_chart(fig, use_container_width=True)
                    
                    with col2:
                        if 'wall_clock_sec' in df.columns:
                            df_clean = df[df['wall_clock_sec'].notna()]
                            if not df_clean.empty:
                                fig = px.histogram(df_clean, x='wall_clock_sec', title='Wall Clock Time Distribution', nbins=20)
                                fig.update_layout(xaxis_title='Duration (seconds)')
                                st.plotly_chart(fig, use_container_width=True)
                
                if 'peak_memory_mb' in df.columns and 'peak_cpu_percent' in df.columns:
                    df_clean = df[(df['peak_memory_mb'].notna()) & (df['peak_cpu_percent'].notna())]
                    if not df_clean.empty:
                        fig = px.scatter(df_clean, x='peak_memory_mb', y='peak_cpu_percent', title='Memory vs CPU Usage',
                                        hover_data=['run_name'] if 'run_name' in df_clean.columns else None)
                        st.plotly_chart(fig, use_container_width=True)
                
                if 'max_concurrent_processes' in df.columns:
                    df_clean = df[df['max_concurrent_processes'].notna()]
                    if not df_clean.empty:
                        fig = px.histogram(df_clean, x='max_concurrent_processes', title='Concurrent Processes Distribution', nbins=20)
                        st.plotly_chart(fig, use_container_width=True)
                        
    except Exception as e:
        st.error(f"Failed to fetch workflow data: {e}")


def main():
    st.title("GW-RePO (Genomic Workflow Resource and Parameter Optimization)")
    
    if not API_KEY:
        st.warning("Please enter your API key in the sidebar to authenticate.")
        return
    
    if page == "Workflow Summaries":
        render_workflow_summaries()
    elif page == "Dashboard":
        df = fetch_process_data(API_KEY)
        render_resource_charts(df)
    elif page == "Analytics":
        render_analytics()
    elif page == "ML Training":
        render_ml_training()
    elif page == "Optimizations":
        render_optimizations()
    elif page == "Model Performance":
        render_model_performance()

if __name__ == "__main__":
    main()
