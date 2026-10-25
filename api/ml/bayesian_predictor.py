"""
Bayesian Linear Regression for resource prediction.
Uses SAME process name normalization as existing code.
"""

from sklearn.linear_model import BayesianRidge
from sklearn.preprocessing import StandardScaler
from scipy.stats import norm
import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime, timezone


class BayesianResourcePredictor:
    """
    Per-process Bayesian predictor.
    Aggregates runs by normalized process name (unchanged from current).
    """
    
    def __init__(self, model_dir: str = "/code/models"):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        
        # Per-process models: {process_name: {resource_type: BayesianRidge}}
        self.models: Dict[str, Dict[str, BayesianRidge]] = {}
        
        # Per-process scalers
        self.scalers: Dict[str, Dict[str, StandardScaler]] = {}
        
        # Feature columns: only what is known before a task runs. Features derived
        # from the task's own peak RSS, CPU use, duration or I/O leak the targets.
        self.feature_cols = [
            'disk_intensity',           # Task input size (MB)
            'log_disk_gb',              # Log-scaled input size (diminishing returns)
            'log_cpus',                 # Log of the CPUs requested (time falls with more)
        ]
    
    def train(self, process_name: str, historical_data: pd.DataFrame) -> Dict:
        """
        Train Bayesian model for ONE normalized process.
        
        Args:
            process_name: ALREADY NORMALIZED (e.g., "BCFTOOLS_FILTER")
            historical_data: ALL runs for this process (including _1, _2 variants)
        
        Returns:
            Dictionary with training results
        """
        n_samples = len(historical_data)
        
        # Scenario-based model configuration
        if n_samples >= 30:
            # Full Bayesian - weak priors (data-driven)
            model_params = {
                'alpha_1': 1e-6, 'alpha_2': 1e-6,
                'lambda_1': 1e-6, 'lambda_2': 1e-6,
                'compute_score': True
            }
        elif n_samples >= 10:
            # Medium data - moderate priors
            model_params = {
                'alpha_1': 1e-4, 'alpha_2': 1e-4,
                'lambda_1': 1e-4, 'lambda_2': 1e-4,
                'compute_score': True
            }
        else:
            # Few samples - strong priors (regularization)
            model_params = {
                'alpha_1': 1e-2, 'alpha_2': 1e-2,
                'lambda_1': 1e-2, 'lambda_2': 1e-2,
                'compute_score': True
            }
        
        # Prepare features
        X = historical_data[self.feature_cols].values
        y_memory = historical_data['peak_rss_mb'].values.astype(float)
        y_time = historical_data['duration'].values.astype(float)
        
        # Remove rows with invalid targets, and rows with missing features (tasks
        # without an input size: their run had no lineage-import)
        valid_mask = (
            (~np.isnan(X.astype(float)).any(axis=1)) &
            (y_memory > 0) & 
            (y_time > 0) & 
            (~np.isnan(y_memory)) & 
            (~np.isnan(y_time))
        )
        X_valid = X[valid_mask]
        y_memory_valid = y_memory[valid_mask]
        y_time_valid = y_time[valid_mask]
        
        if len(X_valid) < 10:
            return {
                'error': f'Insufficient training data: {len(X_valid)} samples',
                'success': False
            }
        
        # Scale features
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_valid)
        
        # Train models for each resource
        models = {}
        
        for resource_type, y in [
            ('memory', y_memory_valid),
            ('time', y_time_valid)
        ]:
            model = BayesianRidge(**model_params)
            model.fit(X_scaled, y)
            models[resource_type] = model
        
        # Store models (one scaler for all three, as load_model and predict expect)
        self.models[process_name] = models
        self.scalers[process_name] = scaler
        
        # Save to disk
        for resource_type, model in models.items():
            model_path = str(self.model_dir / f"{process_name}_{resource_type}_bayesian.pkl")
            joblib.dump(model, model_path)
        
        scaler_path = str(self.model_dir / f"{process_name}_scaler_bayesian.pkl")
        joblib.dump(scaler, scaler_path)
        
        return {
            'process_name': process_name,
            'n_samples': len(X_valid),
            'models': list(models.keys()),
            'model_type': 'BayesianRidge',
            'success': True
        }
    
    def load_model(self, process_name: str) -> bool:
        """Load pre-trained models from disk."""
        model_files = {
            'memory': self.model_dir / f"{process_name}_memory_bayesian.pkl",
            'time': self.model_dir / f"{process_name}_time_bayesian.pkl",
            'scaler': self.model_dir / f"{process_name}_scaler_bayesian.pkl"
        }
        
        if not all(p.exists() for p in model_files.values()):
            return False
        
        self.models[process_name] = {
            'memory': joblib.load(model_files['memory']),
            'time': joblib.load(model_files['time'])
        }
        self.scalers[process_name] = joblib.load(model_files['scaler'])
        
        # Models trained with another feature set need retraining
        if getattr(self.scalers[process_name], 'n_features_in_', None) != len(self.feature_cols):
            del self.models[process_name], self.scalers[process_name]
            return False
        return True
    
    def predict(self, process_name: str, features: dict, quantile: float = 0.95) -> dict:
        """
        Predict with uncertainty estimates. The recommended setting is the given
        quantile of the predictive distribution, so about 1 - quantile of tasks of
        this size would need more.
        
        Args:
            process_name: Normalized process name
            features: Dictionary with feature values
            quantile: Quantile of the predictive distribution to recommend
        
        Returns:
        {
            'memory': {
                'prediction': 2048,
                'uncertainty': 245,  # std dev
                'cv': 0.12,  # coefficient of variation
                'confidence_interval': {'lower': 1568, 'upper': 2528, 'confidence': 0.95},
                'quantile': 0.95,
                'final_with_safety': 2451  # the quantile of the predictive distribution
            },
            'time': {...}
        }
        CPUs come from cpu_setting, not a model.
        """
        if process_name not in self.models:
            if not self.load_model(process_name):
                raise ValueError(f"No trained model found for process: {process_name}")
        
        # Prepare feature vector
        X = np.array([[features[col] for col in self.feature_cols]])
        scaler = self.scalers[process_name]
        X_scaled = scaler.transform(X)
        
        predictions = {}
        
        for resource_type in ['memory', 'time']:
            model = self.models[process_name][resource_type]
            
            # Bayesian prediction with uncertainty
            pred, std = model.predict(X_scaled, return_std=True)
            
            # Coefficient of variation (uncertainty metric)
            cv = std[0] / abs(pred[0]) if pred[0] != 0 else 1.0
            
            # Recommended setting: the quantile of the predictive distribution
            z = norm.ppf(quantile)
            
            # Ensure non-negative predictions (memory/time cannot be negative)
            pred_value = max(0.0, pred[0])
            ci_lower = max(0, pred_value - 1.96 * std[0])
            ci_upper = pred_value + 1.96 * std[0]
            pred_with_safety = max(0, pred_value + z * std[0])
            
            predictions[resource_type] = {
                'prediction': float(pred_value),
                'uncertainty': float(std[0]),
                'cv': float(cv),
                'confidence_interval': {
                    'lower': float(ci_lower),
                    'upper': float(ci_upper),
                    'confidence': 0.95
                },
                'quantile': quantile,
                'final_with_safety': float(pred_with_safety)
            }
        
        return predictions
    
    def train_all_process_models(
        self,
        df: pd.DataFrame,
        institute_id: Optional[str] = None
    ) -> Dict:
        """
        Train per-process models for all processes in the dataset.
        
        Args:
            df: DataFrame with all process data (module_name column already normalized)
            institute_id: Optional filter by institute
        
        Returns:
            Dictionary with training results for all processes
        """
        results = {
            'per_process': {},
            'fallback': {},
            'summary': {
                'total_processes': 0,
                'processes_with_models': 0,
                'processes_using_fallback': 0,
            }
        }
        
        # Filter by institute if specified
        if institute_id:
            df = df[df['institute_id'] == institute_id].copy()
        
        # Group by normalized process name (module_name)
        grouped = df.groupby('module_name')
        results['summary']['total_processes'] = len(grouped)
        
        # Train per-process models
        for process_name, group_df in grouped:
            if len(group_df) < 10:
                # Not enough samples - will use fallback
                results['per_process'][process_name] = {
                    'status': 'fallback',
                    'samples': len(group_df),
                    'reason': 'Insufficient samples (<10)'
                }
                results['summary']['processes_using_fallback'] += 1
                continue
            
            # Train Bayesian model
            result = self.train(process_name, group_df)
            
            if result.get('success'):
                results['per_process'][process_name] = {
                    'status': 'trained',
                    'samples': result['n_samples'],
                    'models': result['models'],
                    'model_type': 'BayesianRidge'
                }
                results['summary']['processes_with_models'] += 1
            else:
                results['per_process'][process_name] = {
                    'status': 'failed',
                    'samples': len(group_df),
                    'error': result.get('error', 'Unknown error')
                }
        
        return results


