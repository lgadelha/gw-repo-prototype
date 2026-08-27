# GW-RePO: Workflow Resource Profiler & Optimizer

Track Nextflow workflow resource usage and get ML-powered recommendations for local execution.

## Quick Start

```bash
./setup.sh
docker compose up -d
```

## Architecture

```
[ Nextflow Pipeline Execution ]
       │
       ├─────────────────────────┬─────────────────────────┬
       ▼                         ▼                         ▼                       
┌──────────────┐         ┌──────────────┐         ┌──────────────┐   
│     work     │         │   bco.json   │         │  trace.txt   │ 
│  directory   │         │  (optional)  │         │  (required)  │ 
└──────────────┘         └──────────────┘         └──────────────┘   
       │                         │                         │
       └─────────────────────────┴────────────┬────────────┘
                                              ▼
                                 [ ETL Script and API Client ]
                                              │
                                              ▼ (HTTP POST / Bearer Token)
┌────────────────────────────────────────────────────────────────┐
│                      Centralized Service                       │
│                                                                │
│                     ┌───────────────────┐                      │
│                     │    GW RePO API    │◄──────┐              │
│                     └───────┬───┬───────┘       │              │
│       (Reads/Writes)        │   │               │ (REST API    │
│       ┌─────────────────────┘   └───────┐       │  Queries)    │
│       ▼                                 ▼       │              │
│ ┌────────────┐                  ┌──────────────┐│              │
│ │ PostgreSQL │                  │ ML Resource  ││              │
│ │  Database  │                  │    Models    ││              │
│ └────────────┘                  └──────────────┘│              │
│                                                 │              │
│                     ┌───────────────────┐       │              │
│                     │ Streamlit UI App  ├───────┘              │
│                     │   (User Facing)   │                      │
│                     └───────────────────┘                      │
└────────────────────────────────────────────────────────────────┘
```

**Data Sources:**
- **work directory** - Disk usage, I/O bytes (via `--work-dir` scan)
- **bco.json** - Provenance data, input/output files (optional)
- **trace.txt** - Process metrics: CPU, memory, duration, I/O (required)

**Components:**
- **GW RePO API** - FastAPI backend (port 80)
- **PostgreSQL Database** - Workflow execution data (port 5432)
- **ML Resource Models** - Gradient Boosting predictors
- **Streamlit UI App** - Analytics dashboard (port 8501)

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

## View Dashboard

```bash
streamlit run ui/app.py
```

Open http://localhost:8501

### Dashboard Pages

#### 1. Dashboard

Enables quick overview of all workflow executions and, find resource-heavy processes

- Process resource utilization charts (CPU%, memory%, duration)
- Filter by process name
- Historical execution table with all metrics
- Generate optimized Nextflow config based on historical P95 values
- Download detailed process data as CSV

---

#### 2. Analytics

Helps to understand resource patterns per process and identification of bottlenecks

- Select process from dropdown
- Correlation plots:
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

#### 3. ML Training

Train resource prediction models here

- Train Gradient Boosting models on your historical data
- View model performance metrics:
  - R² score (variance explained)
  - RMSE (root mean square error)
  - MAE (mean absolute error)
  - Cross-validation scores
- Feature importance rankings (which features matter most)
- Model artifacts stored in `/code/models/`

**Requirements:** Minimum 10 samples per process for per-process models

---

#### 4. ML Predictions
Get resource recommendations for a specific process before running your new analysis
- Enter process name (e.g., `BCFTOOLS_FILTER`)
- Get predictions for SMALL, MEDIUM, LARGE dataset scenarios
- Predictions include:
  - Memory (MB) with P95 safety margin
  - CPU cores with P95 safety margin
  - Duration (seconds) with P95 safety margin
- Auto-generated Nextflow config snippet
- Download ready-to-use config file
- Shows if prediction uses per-process model or fallback

---

#### 5. Optimization
Get data-driven recommendations for all processes

**Features:**
- Lists all processes with historical data
- For each process:
  - Historical statistics (mean, std, min, max, median, P95, P99)
  - Recommended configuration (P95-based)
  - Process insights (CPU-bound, I/O-bound, etc.)
  - Energy and CO2 analysis (if available)
  - 3 scenario predictions (SMALL/MEDIUM/LARGE)
  - `is_fallback_model` flag (true if <10 samples)
- Filter by institute

---

#### 6. Model Performance
Monitor trained model quality before trusting predictions

- List all trained models (memory, time, CPU per process)
- Accuracy metrics comparison
- Feature importance visualizations
- Training sample counts
- Model timestamps
- Delete/retrain individual models

---

## ML Resource Prediction

### Algorithm

**Model:** Gradient Boosting Regressor (sklearn)

**Why Gradient Boosting:**
- Handles non-linear relationships (resource usage vs data size)
- Robust to outliers (some runs are anomalies)
- Provides feature importance (interpretability)
- Works well with tabular data (our feature set)

**Training:**
- 80/20 train/test split
- 5-fold cross-validation
- StandardScaler for feature normalization
- Models saved as `.pkl` files

**Prediction:**
- P95 safety margin (15% buffer for memory/time)
- Minimum 1 hour for time predictions
- CPU rounded to nearest core (1-32 range)

### Features Used (13 total)

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

### Per-Process vs Fallback Models

**Per-process model:** Trained on ≥10 samples of the same process (e.g., `BCFTOOLS_FILTER`)

**Fallback model:** Used when <10 samples, trained on ALL processes combined

**How it works:**
```
Process has 24 samples? → Use BCFTOOLS_FILTER model ✅
Process has 3 samples?  → Use fallback model ⚠️
```

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

## AI-assisted development

Parts of this codebase were written with AI coding assistants. All 
contributions were reviewed, tested and are maintained by the 
authors listed in CITATION.cff, who are solely responsible for the 
correctness of the code. No AI system is credited as an author.