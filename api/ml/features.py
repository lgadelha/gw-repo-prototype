"""
Feature engineering for ML-based resource prediction.
Extracts features from historical workflow execution data.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from sqlmodel import Session, select
from datetime import datetime, timezone


def extract_process_features(session: Session) -> pd.DataFrame:
    """
    Extract features from process execution data for ML training.
    
    Features include:
    - Process name
    - Requested resources (CPUs, memory, time, disk)
    - Actual usage (peak RSS, peak VMEM, CPU%, memory%)
    - I/O metrics (read/write chars, disk bytes)
    - Size category encoding
    """
    
    # Build query for process executions
    query = """
    SELECT 
        p.id,
        p.process_name,
        p.module_name,
        p.container_name,
        p.cpus_requested,
        p.time_requested,
        p.storage_requested,
        p.memory_requested,
        p.realtime,
        p.percent_cpu,
        p.percent_memory,
        p.peak_rss,
        p.peak_vmem,
        p.read_char,
        p.write_char,
        p.duration,
        p.disk_usage_mb,
        p.read_bytes,
        p.write_bytes,
        p.peak_vmem_mb,
        p.peak_rss_mb,
        w.run_name,
        w.nextflow_version
    FROM processexecution p
    LEFT JOIN workflowexecution w ON p.workflow_execution_id = w.id
    """

    
    # Execute query
    df = pd.read_sql(query, session.bind)
    
    if df.empty:
        return df
    
    # ==================== Feature Engineering ====================
    
    # 1. Process name features
    # Extract base process name (last component)
    df['process_base'] = df['process_name'].apply(
        lambda x: x.split(':')[-1].split()[0] if isinstance(x, str) else 'unknown'
    )
    
    # Extract module name from full process name
    df['has_module'] = df['process_name'].apply(
        lambda x: 1 if ':' in str(x) else 0
    )
    
    # 2. Resource ratio features
    df['cpu_utilization'] = df['percent_cpu'] / 100.0
    df['memory_utilization'] = df['percent_memory'] / 100.0
    
    # Memory efficiency (actual vs requested)
    df['memory_requested_mb'] = df['memory_requested'].apply(
        lambda x: x / 1024 if pd.notna(x) and x > 1000 else x
    )
    df['memory_efficiency'] = df['peak_rss'] / df['memory_requested_mb'].replace(0, np.nan)
    
    # Time efficiency
    df['time_efficiency'] = df['duration'] / df['time_requested'].replace(0, np.nan)
    
    # 3. I/O intensity (character I/O from trace)
    df['io_total'] = df['read_char'] + df['write_char']
    df['io_ratio'] = df['read_char'] / (df['write_char'] + 1)
    
    # 3b. Disk I/O intensity (from work directory scanning) - Convert to MB
    # Overwrite with MB values (original bytes values not needed after this)
    df['read_bytes'] = df['read_bytes'].fillna(0) / (1024 * 1024)  # Now in MB
    df['write_bytes'] = df['write_bytes'].fillna(0) / (1024 * 1024)  # Now in MB
    df['disk_io_total'] = df['read_bytes'] + df['write_bytes']  # MB
    df['disk_io_ratio'] = df['read_bytes'] / (df['write_bytes'] + 0.001)
    df['disk_intensity'] = df['disk_usage_mb'].fillna(0)  # MB
    
    # 3c. Trace I/O (from Nextflow trace files) - Convert to MB
    # Overwrite with MB values
    df['read_char'] = df['read_char'].fillna(0) / (1024 * 1024)  # Now in MB
    df['write_char'] = df['write_char'].fillna(0) / (1024 * 1024)  # Now in MB
    df['io_total'] = df['read_char'] + df['write_char']  # MB (model expects this)
    df['io_ratio'] = df['read_char'] / (df['write_char'] + 0.001)
    
    # 4. CPU-Memory correlation
    df['cpu_mem_product'] = df['percent_cpu'] * df['peak_rss']
    
    # 5. Size category encoding (for multi-scenario predictions)
    if 'data_size_tag' in df.columns:
        # Use the tag if available
        size_mapping = {'small': 0, 'medium': 1, 'large': 2, 'mixed': 1}
        df['size_category_encoded'] = df['data_size_tag'].map(size_mapping).fillna(1).astype(float)
    else:
        # Fallback: categorize by disk_usage_mb percentiles
        if 'disk_usage_mb' in df.columns:
            p33 = df['disk_usage_mb'].quantile(0.33)
            p67 = df['disk_usage_mb'].quantile(0.67)
            
            def categorize_size(disk_mb):
                if pd.isna(disk_mb):
                    return 1  # medium default
                elif disk_mb <= p33:
                    return 0  # small
                elif disk_mb <= p67:
                    return 1  # medium
                else:
                    return 2  # large
            
            df['size_category_encoded'] = df['disk_usage_mb'].apply(categorize_size)
    
    # ==================== Target Variables ====================
    
    # Memory prediction target (MB)
    df['target_memory_mb'] = df['peak_rss']
    
    # Time prediction target (seconds)
    df['target_duration_sec'] = df['duration']
    
    # CPU prediction target (number of cores)
    # Use RAW percent_cpu interpretation: percent_cpu / 100 = actual CPU cores used
    # Add 10% buffer for optimal allocation
    def estimate_cpus(row):
        percent_cpu = row['percent_cpu'] if pd.notna(row['percent_cpu']) else 0
        
        if percent_cpu > 0:
            # RAW CPU cores used (cumulative across all cores)
            actual_cores_used = percent_cpu / 100.0
            # Add 10% buffer and round up
            optimal_cpus = int(np.ceil(actual_cores_used * 1.1))
            return max(1, min(32, optimal_cpus))
        else:
            # Fallback to requested if available
            cpus_requested = row['cpus_requested'] if pd.notna(row['cpus_requested']) else 1
            return max(1, min(32, int(cpus_requested)))
    
    df['target_cpus'] = df.apply(estimate_cpus, axis=1)
    
    # Add per-GB normalization features for size-based extrapolation
    # These allow the model to learn resource usage patterns independent of data size
    df['memory_per_gb'] = df['peak_rss'] / (df['disk_usage_mb'] / 1000 + 0.001)
    df['time_per_gb'] = df['duration'] / (df['disk_usage_mb'] / 1000 + 0.001)
    df['cpu_per_gb'] = df['percent_cpu'] / (df['disk_usage_mb'] / 1000 + 0.001)
    
    # ==================== NEW: Enhanced Features for CPU Scaling ====================
    
    # 1. Log-scaled data size (captures diminishing returns in resource usage)
    df['log_disk_gb'] = np.log1p(df['disk_usage_mb'] / 1000)  # ln(1 + disk_gb)
    
    # 2. CPU-Data interaction (explicit scaling relationship)
    df['disk_cpu_interaction'] = (df['disk_usage_mb'] / 1000) * (df['percent_cpu'] / 100 + 0.001)
    
    # 3. I/O per CPU (captures I/O-bound vs CPU-bound processes)
    df['io_per_cpu'] = df['io_total'] / (df['percent_cpu'] / 100 + 1)
    
    # 4. Memory-CPU ratio (resource balance - identifies memory-bound vs CPU-bound)
    df['memory_cpu_ratio'] = df['memory_per_gb'] / (df['cpu_per_gb'] / 100 + 0.001)
    
    return df


def prepare_training_data(df: pd.DataFrame, target: str = 'target_memory_mb') -> tuple:
    """
    Prepare data for ML training.
    
    Returns:
        X: Feature matrix
        y: Target variable
        feature_names: List of feature column names
    """
    
    # Select features for training - EXACT match with what model expects
    # ALL models use SAME features for simplicity
    feature_columns = [
        'has_module',
        
        # Data size features (ALL in MB)
        'disk_intensity',           # disk_usage_mb
        'disk_io_total',            # read_bytes + write_bytes (work dir)
        'disk_io_ratio',            # read_bytes / write_bytes
        
        # Utilization metrics
        'cpu_utilization',
        'memory_utilization',
        
        # I/O features (trace files, in MB)
        'io_total',                 # read_char + write_char
        'io_ratio',                 # read_char / write_char
        
        # Interaction features
        'cpu_mem_product',
        
        # Size category encoding
        'size_category_encoded',
        
        # Per-GB normalization (for extrapolation)
        'memory_per_gb',
        'time_per_gb',
        'cpu_per_gb',
        
        # NEW: Enhanced features for better CPU scaling
        'log_disk_gb',              # Log-scaled data size
        'disk_cpu_interaction',     # CPU-data size scaling
        'io_per_cpu',               # I/O pressure per core
        'memory_cpu_ratio',         # Resource balance
    ]
    
    # Filter to rows with valid target
    df_clean = df[df[target].notna()].copy()
    
    for col in feature_columns:
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').fillna(0)
        else:
            # Feature not available, create as zeros
            df_clean[col] = 0
    
    # Ensure all features are numeric
    X = df_clean[feature_columns].apply(pd.to_numeric, errors='coerce').fillna(0)
    X = X.replace([np.inf, -np.inf], 0)
    y = pd.to_numeric(df_clean[target], errors='coerce').fillna(0)
    
    return X, y, feature_columns


def get_feature_statistics(df: pd.DataFrame) -> Dict:
    """
    Calculate statistics for feature understanding and model interpretability.
    """
    stats = {}
    
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    
    for col in numeric_cols:
        if col in ['target_memory_mb', 'target_duration_sec', 'target_cpus', 
                   'peak_rss', 'peak_vmem', 'duration', 'percent_cpu']:
            stats[col] = {
                'mean': df[col].mean(),
                'std': df[col].std(),
                'min': df[col].min(),
                'max': df[col].max(),
                'median': df[col].median(),
                'p95': df[col].quantile(0.95),
                'p99': df[col].quantile(0.99),
                'count': df[col].count(),
            }
    
    # Process name distribution
    if 'process_base' in df.columns:
        stats['process_distribution'] = df['process_base'].value_counts().head(20).to_dict()
      
    return stats
