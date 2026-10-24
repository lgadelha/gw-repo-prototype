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
| `--endpoint` | `$GWREPO_URL`, else `http://localhost:80` |
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

## Run it before the directories are reused

The script hashes the files as they are when it runs. If a later run in the same
launch directory overwrites `results/`, or `work/` is cleaned, the earlier run's
files are gone or no longer its own.
- **Published outputs:** each one is checked against its lineage record: a file by
  size and modification time, a directory by whether any file under it is newer than
  the record. One that changed since the run published it is posted with `sha256`
  null, not with another run's hash.
- **Task files:** an unreadable file is posted with `sha256` null.
- **Stored hashes are kept:** the API never replaces a stored hash with null, so
  re-importing an old run after `work/` was cleaned leaves the hashes of its first
  import in place. A wrong hash, though, can't be cleared by re-importing; delete the
  row instead.

## Requirements

Python 3 standard library only (`urllib`, `json`, `argparse`, `hashlib`). No
`pip install`.

## What lands in the API

- `POST /input_files/` — one `ProcessExecutionInputFile` row per file-typed task
  input (`process_execution_id`, `filename` — the full work-dir path, `sha256`).
- `POST /output_files/` — one `ProcessExecutionOutputFile` row per file-typed task
  output, same shape.
- `PATCH /processes/{id}` — the task's `code_checksum`: nf-lineage's checksum of the
  process code. It's the same for every task of a process and changes when the
  process's code changes, so it tells pipeline versions apart per process. It's kept
  when a `-resume` reports the task again.
- `POST /parameters/` — one `ProcessExecutionParameterInput` row per task value
  parameter, read back with `GET /parameters/{process_execution_id}`:
  - `val` inputs, with map entries under dotted names (nf-core's `meta` map becomes
    `meta.id`, `meta.single_end`, `meta.strandedness`, ...);
  - task globals the script used, e.g. `task.ext.args` (the tool options set in config)
    and `task.ext.prefix`.

  `parameter_value` holds the text form (booleans as `true`/`false`, lists as JSON);
  numbers are also in `value_num`. Null values are skipped.
- `POST /workflows/{id}/outputs/` — one `WorkflowOutputFile` row per file or directory
  the run published (`publishDir`), read back with `GET /workflows/{id}/outputs/`:
  the published path, its `sha256` and `size_bytes`, the task that produced it
  (`source_process_execution_id`) and its path in that task's work dir
  (`source_filename`). The RO-Crate export lists them as the run's `result`.
  - They come from nf-lineage's `FileOutput` records of the run's last launch
    (`-resume` launches share the workflow id). Lineage records a published file
    only if it lands inside the run's `outputDir`, which defaults to `results`. If a
    pipeline publishes elsewhere (e.g. `--outdir /data/out`), set
    `outputDir = params.outdir` in the config.
  - Nextflow's own reports (`pipeline_info/execution_*`, the DAG, the params JSON)
    aren't recorded by lineage, so they aren't imported.

How `sha256` is computed:
- **Remote inputs** (`https://…`, including pipeline assets nf-lineage records as
  `github.com/<repo>/tree/<commit>/…` links) keep the URL as `filename`; the hash is
  of the copy Nextflow staged into the task's work dir.
- **Directories** (e.g. a `salmon/` index) get one SHA-256 over their sorted entries'
  `<name>\0<sha256>` lines, recursively, built from the entries' own hashes.
- `size_bytes` is recorded in the same pass: a file's size, or for a directory the
  sum of its files' sizes.
- Hashes are cached by real path, so a file or directory used by many tasks (or
  inside an already-hashed directory) is read once per run.
- A path that still can't be read is posted with `sha256` null rather than dropped
  (an already stored hash is kept; see above).
