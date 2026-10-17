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
│           Nextflow Pipeline Execution        │
│   ┌──────────────────────────────────────┐   │
│   │   tower {} — Nextflow's own core     │   │
│   │   Tower/Seqera-Platform HTTP client  │   │
│   └──────────────────────────────────────┘   │
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
`-c gwrepo.config`), set your institute / data-size tag / API key, and run:

```bash
export GWREPO_API_KEY=<your API_KEY from .env>
nextflow secrets set GWREPO_API_KEY "$GWREPO_API_KEY"
export GWREPO_INSTITUTE=DKFZ      # your institute (once per user)
export GWREPO_DATA_SIZE=small     # this run's data-size tag
# export GWREPO_URL=http://<host>:80   # if the API isn't on localhost:80
nextflow run <pipeline> -c gwrepo.config
```

Once the run finishes, `submit_run.py` runs the post-run importers (file
provenance + CO2 + per-sample MultiQC metrics) in one command. It declares its one dependency (`typer`) inline
([PEP 723](https://peps.python.org/pep-0723/)), so with
[`uv`](https://docs.astral.sh/uv/) installed there's no separate install step:

```bash
uv run submit_run.py --run-dir .
```

`--skip-lineage` / `--skip-co2` / `--skip-multiqc` leave one out (e.g. if
`nf-co2footprint` wasn't enabled for this run); `--dry-run` previews without POSTing.
Per-sample metrics come from [`multiqc-import`](multiqc-import/). See below for what
each importer does, or run them individually.

`--campaign <name>` (or `GWREPO_CAMPAIGN`) and `--annotate key=value` (repeatable) add
annotations to the run, e.g. to group the runs of one study. Numeric values are stored as
numbers. They can be run alone, after the fact, with all three `--skip-*` flags.

### 1. Live execution & resource metrics — `tower {}`

```groovy
tower {
    enabled     = true
    // GW-RePO API base URL + /<institute>/<dataSizeTag>, from GWREPO_URL / GWREPO_INSTITUTE / GWREPO_DATA_SIZE
    endpoint    = "${System.getenv('GWREPO_URL') ?: 'http://localhost:80'}/${System.getenv('GWREPO_INSTITUTE') ?: 'UNKNOWN'}/${System.getenv('GWREPO_DATA_SIZE') ?: 'mixed'}"
    accessToken = secrets.GWREPO_API_KEY             // or the GWREPO_API_KEY env var; any non-empty string
}
```

This is Nextflow's own built-in Tower/Seqera Platform client — the API implements
the receiving side of that protocol. Streamed live, as the run proceeds:

**Workflow** (`/trace/create`, `/begin`, `/complete`): run name, Nextflow version,
revision, start time, duration, final state, and what identifies the pipeline version:
`repository`, `commit_id` (both only for pipelines run from a repository, e.g.
`nextflow run nf-core/rnaseq -r 3.26.0`), `script_id` (Nextflow's hash of the main
script), `profile`, and `config_sha256`. `config_sha256` is a SHA-256 of the resolved
config, not the text itself, which can hold paths and secrets. It leaves out what changes
on every run (`runName`, `resume`) and the `tower {}` block, so runs with the same
configuration hash the same. At `/begin`, the pipeline's `params` are
stored as workflow annotations (`params.<name>`; nested maps such as nf-core's `genomes`
flattened with dots, null params skipped).

Annotations are read with `GET /workflows/{id}/annotations/` (optional `?key=`), added
with `POST /workflows/{id}/annotations/`, and filter runs with
`GET /workflows/?annotation.<key>=<value>`, e.g. `?annotation.campaign=casp2010` or
`?annotation.params.aligner=star_salmon` (repeat to combine).

**Per task** (`/trace/{id}/progress`, `/heartbeat`): process name, module, container,
exit status, requested vs. actual CPU / memory / time / disk, `%cpu`, `%mem`,
`peak_rss`, `peak_vmem`, `rchar` / `wchar`, `read_bytes` / `write_bytes`, realtime,
queue, tag, task name and attempt number, submit and complete times (queue wait = start −
submit), executor, the executor's job id and the CPU model. A run's final state is set
only at `/complete`; until then it is shown as RUNNING.

**Retries.** Every attempt of a task is its own row (Nextflow gives each retry a new task
id and work dir). Attempts of one task share the workflow and `task_name`
(`<process> (<tag or index>)`):
- `GET /processes/{id}/attempts` lists all attempts of that row's task, in start order;
- the `logical_task` SQL view has one row per task: number of attempts, whether it
  succeeded (`COMPLETED` or `CACHED`), and how many attempts were killed for exceeding
  memory or time (exit 137/140), with the most memory and time those attempts were given.

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

**Lineage graph.** The `prov_graph` SQL view links each task to the tasks whose outputs
it read, one edge per file:
- within a run, by path (exact, and works for files without a hash);
- across runs, by content hash, for inputs that no task of their own run produced, e.g.
  a published result of an earlier run used as input. Identical files from unrelated
  tasks of the same run (`versions.yml`, empty outputs) are not linked.

Queries on it:
- `GET /processes/{id}/ancestors` and `/descendants`: all tasks upstream or downstream,
  transitively (SQL functions `process_ancestors(id)` and `process_descendants(id)`);
- `GET /files/{sha256}/producers` and `/consumers`: every task output or input with that
  content, in any run;
- `GET /compare_runs?key=<annotation>[&process_name=<substring>]`: resource use (tasks,
  runs, mean/max peak RSS, mean realtime and %CPU) per value of a workflow annotation and
  process, e.g. `key=params.aligner` or `key=campaign`. Only finished attempts count.

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

The sidebar holds the API key, run filters (institute, pipeline, start date) and the
run selector. The selected run is kept in the URL (`?run=<workflow id>`), so links can
be shared. Data is cached for 30 s; **Refresh data** fetches it now. While a run in the
filter is still running, the page reloads every 15 s (**Auto-refresh**, on by default).

#### Runs

One row per run: pipeline, revision and commit, profile, institute, size tag, campaign,
state (RUNNING until the run completes), start, duration, tasks, retried tasks, failed
attempts, CPU-hours used and allocated, energy and CO2e. It also has charts of run
duration over time, CPU-hours per run, and the share of tasks retried or not succeeded.
Select one row and **Open run detail**, or several and **Compare runs**.

#### Run detail

The profile of the selected run:
- **KPIs:** wall time (with the time before the first task), tasks, CPU-hours used
  against allocated, peak task memory, median queue wait, data read and written, energy,
  CO2e and critical path, plus the executors and CPU models that ran the tasks.
- **Timeline:** one bar per task attempt, from start to start + realtime, coloured by
  process or status.
- **Concurrency:** running tasks, CPUs allocated and used, and memory allocated and
  peak, over time.
- **Processes:** per-process medians and maxima, with CPU, memory and time efficiency
  (used / requested), input and output size, CPU-hours and CO2e. Finished attempts only.
- **Cost:** a treemap of CPU-hours, energy or realtime by subworkflow and process, and a
  Pareto chart of the top processes.
- **I/O:** storage read and written per process, and output vs input size per task.
- **Right-sizing:** requested against used memory, CPU or time per task, flagging tasks
  under 25 % or over 90 % of their request.
- **Retries:** tasks retried or never succeeded, each attempt with its exit code and
  memory request.
- **Parameters & versions:** repository, revision, commit, profile, Nextflow version,
  script and config hashes, annotations, and pipeline params. The nf-core `genomes`
  catalog is hidden unless switched on.

#### Provenance

The lineage of the selected run (from `lineage-import`). A task picked in the sidebar is
kept in the URL as `?task=<id>`.
- **Process graph:** one node per process, coloured by CPU-hours, total realtime, max peak
  RSS or failed attempts. Edges are labelled with the number of files and bytes read; the
  critical path is outlined in red.
- **Task lineage:** the ancestors and/or descendants of the selected task, across runs too.
  Edges matched by content between runs are dashed.
- **Task inspector:** status, container, code checksum, resources requested vs used,
  attempts, parameters (`meta.*`, `task.ext.args`), input files with the task that
  produced each, and output files with how many tasks used them, here and in other runs.
- **File inspector:** search the run's files by name or SHA-256 prefix, then list every
  task, in any run, that produced or used the same content.
- **Critical path:** the longest chain of dependent tasks by Nextflow duration, its share of
  the task span (first task start to last task end), and the time before the first task.

#### Samples

For the selected run, the MultiQC General Statistics per sample (choose the metrics) next
to the resources used by each sample's tasks (tasks, CPU-hours, realtime, peak RSS, CO2e).
It also plots a resource against a sample metric, for this run or for every run in the
filter. Samples are linked to tasks through the task tag (nf-core's `meta.id`).

#### Energy & CO2

From nf-co2footprint (via `co2-import`), for the selected run:
- energy, CO2e, car and tree equivalents, carbon intensity, and the CPU model and power
  draw assumed;
- energy per process, split into CPU and memory;
- CO2e per sample;
- CO2e per run over time for the runs in the filter.

#### Process profile

One process (by short name, e.g. `STAR_ALIGN`) across all runs; kept in the URL as
`?process=`.
- **Scaling:** peak RSS, realtime, CPU-hours or %CPU against the task's input size or a
  sample metric, coloured by run, code checksum, revision, institute or size tag, with
  a linear fit and R².
- **Versions:** the same per code checksum and revision.
- **Requested vs used** memory across runs.
- **Failures:** attempts, kills (137/140) and retries per run.

#### Compare runs

Two or more runs against a baseline:
- **Parameters:** params and annotations that differ.
- **Versions:** revision, commit, profile, Nextflow version, script and config hashes, and
  which processes' code changed (code checksum).
- **Resources:** per-process change in median realtime, median peak RSS or CPU-hours,
  as a ratio to the baseline.
- **Samples:** a sample metric side by side for shared samples.
- **Annotation vs resources:** for one annotation (e.g. `campaign`, `params.aligner`) and
  a process, resource use per value across every run (MTCProv's
  `correlate_parameter_runtime`).

#### Process analytics: Dashboard

Combined view for process-level analytics and execution metrics, across all runs.

**Dashboard Tab:**
- Process resource utilization charts (CPU%, memory%, duration)
- Filter by process name
- Historical execution table with all metrics
- Summary statistics by process (finished attempts only)
- CPU utilization distribution (box plots)
- Input size & I/O metrics

---

#### Process analytics: Analytics

Helps to understand resource patterns per process and identification of bottlenecks

- Select process from dropdown
- Correlation plots against the task's input size (the sum of its input files' sizes,
  from `lineage-import`):
  - Memory vs Input Size (with R² correlation)
  - CPU Cores Used vs Input Size
  - Duration vs Input Size
  - Data Read/Written vs Input Size
  - Memory vs I/O intensity
- Process classification:
  - **Memory-Heavy**: Memory/Input ratio > 10×
  - **Input-Heavy**: Memory/Input ratio < 0.5×
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

## AI-assisted development

Parts of this codebase were written with AI coding assistants. All 
contributions were reviewed, tested and are maintained by the 
authors listed in CITATION.cff, who are solely responsible for the 
correctness of the code. No AI system is credited as an author.