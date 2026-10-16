#!/usr/bin/env python3
"""
Post-run per-sample domain-metric importer for GW-RePO.

Reads MultiQC's `multiqc_data.json` (written by nf-core pipelines under
`<outdir>/multiqc/**/multiqc_data/`) and POSTs its General Statistics -- read
counts, alignment rates, duplication, GC content, ... from every tool MultiQC
recognised -- as `Samplemetric` rows (one per sample and metric) to the GW-RePO
API, attached to the workflow the tower-emulation endpoints already created.

Reads the JSON, not `multiqc_general_stats.txt`: the text table holds display-
scaled values (e.g. `total_sequences` 0.1 meaning 0.1 M), the JSON the raw ones.

Usage:
    python multiqc-import/submit_multiqc.py [--run-dir .] [--multiqc-data PATH]

Needs GWREPO_API_KEY in the environment (same key used for `tower.accessToken`).

Requires:
    - <run-dir>/.nextflow/history -- its last line's sessionId gives the workflow id.
    - a multiqc_data.json somewhere under <run-dir> (or --multiqc-data, e.g. when
      the pipeline's --outdir is outside the run dir).

Keys are `<module>-<metric>` as in MultiQC's own table headers (e.g.
`fastqc_raw-total_sequences`, `star-uniquely_mapped_percent`). Per-read rows of
paired-end data (sample `X Read 1`) are stored under sample `X` with the read
appended to the key (`fastqc_raw-total_sequences.read1`), so `sample` matches the
task tag (nf-core's meta.id) of the tasks that processed it.
"""

import argparse
import glob
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

READ_SUFFIX = re.compile(r"^(.*) Read ([12])$")


# --- workflow-id resolution ------------------------------------------------
# Duplicated from co2-import/submit_co2.py rather than shared -- the importers
# are small, single-purpose, and stdlib-only by design; see PLAN.md.

def resolve_workflow_id(run_dir):
    """Last line of <run-dir>/.nextflow/history, column 6 (0-indexed 5):
    timestamp, duration, runName, status, revisionId, sessionId, command."""
    history = Path(run_dir) / ".nextflow" / "history"
    if not history.is_file():
        return None
    lines = [l for l in history.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    cols = lines[-1].split("\t")
    return cols[5] if len(cols) > 5 else None


def newest(pattern, root):
    hits = sorted(glob.glob(str(Path(root) / "**" / pattern), recursive=True), key=os.path.getmtime)
    return hits[-1] if hits else None


# --- MultiQC data ------------------------------------------------------------

def metric_rows(data):
    """Yield (sample, key, value_num, value_text) from multiqc_data.json's
    `report_general_stats_data` ({module: {sample: {metric: value}}})."""
    stats = data.get("report_general_stats_data")
    if not isinstance(stats, dict):
        sys.exit(f"unsupported multiqc_data.json layout (MultiQC {data.get('config_version')}): "
                 "expected report_general_stats_data to be {module: {sample: {metric: value}}}")
    for module, samples in stats.items():
        for sample, metrics in samples.items():
            read = READ_SUFFIX.match(sample)
            base, suffix = (read.group(1), f".read{read.group(2)}") if read else (sample, "")
            for metric, value in metrics.items():
                key = f"{module}-{metric}{suffix}"
                if isinstance(value, bool) or value is None:
                    continue
                if isinstance(value, (int, float)):
                    if math.isfinite(value):
                        yield base, key, float(value), None
                else:
                    yield base, key, None, str(value)


# --- HTTP ------------------------------------------------------------------

def post(endpoint, path, api_key, payload):
    body = json.dumps({k: v for k, v in payload.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(
        endpoint.rstrip("/") + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return None, str(e.reason)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=".", help="dir the pipeline ran in (default: .)")
    ap.add_argument("--multiqc-data", help="path to multiqc_data.json (default: newest under --run-dir)")
    ap.add_argument("--workflow-id", help="override the workflow id resolved from .nextflow/history")
    ap.add_argument("--endpoint", default=os.environ.get("GWREPO_URL", "http://localhost:80"), help="GW-RePO API base URL (default: $GWREPO_URL, else http://localhost:80)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be POSTed, don't POST")
    args = ap.parse_args()

    api_key = os.environ.get("GWREPO_API_KEY", "")
    if not api_key and not args.dry_run:
        sys.exit("GWREPO_API_KEY not set")

    workflow_id = args.workflow_id or resolve_workflow_id(args.run_dir)
    if not workflow_id:
        sys.exit(f"no workflow id -- {args.run_dir}/.nextflow/history not found or empty, "
                  "and --workflow-id not given")

    data_path = args.multiqc_data or newest("multiqc_data.json", args.run_dir)
    if not data_path:
        sys.exit("no multiqc_data.json found under --run-dir -- pass --multiqc-data if the "
                 "pipeline's --outdir is elsewhere")

    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    print(f"workflow {workflow_id}")
    print(f"multiqc data {data_path} (MultiQC {data.get('config_version')})")

    api_path = f"/workflows/{workflow_id}/sample_metrics/"
    posted, failed, samples = 0, 0, set()
    for sample, key, value_num, value_text in metric_rows(data):
        samples.add(sample)
        row = {"workflow_execution_id": workflow_id, "sample": sample, "key": key,
               "value_num": value_num, "value_text": value_text}
        if args.dry_run:
            print(f"  {sample} {key} = {value_num if value_text is None else value_text}")
            posted += 1
            continue
        code, msg = post(args.endpoint, api_path, api_key, row)
        if code and code < 300:
            posted += 1
        else:
            print(f"  ! POST {api_path} for {sample} {key} -> {code} {msg}")
            failed += 1
            if code == 404:
                sys.exit(f"workflow {workflow_id} not found at {args.endpoint} -- was the pipeline "
                         "run with `tower { enabled = true }` pointed at this API?")

    print(f"done: {posted} metric rows for {len(samples)} samples, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
