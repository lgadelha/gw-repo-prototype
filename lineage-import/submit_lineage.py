#!/usr/bin/env python3
"""
Post-run file-provenance importer for GW-RePO.

Reads Nextflow's own `nf-lineage` local file store (`lineage.enabled = true`) to
find each task's input/output file paths and value parameters, computes a real SHA-256 of each file's
content itself (nf-lineage's own `checksum` field is a metadata hash --
murmur3(path + size + mtime), not content -- see doc/step0b-tower-lineage-wire-formats.md
section 4), and POSTs `ProcessExecutionInputFile` / `ProcessExecutionOutputFile` rows
to the GW-RePO API, matching the ProcessExecution rows the tower-emulation endpoints
(api/main.py) already created during the run. Also POSTs each task's `val` inputs
and task globals (e.g. `task.ext.args`) as ProcessExecutionParameterInput rows,
and the files the run published (publishDir) as WorkflowOutputFile rows, each with
the task that produced it.

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
import glob
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
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


def post(endpoint, path, api_key, payload, method="POST"):
    body = json.dumps({k: v for k, v in payload.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(
        endpoint.rstrip("/") + path,
        data=body,
        method=method,
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


def _flatten(name, value):
    """(name, text, num) rows; map entries get dotted names, nulls are dropped."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _flatten(f"{name}.{k}", v)
    elif value is None:
        return
    elif isinstance(value, bool) or isinstance(value, list):
        yield name, json.dumps(value), None
    elif isinstance(value, (int, float)):
        yield name, str(value), float(value)
    else:
        yield name, str(value), None


def task_parameters(lineage_dir, task_hash):
    """Value inputs (`val`, e.g. nf-core's `meta` map) and task globals (e.g.
    `task.ext.args`) of one task, as (name, text, num) rows."""
    task_run = _lineage_record(lineage_dir, task_hash)
    if not task_run or task_run.get("kind") != "TaskRun":
        return []
    spec = task_run["spec"]
    rows = []
    for param in spec.get("input") or []:
        if param.get("type") == "val":
            rows.extend(_flatten(param["name"], param.get("value")))
    for name, value in (spec.get("globalVars") or {}).items():
        rows.extend(_flatten(name, value))
    return rows


def task_code_checksum(lineage_dir, task_hash):
    """nf-lineage's codeChecksum of the task's process code (the same for every task
    of a process; changes when the process code changes)."""
    task_run = _lineage_record(lineage_dir, task_hash)
    if not task_run or task_run.get("kind") != "TaskRun":
        return None
    return (task_run["spec"].get("codeChecksum") or {}).get("value")


def task_work_dir(lineage_dir, task_hash):
    """<workDir>/<hash[:2]>/<hash[2:]>, with workDir from the task's WorkflowRun
    record (so a custom `-w` is honoured), or None if the records are missing."""
    task_run = _lineage_record(lineage_dir, task_hash)
    if not task_run:
        return None
    workflow_run = _lineage_record(lineage_dir, task_run["spec"]["workflowRun"][len("lid://"):])
    if not workflow_run:
        return None
    return Path(workflow_run["spec"]["metadata"]["workDir"]) / task_hash[:2] / task_hash[2:]


def local_copy(path, work_dir):
    """Remote inputs (https://..., and pipeline assets that nf-lineage records as
    https://github.com/<repo>/tree/<commit>/... links) are recorded by their URL,
    but Nextflow staged them into the task's work dir under the URL's file name --
    hash that copy. Local paths are returned unchanged."""
    if "://" not in path or work_dir is None:
        return path
    name = path.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    direct = work_dir / name
    if direct.exists():
        return str(direct)
    # `stageAs: 'input*/*'`-style staging (e.g. CAT_FASTQ) puts it one level down
    for hit in sorted(work_dir.glob(f"*/{glob.escape(name)}")):
        if hit.is_symlink():
            return str(hit)
    return str(direct)


