#!/usr/bin/env python3
"""
Post-run file-provenance importer for GW-RePO.

Reads Nextflow's own `nf-lineage` local file store (`lineage.enabled = true`) to
find each task's input/output file paths, computes a real SHA-256 of each file's
content itself (nf-lineage's own `checksum` field is a metadata hash --
murmur3(path + size + mtime), not content -- see doc/step0b-tower-lineage-wire-formats.md
section 4), and POSTs `ProcessExecutionInputFile` / `ProcessExecutionOutputFile` rows
to the GW-RePO API, matching the ProcessExecution rows the tower-emulation endpoints
(api/main.py) already created during the run.

Usage:
    python lineage-import/submit_lineage.py [--run-dir .]

Needs GWREPO_API_KEY in the environment (same key used for `tower.accessToken`).

Requires:
    - <run-dir>/.nextflow/history -- Nextflow's own always-on run log; its last
      line's sessionId column gives the workflow id.
    - <run-dir>/.lineage/ -- nf-lineage's store, written when the pipeline was run
      with `lineage.enabled = true`.

Known limitation (not fixed here): a task using the `scratch` directive tears down
its local scratch dir as soon as that task completes, so its lineage-recorded
FileOutput.path may already be gone by the time this script runs, even though the
overall pipeline succeeded. Files under an ordinary (non-scratch) work dir are safe
to read post-run -- Nextflow never deletes `work/` on error/abort, and only deletes
it on success if `cleanup = true` is explicitly set.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


# --- workflow / process-id resolution --------------------------------------
# Duplicated from co2-import/submit_co2.py rather than shared -- both scripts are
# small, single-purpose, and stdlib-only by design; see PLAN.md.

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


def get_json(endpoint, path, api_key):
    req = urllib.request.Request(
        endpoint.rstrip("/") + path,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


# --- nf-lineage store reading -----------------------------------------------
# Wire format confirmed empirically -- see doc/step0b-tower-lineage-wire-formats.md.

def _lineage_record(lineage_dir, key):
    path = lineage_dir / key / ".data.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_file_uri(path):
    return path[len("file://"):] if path.startswith("file://") else path


def _resolve_path_values(value, lineage_dir):
    """A `Parameter.value` for a `type: "path"` entry is either a `lid://<hash>/
    <filename>` string (produced by an earlier task -> resolve its FileOutput
    record for the real path), an inline `{path, checksum}` dict (a workflow-
    boundary input, no producing task), or a list of either. Returns a flat list
    of real filesystem paths."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    paths = []
    for item in items:
        if isinstance(item, str) and item.startswith("lid://"):
            key = item[len("lid://"):]  # "<hash>/<filename>"
            record = _lineage_record(lineage_dir, key)
            if record and record.get("kind") == "FileOutput":
                paths.append(_strip_file_uri(record["spec"]["path"]))
        elif isinstance(item, dict) and "path" in item:
            paths.append(_strip_file_uri(item["path"]))
    return paths


def task_file_paths(lineage_dir, task_hash):
    """(input_paths, output_paths) for one task, or (None, None) if this task has
    no TaskRun record in the lineage store (e.g. lineage was disabled)."""
    task_run = _lineage_record(lineage_dir, task_hash)
    if not task_run or task_run.get("kind") != "TaskRun":
        return None, None

    inputs = []
    for param in task_run["spec"].get("input") or []:
        if param.get("type") == "path":
            inputs.extend(_resolve_path_values(param.get("value"), lineage_dir))

    outputs = []
    task_output = _lineage_record(lineage_dir, f"{task_hash}#output")
    if task_output and task_output.get("kind") == "TaskOutput":
        for param in task_output["spec"].get("output") or []:
            if param.get("type") == "path":
                outputs.extend(_resolve_path_values(param.get("value"), lineage_dir))

    return inputs, outputs


# --- hashing -----------------------------------------------------------------

def sha256_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        print(f"    ! could not read {path}: {e}")
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=".", help="dir the pipeline ran in (default: .)")
    ap.add_argument("--workflow-id", help="override the workflow id resolved from .nextflow/history")
    ap.add_argument("--endpoint", default="http://localhost:80", help="GW-RePO API base URL (default: http://localhost:80)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be POSTed, don't POST")
    args = ap.parse_args()

    api_key = os.environ.get("GWREPO_API_KEY", "")
    if not api_key and not args.dry_run:
        sys.exit("GWREPO_API_KEY not set")

    workflow_id = args.workflow_id or resolve_workflow_id(args.run_dir)
    if not workflow_id:
        sys.exit(f"no workflow id -- {args.run_dir}/.nextflow/history not found or empty, "
                  "and --workflow-id not given")

    lineage_dir = Path(args.run_dir) / ".lineage"
    if not lineage_dir.is_dir():
        sys.exit(f"{lineage_dir} not found -- run the pipeline with `lineage.enabled = true`")

    try:
        processes = get_json(args.endpoint, f"/processes/?workflow_execution_id={workflow_id}", api_key)
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        sys.exit(f"could not fetch processes for workflow {workflow_id} from {args.endpoint}: {e}")
    if not processes:
        sys.exit(f"no ProcessExecution rows found for workflow {workflow_id} at {args.endpoint} -- "
                  "was the pipeline run with `tower {{ enabled = true }}` pointed at this API?")

    print(f"workflow {workflow_id}")
    print(f"lineage store {lineage_dir}")

    posted, skipped, failed = 0, 0, 0
    for proc in processes:
        process_id = proc["id"]
        prefix = f"{workflow_id}_"
        if not process_id.startswith(prefix):
            continue
        task_hash = process_id[len(prefix):]

        inputs, outputs = task_file_paths(lineage_dir, task_hash)
        if inputs is None:
            print(f"  ! {process_id}: no TaskRun record in {lineage_dir} -- skipping")
            skipped += 1
            continue

        for kind, paths, api_path in (("input", inputs, "/input_files/"), ("output", outputs, "/output_files/")):
            for path in paths:
                if args.dry_run:
                    print(f"  {process_id} {kind} {path}")
                    posted += 1
                    continue
                sha256 = sha256_file(path)
                if sha256 is None:
                    failed += 1
                    continue
                row = {"process_execution_id": process_id, "filename": path, "sha256": sha256}
                code, msg = post(args.endpoint, api_path, api_key, row)
                if code and code < 300:
                    posted += 1
                else:
                    print(f"  ! POST {api_path} for {process_id} ({path}) -> {code} {msg}")
                    failed += 1

    print(f"done: {posted} file rows, {skipped} tasks skipped, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
