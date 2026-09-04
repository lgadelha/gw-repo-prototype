# co2-import

Post-run CO2 importer for GW-RePO. Reads [`nf-co2footprint`](https://nextflow-io.github.io/nf-co2footprint/)'s
provenance file and POSTs per-task CO2 rows plus a workflow summary to the GW-RePO API,
attaching them to the rows the [`nf-gwrepo`](../plugin/nf-gwrepo/) plugin created during
the run.

## Why this is a separate step

`nf-co2footprint` writes its output files during Nextflow's *plugin-shutdown* phase —
after every observer (including `nf-gwrepo`) has already finished. Nothing running inside
Nextflow can reliably read them. So CO2 ingestion runs afterwards, as its own process.

## Usage

```bash
export GWREPO_API_KEY=<your API_KEY from .env>

nextflow run <pipeline> -c gwrepo.config      # nf-gwrepo + nf-co2footprint both enabled
python co2-import/submit_co2.py               # from the same directory
```

`submit_co2.py` needs, in the run directory:

- **`.gwrepo-run.json`** — written by the `nf-gwrepo` plugin. Carries the workflow id, the
  API endpoint, and a `task_id → process_execution_id` map (`nf-co2footprint`'s provenance
  file identifies tasks only by `task_id`).
- **`co2footprint_provenance*.json`** — written by `nf-co2footprint` (on by default).
- **`co2footprint_summary*.txt`** — optional; the only source for the "car km" and "tree
  sequestration" figures on the workflow summary. Enable it with
  `co2footprint.summary.enabled = true`; without it those two fields are `0`.

### Options

| Flag | Default |
|---|---|
| `--run-dir` | `.` |
| `--marker` | `<run-dir>/.gwrepo-run.json` |
| `--provenance` | newest `co2footprint_provenance*.json` under `--run-dir` |
| `--summary` | newest `co2footprint_summary*.txt` under `--run-dir` |
| `--workflow-id` | from the marker |
| `--endpoint` | from the marker, else `http://localhost:80` |
| `--dry-run` | print payloads instead of POSTing |

## Wiring it into a run

`workflow.onComplete` cannot be used — it fires before `nf-co2footprint` writes its files.
Use a wrapper instead:

```bash
nextflow run <pipeline> -c gwrepo.config && python /path/to/co2-import/submit_co2.py
```

## Requirements

Python 3 standard library only (`urllib`, `json`, `argparse`). No `pip install`.

## What lands in the API

- `POST /processes/co2` → one `Co2footprint` row per task (`energy_consumption_mwh`,
  `co2e_mg`, `co2e_market_mg`, `carbon_intensity_gco2e_kwh`, `powerdraw_cpu_w`,
  `cpu_model`, `raw_energy_processor_mwh`, `raw_energy_memory_mwh`). `nf-co2footprint`
  stores energy in Wh and mass in g; the importer converts to mWh / mg.
- `POST /workflows/{id}/co2/` → one `Workflowco2summary` row, from the provenance tree's
  root (session) node — total energy and CO2e "including cached tasks", matching the
  summary file's headline figures.
