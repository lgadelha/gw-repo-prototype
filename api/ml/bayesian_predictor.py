"""
Bayesian Linear Regression for resource prediction.
Uses SAME process name normalization as existing code.
"""

from sklearn.linear_model import BayesianRidge
from sklearn.preprocessing import StandardScaler
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
        
        # Feature columns - enhanced for better CPU scaling
        self.feature_cols = [
            'disk_intensity',
            'cpu_utilization',
            'memory_utilization',
            'io_total',
            'io_ratio',
            'cpu_mem_product',
            'memory_per_gb',
            'time_per_gb',
            'cpu_per_gb',
            'size_category_encoded',
            # New features for better CPU scaling
            'log_disk_gb',              # Log-scaled data size (diminishing returns)
            'disk_cpu_interaction',     # Explicit CPU-data size scaling
            'io_per_cpu',               # I/O pressure per core
            'memory_cpu_ratio'          # Resource balance
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
        y_cpu = (historical_data['percent_cpu'].values / 100.0).astype(float)  # Normalize to 0-1
        
        # Remove rows with invalid targets
        valid_mask = (
            (y_memory > 0) & 
            (y_time > 0) & 
            (~np.isnan(y_memory)) & 
            (~np.isnan(y_time)) & 
            (~np.isnan(y_cpu))
        )
        X_valid = X[valid_mask]
        y_memory_valid = y_memory[valid_mask]
        y_time_valid = y_time[valid_mask]
        y_cpu_valid = y_cpu[valid_mask]
        
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
        scalers = {}
        
        for resource_type, y in [
            ('memory', y_memory_valid),
            ('time', y_time_valid),
            ('cpu', y_cpu_valid)
        ]:
            model = BayesianRidge(**model_params)
            model.fit(X_scaled, y)
            models[resource_type] = model
        
        scalers = {resource_type: scaler for resource_type in ['memory', 'time', 'cpu']}
        
        # Store models
        self.models[process_name] = models
        self.scalers[process_name] = scalers
        
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
            'cpu': self.model_dir / f"{process_name}_cpu_bayesian.pkl",
            'scaler': self.model_dir / f"{process_name}_scaler_bayesian.pkl"
        }
        
        if not all(p.exists() for p in model_files.values()):
            return False
        
        self.models[process_name] = {
            'memory': joblib.load(model_files['memory']),
            'time': joblib.load(model_files['time']),
            'cpu': joblib.load(model_files['cpu'])
        }
        self.scalers[process_name] = joblib.load(model_files['scaler'])
        
        return True
    
    def predict(self, process_name: str, features: dict) -> dict:
        """
        Predict with uncertainty estimates.
        
        Args:
            process_name: Normalized process name
            features: Dictionary with feature values
        
        Returns:
        {
            'memory': {
                'prediction': 2048,
                'uncertainty': 245,  # std dev
                'cv': 0.12,  # coefficient of variation
                'confidence_interval': {'lower': 1568, 'upper': 2528, 'confidence': 0.95},
                'safety_margin': 0.10,
                'final_with_safety': 2253
            },
            'time': {...},
            'cpu': {...}
        }
        """
        if process_name not in self.models:
            if not self.load_model(process_name):
                raise ValueError(f"No trained model found for process: {process_name}")
        
        # Prepare feature vector
        X = np.array([[features[col] for col in self.feature_cols]])
        scaler = self.scalers[process_name]
        X_scaled = scaler.transform(X)
        
        predictions = {}
        
        for resource_type in ['memory', 'time', 'cpu']:
            model = self.models[process_name][resource_type]
            
            # Bayesian prediction with uncertainty
            pred, std = model.predict(X_scaled, return_std=True)
            
            # Coefficient of variation (uncertainty metric)
            cv = std[0] / abs(pred[0]) if pred[0] != 0 else 1.0
            
            # Dynamic safety margin based on uncertainty
            if cv < 0.1:
                safety_margin = 0.05  # High confidence: 5%
            elif cv < 0.2:
                safety_margin = 0.15  # Medium confidence: 15%
            else:
                safety_margin = 0.30  # Low confidence: 30%
            
            # For CPU, ensure minimum 1 core (CPUs cannot be 0 or negative)
            if resource_type == 'cpu':
                # Ensure raw prediction is at least 1 core
                pred_value = max(1.0, pred[0])
                ci_lower = max(1.0, pred_value - 1.96 * std[0])
                ci_upper = max(1.0, pred_value + 1.96 * std[0])
                pred_with_safety = max(1, round(pred_value * (1 + safety_margin)))
            else:
                # Ensure non-negative predictions (memory/time cannot be negative)
                pred_value = max(0.0, pred[0])
                ci_lower = max(0, pred_value - 1.96 * std[0])
                ci_upper = pred_value + 1.96 * std[0]
                pred_with_safety = max(0, pred_value * (1 + safety_margin))
            
            predictions[resource_type] = {
                'prediction': float(pred_value),
                'uncertainty': float(std[0]),
                'cv': float(cv),
                'confidence_interval': {
                    'lower': float(ci_lower),
                    'upper': float(ci_upper),
                    'confidence': 0.95
                },
                'safety_margin': safety_margin,
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
