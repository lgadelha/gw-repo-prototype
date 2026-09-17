# GW-RePO: Workflow Resource Profiler & Optimizer

Track Nextflow workflow resource usage and get ML-powered recommendations for local execution.

## Quick Start

```bash
./setup.sh
docker compose up -d
```

## Architecture

```
┌──────────────────────────────────────────────┐
│           Nextflow Pipeline Execution         │
│   ┌──────────────────────────────────────┐    │
│   │   tower {} — Nextflow's own core      │    │
│   │   Tower/Seqera-Platform HTTP client   │    │
│   └──────────────────────────────────────┘    │
└───────────────────────┬──────────────────────┘
                        │  live, as the run proceeds
                        ▼  (HTTP POST/PUT / Bearer or Basic auth)
┌────────────────────────────────────────────────────────────────┐
│                      Centralized Service                       │
│                                                                │
│                     ┌───────────────────┐                      │
│                     │    GW RePO API    │◄──────┐              │
│                     │  (incl. tower     │       │              │
│                     │  emulation routes)│       │              │
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
                        ▲
                        │  post-run (files/records already on disk)
        lineage-import/submit_lineage.py, co2-import/submit_co2.py
```

**Data source:** no custom Nextflow plugin — three things Nextflow already ships,
each covering one concern, with no overlap:
- **`tower {}`** (built into Nextflow core) streams workflow metadata and per-task
  resource metrics (CPU, memory, duration, I/O) to the API live, as the pipeline
  runs. The API implements the receiving side of Nextflow's own Tower/Seqera
  Platform protocol — no post-hoc log/trace parsing.
- **`lineage {}`** (built into Nextflow core, `lineage.enabled = true`) records
  input/output file paths per task to a local store, read after the run by
  [`lineage-import`](lineage-import/) to compute and submit a SHA-256 of each
  file's content (`nf-lineage`'s own checksum is a metadata hash, not content).
- **`nf-co2footprint`** (optional plugin), read after the run by
  [`co2-import`](co2-import/) — see below.

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

No plugin to install. Copy [`gwrepo.config`](gwrepo.config) into your pipeline (or
`-c gwrepo.config`), fill in your institute / data-size tag / API key, and run:

```bash
export GWREPO_API_KEY=<your API_KEY from .env>
nextflow run <pipeline> -c gwrepo.config
```

Once the run finishes, `submit_run.py` runs both post-run importers (file
provenance + CO2) in one command. It declares its one dependency (`typer`) inline
([PEP 723](https://peps.python.org/pep-0723/)), so with
[`uv`](https://docs.astral.sh/uv/) installed there's no separate install step:

```bash
uv run submit_run.py --run-dir .
```

`--skip-co2` / `--skip-lineage` run just one (e.g. if `nf-co2footprint` wasn't
enabled for this run); `--dry-run` previews without POSTing. See below for what
each importer does, or run them individually.

### 1. Live execution & resource metrics — `tower {}`

```groovy
tower {
    enabled     = true
    endpoint    = 'http://localhost:80/DKFZ/mixed'   // GW-RePO API base URL + /<institute>/<dataSizeTag>
    accessToken = secrets.GWREPO_API_KEY             // or the GWREPO_API_KEY env var; any non-empty string
}
```

This is Nextflow's own built-in Tower/Seqera Platform client — the API implements
the receiving side of that protocol. Streamed live, as the run proceeds:

**Workflow** (`/trace/create`, `/begin`, `/complete`): run name, Nextflow version,
revision, start time, duration, final state.

**Per task** (`/trace/{id}/progress`, `/heartbeat`): process name, module, container,
exit status, requested vs. actual CPU / memory / time / disk, `%cpu`, `%mem`,
`peak_rss`, `peak_vmem`, `rchar` / `wchar`, `read_bytes` / `write_bytes`, realtime,
queue.

### 2. File provenance — `lineage {}` + `lineage-import`

```groovy
lineage {
    enabled = true
}
```

```bash
nextflow run <pipeline> -c gwrepo.config && python lineage-import/submit_lineage.py
```

Input and output file paths per task, each with a SHA-256 of its content — computed
by the importer itself after the run (`nf-lineage`'s own checksum is a metadata hash,
not content). See [`lineage-import/README.md`](lineage-import/README.md).

### 3. CO2 footprint (optional)

CO2 and energy data comes from the [`nf-co2footprint`](https://nextflow-io.github.io/nf-co2footprint/)
plugin, imported after the run by a small script (`nf-co2footprint` writes its files
during Nextflow shutdown, too late to read live):

```groovy
plugins {
    id 'nf-co2footprint'
}
co2footprint {
    location = 'DE'
    summary { enabled = true }   // needed for the car-km / tree-sequestration figures
}
```

```bash
nextflow run <pipeline> -c gwrepo.config && python co2-import/submit_co2.py
```

See [`co2-import/README.md`](co2-import/README.md).

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