def size_spread(sizes: pd.Series) -> float:
    """(p90 - p10) / median of input sizes: below 0.2 they barely vary, too little to
    fit how usage grows with size."""
    sizes = sizes.dropna()
    if sizes.empty:
        return 0.0
    return float((sizes.quantile(0.9) - sizes.quantile(0.1)) / max(sizes.median(), 0.001))


def history_predictions(df: pd.DataFrame, quantile: float = 0.95) -> dict:
    """
    Settings from a process's past usage, without a size model: the given quantile
    of its completed attempts' peak RSS and duration, and cpu_setting. Same shape as
    BayesianResourcePredictor.predict; 'prediction' is the median and the interval
    the 2.5-97.5 % range of past use.
    """
    predictions = {}
    for resource_type, values in [('memory', df['peak_rss_mb']),
                                  ('time', df['duration'])]:
        predictions[resource_type] = _usage_quantile(values, quantile, resource_type)
    predictions['cpu'] = cpu_setting(df, quantile)
    return predictions


def cpu_setting(df: pd.DataFrame, quantile: float = 0.95) -> dict:
    """
    CPUs for a process from its completed attempts: the given quantile of the CPUs
    they used, rounded up, but no more than the most they requested. Usage can't
    exceed the request, so when it reached it (quantile of used / requested ≥ 0.9)
    `capped_by_request` says the process might use more if given more; not for a
    request of 1 CPU, which single-threaded tools fill anyway.
    """
    setting = _usage_quantile(df['percent_cpu'] / 100.0, quantile, 'cpu')
    used = setting['final_with_safety']
    requested = pd.to_numeric(df['cpus_requested'], errors='coerce')
    requested = requested[requested > 0]
    cpus = int(np.ceil(used))
    if not requested.empty:
        cpus = min(cpus, int(requested.max()))
        ratio = (pd.to_numeric(df['percent_cpu'], errors='coerce') / 100.0 / df['cpus_requested']).dropna()
        setting['capped_by_request'] = bool(requested.max() > 1 and not ratio.empty
                                            and ratio.quantile(quantile) >= 0.9)
        setting['requested'] = int(requested.max())
    setting['final_with_safety'] = float(max(1, cpus))
    return setting


def _usage_quantile(values: pd.Series, quantile: float, resource_type: str) -> dict:
    """The quantile of positive past usage, with its median and 2.5-97.5 % range."""
    values = pd.to_numeric(values, errors='coerce').dropna()
    values = values[values > 0]
    if values.empty:
        raise ValueError(f"No recorded {resource_type} usage")
    mean, std = float(values.mean()), float(values.std(ddof=0))
    return {
        'prediction': float(values.median()),
        'uncertainty': std,
        'cv': std / mean if mean else 1.0,
        'confidence_interval': {
            'lower': float(values.quantile(0.025)),
            'upper': float(values.quantile(0.975)),
            'confidence': 0.95
        },
        'quantile': quantile,
        'final_with_safety': float(values.quantile(quantile)),
        'samples': int(len(values))
    }
