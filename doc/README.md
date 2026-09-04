# Technical Documentation

## ML Model

**Algorithm**: Bayesian Ridge Regression

**Why Bayesian:**
- Provides uncertainty estimates (CV, confidence intervals)
- Dynamic safety margins based on uncertainty
- Better extrapolation with scenario-based priors
- Per-process models with automatic regularization

**Features (17):**
1. `has_module` - Process has module prefix
2. `disk_intensity` - Disk usage (MB)
3. `disk_io_total` - Read + write bytes (MB)
4. `disk_io_ratio` - Read/write ratio
5. `cpu_utilization` - CPU % / 100
6. `memory_utilization` - Memory % / 100
7. `io_total` - Trace I/O (MB)
8. `io_ratio` - Trace I/O ratio
9. `cpu_mem_product` - CPU × memory correlation
10. `size_category_encoded` - Small/medium/large
11. `memory_per_gb` - Memory efficiency
12. `time_per_gb` - Time efficiency
13. `cpu_per_gb` - CPU efficiency
14. `log_disk_gb` - Log-scaled data size (diminishing returns)
15. `disk_cpu_interaction` - CPU × data size (scaling)
16. `io_per_cpu` - I/O pressure per core
17. `memory_cpu_ratio` - Resource balance

**Training Configuration:**
- ≥30 samples: weak priors (data-driven)
- 10-30 samples: moderate priors
- <10 samples: strong regularization

**Prediction Output:**
- Base prediction
- Uncertainty (std dev)
- Coefficient of variation (CV)
- 95% confidence interval
- Dynamic safety margin (5-30% based on CV)
- Final prediction with safety margin

## API Endpoints

### POST /ml/train
Train Bayesian models on historical data.

```bash
curl -X POST http://localhost/ml/train \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"resource_limits":{"max_cpus":32,"max_memory_mb":131072,"max_duration_sec":86400}}'
```

### POST /ml/optimize-for-size
Get interactive optimization with uncertainty estimates.

```bash
curl -X POST http://localhost/ml/optimize-for-size \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "process_name": "BCFTOOLS_FILTER",
    "expected_disk_gb": 50.0,
    "priority": "balanced",
    "resource_limits": {"max_cpus": 32, "max_memory_mb": 65536, "max_duration_sec": 86400}
  }'
```

**Response includes:**
- Memory/time/CPU predictions with uncertainty
- 95% confidence intervals
- CV (coefficient of variation)
- Safety margins
- Nextflow config
- Warnings for extrapolation

### GET /ml/optimizations
Get all process optimizations with S/M/L scenarios.

```bash
curl "http://localhost/ml/optimizations" \
  -H "Authorization: Bearer $API_KEY"
```

### GET /ml/optimization/{process_name}
Get detailed historical statistics for a process.

```bash
curl "http://localhost/ml/optimization/BCFTOOLS_FILTER" \
  -H "Authorization: Bearer $API_KEY"
```

## Database Schema

**Key tables:**
- `workflowexecution` - Workflow runs (id, run_name, final_state, duration, start_time)
- `processexecution` - Process-level metrics (duration, peak_rss, percent_cpu, disk_usage_mb, etc.)
- `mlmodelmetadata` - Trained model info (process_name, resource_type, model_type, training_samples)
- `co2footprint` - Energy and CO2 emissions per process

## Resource Limits

**User-provided constraints prevent absurd predictions:**

**Defaults:**
- Max CPUs: 32 cores per task
- Max Memory: 128 GB per task
- Max Duration: 24 hours per task

**Enforcement:**
- Applied as hard caps on all predictions
- Prevents extreme extrapolation (e.g., 816 CPUs, 11 TB memory)
- Set in ML Training tab, optional override in Optimizations tab
- Warnings generated when predictions hit limits

## Troubleshooting

**Models not training:**
- Bayesian models train with ≥10 samples
- <10 samples: strong priors used (still trains)
- Check `docker logs gw-repo-prototype-api-1`

**Predictions have high uncertainty (CV > 100%):**
- Input data size far beyond historical range
- Model extrapolating beyond training data
- Collect more workflow runs with varying input sizes

**Predictions capped at limits:**
- Check resource limits in ML Training tab
- Increase limits if needed for your workflow
- Warnings show when capping occurs

**API connection refused:**
```bash
docker compose restart api
```

**Absurd predictions (TB memory, 500+ hours):**
- Resource limits not set or too high
- Extreme extrapolation (50 GB input for <1 GB training data)
- Set appropriate resource limits in ML Training tab
