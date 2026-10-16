# multiqc-import

Post-run per-sample domain-metric importer for GW-RePO. Reads
[MultiQC](https://multiqc.info/)'s `multiqc_data.json` and POSTs its General Statistics
(read counts, alignment rates, duplication, GC content, ... from every tool MultiQC
recognised) to the GW-RePO API as `Samplemetric` rows, attached to the workflow the
tower-emulation endpoints (`api/main.py`) already created during the run.

## Why

Input volume (reads, bases) is the strongest predictor of a task's resource use, and it's
known before the task runs. GW-RePO otherwise only has the hand-set `data_size_tag`.
nf-core pipelines already gather these per-sample numbers into one MultiQC report, so
one importer covers most pipelines. This is BioWorkbench's "domain extractor" idea; see
`../PLAN-provenance-ideas.md`, item 2.

## Usage

```bash
export GWREPO_API_KEY=<your API_KEY from .env>

nextflow run <pipeline> -c gwrepo.config --outdir results
python multiqc-import/submit_multiqc.py       # from the same directory
```

Or run it as part of `submit_run.py`, which runs it by default (`--skip-multiqc` to turn it off).

### Options

| Flag | Default |
|---|---|
| `--run-dir` | `.` |
| `--multiqc-data` | newest `multiqc_data.json` under `--run-dir` (pass it if `--outdir` is elsewhere) |
| `--workflow-id` | resolved from `.nextflow/history` |
| `--endpoint` | `$GWREPO_URL`, else `http://localhost:80` |
| `--dry-run` | print rows instead of POSTing |

## What lands in the API

`POST /workflows/{workflow_id}/sample_metrics/` gets one row per sample and metric:

| Field | Value |
|---|---|
| `sample` | MultiQC sample name. For nf-core this is `meta.id`, the same as `ProcessExecution.tag` of the tasks that processed it. |
| `key` | `<module>-<metric>`, as in MultiQC's own headers, e.g. `star-total_reads`, `fastqc_raw-total_sequences` |
| `value_num` / `value_text` | numeric values in `value_num`, others (e.g. `salmon-library_types = ISR`) in `value_text` |

Details:

- **Per-read rows.** For paired-end data, MultiQC reports some tools per read (`WT_REP1 Read 1`). These are stored under the sample (`WT_REP1`) with the read appended to the key (`fastqc_raw-total_sequences.read1`), so `sample` always matches the task tag.
- **Raw values.** The importer reads `multiqc_data.json`, not `multiqc_general_stats.txt`. The text table holds display-scaled values (`total_sequences` = `0.1` meaning 0.1 M); the JSON has the raw ones (`100000`).
- **Re-running is safe.** Rows are upserted on `(workflow, sample, key)`.

Read them back with `GET /workflows/{workflow_id}/sample_metrics/?sample=<id>`, or join in SQL:

```sql
SELECT p.process_name, p.tag, p.peak_rss, m.value_num AS total_reads
FROM processexecution p
JOIN samplemetric m ON m.workflow_execution_id = p.workflow_execution_id
                   AND m.sample = p.tag AND m.key = 'fastqc_raw-total_sequences';
```

## Requirements

Python 3 standard library only. Tested with MultiQC 1.33, the version used by
nf-core/rnaseq 3.26.0. It expects `report_general_stats_data` to be
`{module: {sample: {metric: value}}}` and exits with a clear message if a
MultiQC version lays it out differently.
