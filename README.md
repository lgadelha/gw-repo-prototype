# GW-RePO: Workflow Resource Profiler & Optimizer

Track Nextflow workflow resource usage and get ML-powered recommendations for local execution.

## Quick Start

```bash
./setup.sh
docker compose up -d
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Nextflow Pipeline Execution                              │
└─────────────────────────────────────────────────────────────────────────────┘
       │
       ├───────────────┬───────────────┬───────────────┬──────────────┬
       ▼               ▼               ▼               ▼              ▼              
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│     work     │ │   bco.json   │ │  trace.txt   │ │nextflow.log  │ │co2footprint_*│
│  directory   │ │  (optional)  │ │  (required)  │ │  (optional)  │ │  (optional)  │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
       │               │               │               │              │
       └───────────────┴───────────────┴───────────────┴──────────────┘
                                              │
                                              ▼
                                 [ ETL Script and API Client ]
                                              │
                                              ▼ (HTTP POST / Bearer Token)
┌─────────────────────────────────────────────────────────────────────┐
│                         Centralized Service                         │
│                                                                     │
│                     ┌───────────────────┐                           │
│                     │    GW RePO API    │◄──────────────┐           │
│                     └─────────┬─────────┘               │           │
│       (Reads/Writes)          │                         │ (REST     │
│       ┌───────────────────────┘                         │  API)     │
│       ▼                                                 │           │
│ ┌────────────┐                  ┌──────────────────┐    │           │
│ │ PostgreSQL │                  │ ML Resource      │    │           │
│ │  Database  │                  │    Models        │    │           │
│ └────────────┘                  └──────────────────┘    │           │
│       ▲                                                 │           │
│       │                                                 │           │
│       │                  ┌───────────────────┐          │           │
│       └──────────────────│ Streamlit UI App  │◄─────────┘           │
│                          │ (User Facing)     │                      │
│                          │ ───────────────── │                      │
│                          │ 1. Workflow       │                      │
│                          │    Summaries      │                      │
│                          │ 2. Analytics &    │                      │
│                          │    Dashboard      │                      │
│                          │ 3. Bayesian       │                      │
│                          │    Modelling      │                      │
│                          │   - Training      │                      │
│                          │   - Optimizations │                      │
│                          │   - Bulk Opt.     │                      │
│                          └───────────────────┘                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Data Sources:**

- **work directory** *(optional, `--work-dir`)* — Disk usage, I/O bytes, peak memory from procfs
- **bco.json** *(optional)* — Provenance data, input/output files, parameters
- **trace.txt** *(required)* — Process metrics: CPU%, memory%, duration, I/O
- **nextflow.log** *(optional)* — Workflow-level: run_name, session_id, wall_clock, status
- **co2footprint_*.txt** *(optional)* — Energy (mWh), CO2e (mg), car km equivalent, tree sequestration time

**Components:**

- **GW RePO API** *(port 80)* — FastAPI backend with REST endpoints
- **PostgreSQL Database** *(port 5432)* — Persistent storage for workflows, processes, ML models
- **ML Resource Models** — Per-process Bayesian Ridge predictors with uncertainty estimation (memory, time, CPU)
- **Streamlit UI App** *(port 8501)* — 3-page analytics dashboard

**Runs entirely on your machine:**
- ✅ Private: All data stays local
- ✅ Self-contained: Docker containers only
- ✅ Works offline

## Submit Workflow Data

### 1. Configure Nextflow

Add to `nextflow.config`:

```groovy
trace {
    enabled = true
    fields = 'hash,process,name,status,duration,cpus,time,disk,memory,realtime,%cpu,%mem,peak_rss,peak_vmem,rchar,wchar'
}
```

### 2. Run Client

```bash
export API_KEY=<your-key-from-.env>
python client/client.py <pipeline_info_dir> --work-dir <work_dir>
```

### 3. What Gets Extracted

**From execution trace (`execution_trace_*.txt`):**
- `process_name` - Full process identifier
- `duration` - Actual runtime (seconds)
- `cpus_requested` - Requested CPU cores
- `memory_requested` - Requested memory
- `time_requested` - Requested time limit
- `disk_requested` - Requested disk space
- `percent_cpu` - Actual CPU utilization (%)
- `percent_memory` - Actual memory utilization (%)
- `peak_rss` - Peak resident memory (MB)
- `peak_vmem` - Peak virtual memory (MB)
- `rchar` - Characters read (bytes)
- `wchar` - Characters written (bytes)
- `realtime` - Wall clock time

**From work directory scanning (`--work-dir`):**
- `disk_usage_mb` - Total disk space used by task (MB)
- `read_bytes` - Bytes read from disk during execution
- `write_bytes` - Bytes written to disk during execution
- `peak_vmem_mb` - Peak virtual memory from procfs (MB)
- `peak_rss_mb` - Peak resident memory from procfs (MB)

**From BCO provenance (`manifest_*.bco.json`):** *(optional)*
- Input file paths and hashes
- Output file paths and hashes
- Parameter inputs
- Workflow structure

**Privacy:** File paths are stored for provenance only. ML models use numerical metrics only.

### Module Name Normalization

Process names are automatically normalized using nf-core module cache:

**Examples:**
- `BCFTOOLS_REHEADER_1`, `BCFTOOLS_REHEADER_2` → `BCFTOOLS_REHEADER`
- `BCFTOOLS_REHEADER_TP_BASE` → `BCFTOOLS_REHEADER`
- `TABIX_TABIX_2` → `TABIX_TABIX`
- `BCFTOOLS_FILTER_QUERY_FP` → `BCFTOOLS_FILTER`

**Benefits:**
- ✅ Consistent naming across all views
- ✅ Aggregates metrics from multiple runs with different suffixes
- ✅ ML models trained on consolidated data per module
- ✅ Cleaner optimization recommendations

**Migration:** Run `python scripts/migrate_normalize_modules.py` to normalize existing data.

## View Dashboard

```bash
streamlit run ui/app.py
```

Open http://localhost:8501

### Dashboard Pages

#### 1. Workflow Summaries

Overview of workflow-level execution metrics and visualizations

- Total workflow runs count
- Workflow execution table with key metrics
- Visualizations:
  - Workflow status distribution (pie chart)
  - Duration distribution (histogram)
  - Duration over time (line chart)
  - Data size tag distribution (bar chart)
  - Duration by data size tag (box plot)

---

#### 2. Analytics & Dashboard

Combined view for process-level analytics and execution metrics

**Dashboard Tab:**
- Process resource utilization charts (CPU%, memory%, duration)
- Filter by process name
- Historical execution table with all metrics
- Summary statistics by process
- CPU utilization distribution (box plots)
- Disk I/O & storage metrics

**Analytics Tab:**
- Correlation plots per process:
  - Memory vs Disk Size (with R² correlation)
  - CPU Cores Used vs Disk Size
  - Duration vs Disk Size
  - Data Read/Written vs Disk Size
  - Memory vs I/O intensity
- Process classification:
  - **Memory-Heavy**: Memory/Disk ratio > 10×
  - **Disk-Heavy**: Memory/Disk ratio < 0.5×
  - **I/O-Intensive**: I/O intensity > 5×
  - **Compute-Intensive**: I/O intensity < 0.5×
- Toggle log scale for better visualization

---

#### 3. Bayesian Modelling

Train Bayesian models and get ML-powered recommendations with uncertainty estimates

**Training Tab:**
- Train Bayesian Ridge models on your historical data
- Set resource limits (max CPUs, memory, duration per task)
- View training results:
  - Processes with trained models
  - Processes needing more data (<10 samples)
- Enhanced features for better CPU scaling:
  - Log-scaled data size
  - CPU-data interaction terms
  - I/O per CPU ratio
  - Memory-CPU balance

**Optimizations Tab:**
- Interactive resource optimizer with uncertainty estimates
- Select process and enter data size with visual ruler
- See historical range and your position
- Get predictions with:
  - 95% confidence intervals
  - Coefficient of variation (CV) for uncertainty
  - Dynamic safety margins based on uncertainty
- Visualize predictions with uncertainty plots
- Resource limits enforcement (from training tab)
- Download Nextflow config

**Bulk Optimizations Tab:**
- Generate configs for ALL trained processes at once
- Choose data size strategy:
  - Use historical average per process
  - Use historical maximum per process
  - Custom size (same for all)
- Priority modes: balanced, cost, performance
- Sanity caps prevent absurd predictions
- Download combined Nextflow config

---

## ML Resource Prediction

### Algorithm

**Model:** Bayesian Ridge Regression (sklearn)

**Why Bayesian Ridge:**
- Provides uncertainty estimates (CV, confidence intervals)
- Handles small datasets with scenario-based priors
- Dynamic safety margins based on uncertainty
- Better extrapolation beyond training data
- Per-process models with automatic regularization

**Training:**
- Scenario-based priors:
  - ≥30 runs: weak priors (data-driven)
  - 10-30 runs: moderate priors
  - <10 runs: strong regularization
- StandardScaler for feature normalization
- Models saved as `_bayesian.pkl` files

**Prediction:**
- 95% confidence intervals for all predictions
- Coefficient of variation (CV) for uncertainty
- Dynamic safety margins:
  - CV < 10%: 5% margin (high confidence)
  - CV 10-20%: 15% margin (medium confidence)
  - CV > 20%: 30% margin (low confidence)
- Minimum 1 core for CPU, 256 MB for memory, 1 hour for time
- User-provided resource limits enforced as hard caps

### Features Used (14 total)

| Feature | Description | Why It Matters |
|---------|-------------|----------------|
| `has_module` | 1 if process has module prefix | Distinguishes tool vs custom script |
| `disk_intensity` | Disk usage in MB | Direct measure of data size |
| `disk_io_total` | Read + write bytes (MB) | I/O volume affects runtime |
| `disk_io_ratio` | Read/write ratio | Read-heavy vs write-heavy patterns |
| `cpu_utilization` | CPU % / 100 | How much CPU the process uses |
| `memory_utilization` | Memory % / 100 | How much memory the process uses |
| `io_total` | Trace I/O (rchar+wchar in MB) | Nextflow-reported I/O |
| `io_ratio` | Trace I/O ratio | Read/write pattern from trace |
| `cpu_mem_product` | CPU × memory correlation | Processes that use both heavily |
| `size_category_encoded` | 0=small, 1=medium, 2=large | Dataset size category |
| `memory_per_gb` | Memory efficiency (MB per GB data) | Normalized memory usage |
| `time_per_gb` | Time efficiency (sec per GB data) | Normalized runtime |
| `cpu_per_gb` | CPU efficiency (cores per GB data) | Normalized CPU usage |
| `log_disk_gb` | Log-scaled data size | Captures diminishing returns |
| `disk_cpu_interaction` | CPU × data size | Explicit scaling relationship |
| `io_per_cpu` | I/O per core | I/O pressure per CPU |
| `memory_cpu_ratio` | Memory/CPU balance | Resource balance indicator |

### Per-Process Models

**Each process gets its own model** trained only on its historical data:
- Automatically uses normalized process names (e.g., `BCFTOOLS_FILTER`)
- Aggregates runs from all variants (`_1`, `_2`, etc.)
- Strong priors for processes with <10 samples prevent overfitting
- No fallback model needed - Bayesian priors handle low-sample cases

**How it works:**
```
BCFTOOLS_FILTER has 24 samples? → Train BCFTOOLS_FILTER model ✅
BCFTOOLS_FILTER has 3 samples?  → Strong priors, still train ✅
```

### Resource Limits

**Set workflow-level resource constraints:**
- Max CPUs per task (default: 32)
- Max memory per task (default: 128 GB)
- Max duration per task (default: 24 hours)

**Enforcement:**
- Applied as hard caps on all predictions
- Prevents absurd extrapolation (e.g., 816 CPUs, 11 TB memory)
- Configurable per workflow in ML Training tab
- Optional override per prediction in Optimizations tab

## API Endpoints

### POST /ml/train
Train models on historical data.

```bash
curl -X POST http://localhost/ml/train \
  -H "Authorization: Bearer $API_KEY"
```

### GET /ml/predict
Get predictions for a process.

```bash
curl "http://localhost/ml/predict?process_name=BCFTOOLS_FILTER" \
  -H "Authorization: Bearer $API_KEY"
```

### GET /ml/optimizations
Get all process optimizations.

```bash
curl "http://localhost/ml/optimizations" \
  -H "Authorization: Bearer $API_KEY"
```

## Data Management

**View data:**
```bash
docker compose exec db psql -U postgres -d gw_repo -c "SELECT COUNT(*) FROM processexecution;"
```

**Backup:**
```bash
docker compose exec db pg_dump -U postgres gw_repo > backup.sql
```

**Reset:**
```bash
docker compose down -v  # Deletes all data
rm .env
./setup.sh              # Fresh start
```

## Configuration

All settings in `.env`:
- `API_KEY`: Authentication
- `DATABASE_URL`: PostgreSQL connection
- `API_BASE_URL`: API endpoint

---

**Full documentation**: `doc/README.md`