def workflow_run_hash(lineage_dir, workflow_id):
    """The lineage hash of the run's last launch. .lineage/.history/ has one file
    per launch (timestamp, runName, sessionId, runLid); -resume launches share the
    sessionId, which is GW-RePO's workflow id, and GW-RePO's row describes the last."""
    launches = []
    for path in (lineage_dir / ".history").glob("*"):
        cols = path.read_text(encoding="utf-8").strip().split("\t")
        if len(cols) > 3 and cols[2] == workflow_id and cols[3].startswith("lid://"):
            launches.append((cols[0], cols[3][len("lid://"):]))
    return max(launches)[1] if launches else None


def published_outputs(lineage_dir, run_hash):
    """(published path, producing task hash, path in that task's work dir, record
    spec) for each file or directory the run published. Lineage records these as FileOutput records
    under <run hash>/<path in the output dir>, with no taskRun and a `source` of
    lid://<task hash>/<name>. Files outside the run's `outputDir` (default `results`)
    and Nextflow's own reports (pipeline_info/execution_*) are not recorded."""
    outputs = []
    for path in sorted((lineage_dir / run_hash).rglob(".data.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        spec = record.get("spec") or {}
        if record.get("kind") != "FileOutput" or spec.get("taskRun"):
            continue
        source = spec.get("source") or ""
        task_hash = source_path = None
        if source.startswith("lid://"):
            key = source[len("lid://"):]
            task_hash = key.split("/", 1)[0]
            source_record = _lineage_record(lineage_dir, key)
            if source_record and source_record.get("kind") == "FileOutput":
                source_path = _strip_file_uri(source_record["spec"]["path"])
        outputs.append((_strip_file_uri(spec["path"]), task_hash, source_path, spec))
    return outputs


def changed_since_published(path, spec):
    """True if a published file or directory changed after this run published it,
    e.g. a later run wrote to the same output directory: its content is then no
    longer this run's. A file is compared by size and modification time with its
    lineage record; a directory (whose own mtime doesn't change when files in it are
    rewritten) by whether any file under it is newer than the record."""
    if not os.path.exists(path):
        return False
    # Trim nanoseconds to microseconds, which fromisoformat accepts on every Python 3.
    stamp = re.sub(r"(\.\d{6})\d+", r"\1", spec.get("modifiedAt") or "")
    try:
        published = datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return False
    if os.path.isdir(path):
        return any(os.stat(os.path.join(root, name)).st_mtime >= published + 1
                   for root, _, names in os.walk(path) for name in names)
    st = os.stat(path)
    return st.st_size != spec.get("size") or abs(st.st_mtime - published) >= 1


# --- hashing -----------------------------------------------------------------

_hash_cache = {}


def hash_and_size(path):
    """(sha256, size_bytes) of a file's content; for a directory, a SHA-256 over
    its sorted entries' `<name>\\0<sha256>` lines (recursively, Merkle-style) and
    the sum of its entries' sizes. Cached by real path, so a file or directory
    staged into many tasks (symlinks to one copy), or already hashed inside a
    parent directory, is read only once per run. (None, None) if anything under
    `path` can't be read."""
    real = os.path.realpath(path)
    if real in _hash_cache:
        return _hash_cache[real]
    try:
        h = hashlib.sha256()
        size = 0
        if os.path.isdir(real):
            for name in sorted(os.listdir(real)):
                child, child_size = hash_and_size(os.path.join(real, name))
                if child is None:
                    return None, None
                h.update(f"{name}\0{child}\n".encode())
                size += child_size
        else:
            with open(real, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
                    size += len(chunk)
        result = (h.hexdigest(), size)
    except OSError as e:
        print(f"    ! could not read {path}: {e}")
        result = (None, None)
    _hash_cache[real] = result
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=".", help="dir the pipeline ran in (default: .)")
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

    lineage_dir = Path(args.run_dir) / ".lineage"
    if not lineage_dir.is_dir():
        sys.exit(f"{lineage_dir} not found -- run the pipeline with `lineage.enabled = true`")

    try:
        processes = get_json(args.endpoint, f"/processes/?workflow_execution_id={workflow_id}", api_key)
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        sys.exit(f"could not fetch processes for workflow {workflow_id} from {args.endpoint}: {e}")
    if not processes:
        sys.exit(f"no ProcessExecution rows found for workflow {workflow_id} at {args.endpoint} -- "
                  "was the pipeline run with `tower { enabled = true }` pointed at this API?")

    print(f"workflow {workflow_id}")
    print(f"lineage store {lineage_dir}")

    posted, skipped, failed, unhashed, params_posted = 0, 0, 0, 0, 0
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

        work_dir = task_work_dir(lineage_dir, task_hash)
        for kind, paths, api_path in (("input", inputs, "/input_files/"), ("output", outputs, "/output_files/")):
            for path in paths:
                # Unreadable files are still recorded (sha256 null), not dropped.
                sha256, size_bytes = hash_and_size(local_copy(path, work_dir))
                if sha256 is None:
                    unhashed += 1
                if args.dry_run:
                    print(f"  {process_id} {kind} {path} {sha256} {size_bytes}")
                    posted += 1
                    continue
                row = {"process_execution_id": process_id, "filename": path, "sha256": sha256,
                       "size_bytes": size_bytes}
                code, msg = post(args.endpoint, api_path, api_key, row)
                if code and code < 300:
                    posted += 1
                else:
                    print(f"  ! POST {api_path} for {process_id} ({path}) -> {code} {msg}")
                    failed += 1

        checksum = task_code_checksum(lineage_dir, task_hash)
        if checksum and args.dry_run:
            print(f"  {process_id} code_checksum {checksum}")
        elif checksum:
            code, msg = post(args.endpoint, f"/processes/{process_id}", api_key,
                             {"code_checksum": checksum}, method="PATCH")
            if not (code and code < 300):
                print(f"  ! PATCH /processes/{process_id} code_checksum -> {code} {msg}")
                failed += 1

        for name, text, num in task_parameters(lineage_dir, task_hash):
            if args.dry_run:
                print(f"  {process_id} param {name} = {text}")
                params_posted += 1
                continue
            row = {"process_execution_id": process_id, "parameter_name": name,
                   "parameter_value": text, "value_num": num}
            code, msg = post(args.endpoint, "/parameters/", api_key, row)
            if code and code < 300:
                params_posted += 1
            else:
                print(f"  ! POST /parameters/ for {process_id} ({name}) -> {code} {msg}")
                failed += 1

    run_hash = workflow_run_hash(lineage_dir, workflow_id)
    if not run_hash:
        print(f"  ! no launch of {workflow_id} in {lineage_dir}/.history -- published outputs skipped")
    task_ids = {proc["id"] for proc in processes}
    outputs_posted = 0
    for path, task_hash, source_path, spec in published_outputs(lineage_dir, run_hash) if run_hash else []:
        if changed_since_published(path, spec):
            print(f"    ! {path} changed since this run published it -- recorded without sha256")
            sha256, size_bytes = None, None
        else:
            # A copy is read again; a symlink to the work dir hits the cache.
            sha256, size_bytes = hash_and_size(path)
        if sha256 is None:
            unhashed += 1
        source_id = f"{workflow_id}_{task_hash}" if task_hash else None
        if args.dry_run:
            print(f"  output {path} {sha256} {size_bytes} <- {source_id} {source_path}")
            outputs_posted += 1
            continue
        row = {"filename": path, "sha256": sha256, "size_bytes": size_bytes,
               "source_process_execution_id": source_id if source_id in task_ids else None,
               "source_filename": source_path}
        code, msg = post(args.endpoint, f"/workflows/{workflow_id}/outputs/", api_key, row)
        if code and code < 300:
            outputs_posted += 1
        else:
            print(f"  ! POST /workflows/{workflow_id}/outputs/ ({path}) -> {code} {msg}")
            failed += 1

    print(f"done: {posted} file rows ({unhashed} without sha256), {params_posted} parameter rows, "
          f"{outputs_posted} published outputs, {skipped} tasks skipped, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
