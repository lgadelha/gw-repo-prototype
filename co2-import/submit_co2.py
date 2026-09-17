#!/usr/bin/env python3
"""
Post-run CO2 importer for GW-RePO.

nf-co2footprint writes its output files during Nextflow's plugin-shutdown phase, i.e.
*after* the tower-emulation POSTs for the run have already landed. Nothing running
inside Nextflow can reliably read them. This script runs afterwards: it reads
nf-co2footprint's provenance JSON and POSTs per-task CO2 rows plus a workflow summary
to the GW-RePO API, matching the ProcessExecution rows the tower emulation endpoints
(api/main.py) already created during the run.

Usage:
    python co2-import/submit_co2.py [--run-dir .] [--provenance PATH]

Needs GWREPO_API_KEY in the environment (same key used for `tower.accessToken`).

Requires:
    - <run-dir>/.nextflow/history -- Nextflow's own always-on run log; its last
      line's sessionId column gives the workflow id (no gw-repo-specific marker
      file needed -- there is nothing server-side that could write one to the
      pipeline's launch dir).
    - a co2footprint_provenance_*.json somewhere under <run-dir> (or --provenance).
"""

import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# --- unit normalisation -------------------------------------------------------
# nf-co2footprint's provenance JSON stores canonical units (Wh, g); the GW-RePO
# schema wants mWh and mg. Factors convert *to* the schema unit.
_ENERGY_TO_MWH = {"wh": 1000.0, "mwh": 1.0, "kwh": 1_000_000.0, "uwh": 0.001, "µwh": 0.001}
_MASS_TO_MG = {"g": 1000.0, "mg": 1.0, "kg": 1_000_000.0, "ug": 0.001, "µg": 0.001}


def _value(node, key):
    """Unwrap a JSON-LD field: {"@type": ..., "value": X} -> X (or the raw value)."""
    v = node.get(key)
    if isinstance(v, dict):
        return v.get("value")
    return v


def _unit(node, key):
    v = node.get(key)
    return (v.get("unitText") or "").strip().lower() if isinstance(v, dict) else ""


def _energy_mwh(node, key):
    raw = _value(node, key)
    if raw is None:
        return None
    return float(raw) * _ENERGY_TO_MWH.get(_unit(node, key), 1.0)


def _mass_mg(node, key):
    raw = _value(node, key)
    if raw in (None, "-", ""):
        return None
    return float(raw) * _MASS_TO_MG.get(_unit(node, key), 1.0)


def _plain(node, key):
    raw = _value(node, key)
    return None if raw in (None, "-", "") else raw


# --- provenance tree walk ----------------------------------------------------

def iter_tasks(node):
    """Yield every task-level node (schema:Action) in the provenance tree."""
    if node.get("@type") == "schema:Action":
        yield node
    for child in node.get("hasPart", []) or []:
        yield from iter_tasks(child)


def process_co2_row(task, process_execution_id):
    return {
        "process_execution_id": process_execution_id,
        "energy_consumption_mwh": _energy_mwh(task, "energy_consumption"),
        "co2e_mg": _mass_mg(task, "CO2e"),
        "co2e_market_mg": _mass_mg(task, "CO2e_market"),
        "carbon_intensity_gco2e_kwh": float(_value(task, "carbon_intensity")),
        "powerdraw_cpu_w": float(_value(task, "powerdraw_cpu")),
        "cpu_model": _plain(task, "cpu_model") or "unknown",
        "raw_energy_processor_mwh": _energy_mwh(task, "raw_energy_processor"),
        "raw_energy_memory_mwh": _energy_mwh(task, "raw_energy_memory"),
    }


def workflow_co2_row(root, workflow_execution_id, summary_path):
    car_km, tree_sec = parse_summary(summary_path) if summary_path else (0.0, 0.0)
    return {
        "workflow_execution_id": workflow_execution_id,
        "total_energy_mwh": _energy_mwh(root, "energy_consumption") or 0.0,
        "total_co2e_mg": _mass_mg(root, "CO2e") or 0.0,
        "car_km_equivalent": car_km,
        "tree_sequestration_time_sec": tree_sec,
    }


def parse_summary(path):
    """car_km_equivalent / tree_sequestration_time_sec appear only as prose."""
    import re

    text = Path(path).read_text(encoding="utf-8", errors="replace")
    car = re.search(r"([\d.eE+-]+)\s*km travelled by car", text)
    tree = re.search(r"tree\s+(?:(\d+)h)?\s*(?:(\d+)min)?\s*([\d.]+)s\s+to sequester", text)
    car_km = float(car.group(1)) if car else 0.0
    tree_sec = 0.0
    if tree:
        h, m, s = tree.groups()
        tree_sec = (int(h) * 3600 if h else 0) + (int(m) * 60 if m else 0) + float(s)
    return car_km, tree_sec


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


