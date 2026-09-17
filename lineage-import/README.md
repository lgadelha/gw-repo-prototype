# lineage-import

Post-run file-provenance importer for GW-RePO. Reads
[`nf-lineage`](https://www.nextflow.io/docs/latest/lineage.html)'s local file store
and POSTs input/output file rows (each with a real SHA-256 of its content) to the
GW-RePO API, attaching them to the `ProcessExecution` rows the tower-emulation
endpoints (`api/main.py`) already created during the run.

## Why this is a separate step

Content hashing needs to read every file, which is I/O that shouldn't sit on the
workflow's task-completion critical path. `nf-lineage` itself only ever computes a
*metadata* hash (path + size + mtime, not content) for ordinary work-dir files, so
it can't be used as-is for a `sha256` column either. This script does the one thing
neither piece does: read the files nf-lineage already tells us about and hash their
actual content, after the run.

## Usage

```bash
export GWREPO_API_KEY=<your API_KEY from .env>

nextflow run <pipeline> -c gwrepo.config      # tower {} + lineage.enabled = true
python lineage-import/submit_lineage.py       # from the same directory
```

`submit_lineage.py` needs, in the run directory:

- **`.nextflow/history`** — Nextflow's own always-on run log; its last line gives
  the workflow id. No gw-repo-specific marker file is used (there's nothing
  server-side that could write one to the pipeline's launch dir).
- **`.lineage/`** — nf-lineage's store, written when the pipeline ran with
  `lineage.enabled = true`.

### Options

| Flag | Default |
|---|---|
| `--run-dir` | `.` |
| `--workflow-id` | resolved from `.nextflow/history` |
| `--endpoint` | `http://localhost:80` |
| `--dry-run` | print what would be POSTed instead of POSTing |

## Wiring it into a run

Unlike CO2 ingestion, `nf-lineage` writes its records incrementally as each task
completes (not only at a clean shutdown), so in principle this could run any time
after a task finishes. In practice it's simplest to run it the same way as
`co2-import`, once after the pipeline exits:

```bash
nextflow run <pipeline> -c gwrepo.config && python /path/to/lineage-import/submit_lineage.py
```

## Known limitation: `scratch`

A task using the `scratch` directive runs in a location whose lifetime is tied to
that individual task, not the whole pipeline run. If that location is genuinely
ephemeral (e.g. a cluster scheduler's node-local `$TMPDIR`, torn down when the
task's job allocation ends), the file this script tries to hash may already be
gone by the time it runs, even though the overall pipeline succeeded — not fixed
here. (Tested with the default local executor and a plain `scratch true`: the
declared output was still present post-run, since Nextflow stages declared outputs
back into the canonical work dir regardless of scratch use — the risk is specific
to schedulers with a real ephemeral device tied to job lifetime, not exercised by
this test.)

Files under an *ordinary* (non-scratch) work dir are safe to read post-run in every
other case: Nextflow never deletes `work/` on error/abort, and only deletes it on
success if `cleanup = true` is explicitly set in `nextflow.config`.

## Requirements

Python 3 standard library only (`urllib`, `json`, `argparse`, `hashlib`). No
`pip install`.

## What lands in the API

- `POST /input_files/` — one `ProcessExecutionInputFile` row per file-typed task
  input (`process_execution_id`, `filename` — the full work-dir path, `sha256`).
- `POST /output_files/` — one `ProcessExecutionOutputFile` row per file-typed task
  output, same shape.