def get_json(endpoint, path, api_key):
    req = urllib.request.Request(
        endpoint.rstrip("/") + path,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --- workflow / process-id resolution --------------------------------------
# No gw-repo-specific marker file (the old plugin's .gwrepo-run.json) exists
# anymore -- the tower-emulation API is a remote HTTP receiver with no
# filesystem access to the pipeline's launch dir. Resolve everything from
# Nextflow's own bookkeeping and the API instead.

def resolve_workflow_id(run_dir):
    """The last line of Nextflow's own <run-dir>/.nextflow/history, column 6
    (0-indexed 5): timestamp, duration, runName, status, revisionId, sessionId,
    command -- tab-separated, one line appended per run."""
    history = Path(run_dir) / ".nextflow" / "history"
    if not history.is_file():
        return None
    lines = [l for l in history.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    cols = lines[-1].split("\t")
    return cols[5] if len(cols) > 5 else None


def resolve_process_map(endpoint, api_key, workflow_id):
    """task_id -> process_execution_id, from the ProcessExecution rows the tower
    emulation endpoints already created for this workflow (task_id column added
    for exactly this purpose)."""
    rows = get_json(endpoint, f"/processes/?workflow_execution_id={workflow_id}", api_key)
    return {str(r["task_id"]): r["id"] for r in rows if r.get("task_id") is not None}


# --- file discovery -------------------------------------------------------

def newest(pattern, root):
    hits = sorted(glob.glob(str(Path(root) / "**" / pattern), recursive=True), key=os.path.getmtime)
    return hits[-1] if hits else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=".", help="dir the pipeline ran in (default: .)")
    ap.add_argument("--provenance", help="path to co2footprint_provenance_*.json (default: newest under --run-dir)")
    ap.add_argument("--summary", help="path to co2footprint_summary_*.txt (default: newest under --run-dir)")
    ap.add_argument("--workflow-id", help="override the workflow id resolved from .nextflow/history")
    ap.add_argument("--endpoint", default="http://localhost:80", help="GW-RePO API base URL (default: http://localhost:80)")
    ap.add_argument("--dry-run", action="store_true", help="print payloads, don't POST")
    args = ap.parse_args()

    api_key = os.environ.get("GWREPO_API_KEY", "")
    if not api_key and not args.dry_run:
        sys.exit("GWREPO_API_KEY not set")

    workflow_id = args.workflow_id or resolve_workflow_id(args.run_dir)
    if not workflow_id:
        sys.exit(f"no workflow id -- {args.run_dir}/.nextflow/history not found or empty, "
                  "and --workflow-id not given")

    endpoint = args.endpoint
    try:
        processes = resolve_process_map(endpoint, api_key, workflow_id)
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        sys.exit(f"could not fetch processes for workflow {workflow_id} from {endpoint}: {e}")
    if not processes:
        sys.exit(f"no ProcessExecution rows found for workflow {workflow_id} at {endpoint} -- "
                  "was the pipeline run with `tower {{ enabled = true }}` pointed at this API?")

    prov_path = args.provenance or newest("co2footprint_provenance*.json", args.run_dir)
    if not prov_path:
        sys.exit("no co2footprint_provenance*.json found under --run-dir -- is nf-co2footprint enabled?")
    summary_path = args.summary or newest("co2footprint_summary*.txt", args.run_dir)

    tree = json.loads(Path(prov_path).read_text(encoding="utf-8"))
    print(f"workflow {workflow_id}")
    print(f"provenance {prov_path}")

    posted, skipped, failed = 0, 0, 0
    for task in iter_tasks(tree):
        task_id = str(_value(task, "task_id"))
        pid = processes.get(task_id)
        if not pid:
            print(f"  ! task_id {task_id} ({_value(task, 'name')}) has no matching ProcessExecution row -- skipping")
            skipped += 1
            continue
        row = process_co2_row(task, pid)
        if args.dry_run:
            print(f"  {task_id} -> {json.dumps(row)}")
            posted += 1
            continue
        code, msg = post(endpoint, "/processes/co2", api_key, row)
        if code and code < 300:
            posted += 1
        else:
            print(f"  ! POST /processes/co2 for {task_id} -> {code} {msg}")
            failed += 1

    summary_row = workflow_co2_row(tree, workflow_id, summary_path)
    if args.dry_run:
        print(f"  summary -> {json.dumps(summary_row)}")
    else:
        code, msg = post(endpoint, f"/workflows/{workflow_id}/co2/", api_key, summary_row)
        if not (code and code < 300):
            print(f"  ! POST /workflows/{workflow_id}/co2/ -> {code} {msg}")
            failed += 1

    print(f"done: {posted} task rows, {skipped} skipped, {failed} failed")
    if skipped and not posted:
        sys.exit("every task was skipped -- the resolved process map does not match this "
                 "provenance file (wrong --run-dir, or --workflow-id from a different run?)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
