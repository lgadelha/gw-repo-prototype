"""Workflow Run RO-Crate export of a stored run (PLAN-provenance-ideas.md 7b):
a detached, metadata-only ro-crate-metadata.json following the Provenance Run
Crate profile 0.5 (RO-Crate 1.1), which includes Workflow and Process Run Crate. Pure functions over rows the API already
reads, so they can be tested without a database."""

import mimetypes
import posixpath
import re
from datetime import datetime, timezone
from urllib.parse import quote

PROFILE_VERSION = "0.5"
PROFILES = [
    ("https://w3id.org/ro/wfrun/process/0.5", "Process Run Crate", PROFILE_VERSION),
    ("https://w3id.org/ro/wfrun/workflow/0.5", "Workflow Run Crate", PROFILE_VERSION),
    ("https://w3id.org/ro/wfrun/provenance/0.5", "Provenance Run Crate", PROFILE_VERSION),
    ("https://w3id.org/workflowhub/workflow-ro-crate/1.0", "Workflow RO-Crate", "1.0"),
]
LICENSE = "https://creativecommons.org/licenses/by/4.0/"
NEXTFLOW = "https://w3id.org/workflowhub/workflow-ro-crate#nextflow"
ENGINE = "#nextflow-engine"
TASK_DIR = re.compile(r"^(.*?)/[^/]+/[0-9a-f]{2}/[0-9a-f]{30}(/|$)")  # <launch>/<work>/ab/<30 hex>
MIME = mimetypes.MimeTypes()  # Python's built-in table, not the host's mime.types
COMPRESSED = {"gzip": "application/gzip", "bzip2": "application/x-bzip2", "xz": "application/x-xz"}
NF_TRACE = "https://w3id.org/ro/terms/nf-trace#"  # the profile's example namespace, not yet registered
QUDT = "https://qudt.org/vocab/unit/"
MIB = 1024 * 1024
# resourceUsage per task: (term, processexecution column, factor back to Nextflow's
# trace unit, QUDT unit). Terms are Nextflow's trace field names in the camelCase of
# the profile's example; columns hold seconds and MiB, the crate milliseconds and bytes.
# Percentages and counts get QUDT's PERCENT and NUM, as the validator wants a unitCode on all.
RESOURCES = [("realTime", "realtime", 1000, "MilliSEC"), ("percentCPU", "percent_cpu", 1, "PERCENT"),
             ("percentMem", "percent_memory", 1, "PERCENT"), ("peakRss", "peak_rss", MIB, "BYTE"),
             ("peakVmem", "peak_vmem", MIB, "BYTE"), ("rchar", "read_char", MIB, "BYTE"),
             ("wchar", "write_char", MIB, "BYTE"), ("readBytes", "read_bytes", 1, "BYTE"),
             ("writeBytes", "write_bytes", 1, "BYTE"), ("cpus", "cpus_requested", 1, "NUM"),
             ("memory", "memory_requested", MIB, "BYTE"), ("time", "time_requested", 1000, "MilliSEC")]
STATUS = {"COMPLETED": "http://schema.org/CompletedActionStatus", "FAILED": "http://schema.org/FailedActionStatus"}


def _iso(epoch_s):
    """ISO 8601 with milliseconds, the form the profiles' validator checks for."""
    return datetime.fromtimestamp(epoch_s, timezone.utc).isoformat(timespec="milliseconds") if epoch_s is not None else None


def _ref(entity_id):
    return {"@id": entity_id}


def workflow_id(run: dict) -> str:
    """The main workflow's @id, which validators require to resolve: the main
    script at the run's commit on GitHub, else the repository; a local id for
    runs of local scripts."""
    repo, commit = run.get("repository"), run.get("commit_id")
    if repo and commit and repo.startswith("https://github.com/"):
        return f"{repo}/blob/{commit}/main.nf"
    return repo or f"#workflow-{run.get('script_id')}"


def launch_dir(files: list):
    """The run's launch directory, which GW-RePO doesn't store: the parent of the
    work directory holding the task folders. None when no task folder is seen."""
    for f in files:
        m = TASK_DIR.match(f["filename"])
        if m:
            return m.group(1) or "/"
    return None


def file_id(filename: str, base=None) -> str:
    """A file's @id: its URL for remote files (https, s3, ...), else a local
    `#` id of its path relative to the launch directory `base` (paths outside it
    start with ../), so the crate shows no absolute local paths. RO-Crate 1.2 uses
    `#` ids for files deliberately not in the crate; a relative path would claim the
    file is in it. One entity per path, not per content, so identical outputs of
    different tasks stay apart and the crate's lineage matches GW-RePO's."""
    if "://" in filename:
        return filename
    path = filename
    if base and filename.startswith("/"):
        path = posixpath.relpath(filename, base) + ("/" if filename.endswith("/") else "")
    return "#" + quote(path)


def encoding_format(filename: str):
    """MIME type from the extension; compressed files are their compression format.
    None for extensions the table doesn't know (bam, fastq, log, ...)."""
    mime, compression = MIME.guess_type(filename.rstrip("/"))
    return COMPRESSED.get(compression) or (None if compression else mime)


def _file_entity(f: dict, fid: str) -> dict:
    """A File from a row with filename, sha256 and size_bytes. The SHA-256 goes in
    `identifier`, since the RO-Crate 1.1 context has no sha256 term."""
    entity = {"@id": fid, "@type": "File", "name": f["filename"].rstrip("/").rsplit("/", 1)[-1]}
    if encoding_format(f["filename"]):
        entity["encodingFormat"] = encoding_format(f["filename"])
    if f.get("size_bytes") is not None:
        entity["contentSize"] = str(f["size_bytes"])
    if f.get("sha256"):
        entity["identifier"] = f"sha256:{f['sha256']}"
    return entity


def _files(files: list, ran_ids: set, base):
    """File entities for the tasks that ran, and per task its input and output
    ids. `files` are /workflows/{id}/files rows (direction, process_execution_id,
    filename, sha256, size_bytes)."""
    entities, inputs, outputs = {}, {}, {}
    for f in files:
        if f["process_execution_id"] not in ran_ids:
            continue
        fid = file_id(f["filename"], base)
        if fid not in entities:
            entities[fid] = _file_entity(f, fid)
        side = inputs if f["direction"] == "input" else outputs
        side.setdefault(f["process_execution_id"], []).append(_ref(fid))
    return entities, inputs, outputs


def _resource_usage(task: dict) -> list:
    """PropertyValues of a task's trace resource fields, skipping those not recorded."""
    usage = []
    for term, column, factor, unit in RESOURCES:
        if task.get(column) is None:
            continue
        value = task[column] * factor
        usage.append({"@id": f"#usage-{task['id']}-{term}", "@type": "PropertyValue", "name": term,
                      "propertyID": NF_TRACE + term, "value": round(value) if factor > 1 else value,
                      "unitCode": QUDT + unit})
    return usage


def _value(annotation: dict):
    return annotation["value_text"] if annotation.get("value_text") is not None else annotation.get("value_num")


def _tasks(tasks: list, agent, inputs: dict, outputs: dict):
    """Tools, steps, task actions and one ControlAction per process, for the attempts
    that ran in this run (completed or failed). Steps are numbered in order of each
    process's first start, which respects the dependencies between them."""
    ran = sorted((t for t in tasks if t["final_status"] in STATUS), key=lambda t: (t["start_time"], t["id"]))
    processes = list(dict.fromkeys(t["process_name"] for t in ran))
    tools, steps, controls, entities = [], [], [], []
    for position, process in enumerate(processes, start=1):
        tool, step, control = f"#process-{process}", f"#step-{process}", f"#control-{process}"
        attempts = [t for t in ran if t["process_name"] == process]
        checksum = attempts[0].get("code_checksum")
        entities.append({"@id": tool, "@type": "SoftwareApplication", "name": process,
                         **({"identifier": checksum} if checksum else {})})
        entities.append({"@id": step, "@type": "HowToStep", "name": process, "position": str(position),
                         "workExample": _ref(tool)})
        entities.append({"@id": control, "@type": "ControlAction", "name": f"Orchestration of {process}",
                         "instrument": _ref(step), "object": [_ref(f"#task-{t['id']}") for t in attempts]})
        for t in attempts:
            action = {"@id": f"#task-{t['id']}", "@type": "CreateAction",
                      "name": t.get("task_name") or process,
                      "description": f"Attempt {t.get('attempt') or 1}, exit code {t['exit_code']}",
                      "instrument": _ref(tool), "actionStatus": STATUS[t["final_status"]],
                      "startTime": _iso(t["start_time"]),
                      "endTime": _iso(t.get("complete_time") or t["start_time"] + t["duration"])}
            if t["id"] in inputs:
                action["object"] = inputs[t["id"]]
            if t["id"] in outputs:
                action["result"] = outputs[t["id"]]
            if t["final_status"] == "FAILED":
                action["error"] = f"Exit code {t['exit_code']}"
            if agent:
                action["agent"] = agent
            usage = _resource_usage(t)
            if usage:
                action["resourceUsage"] = [_ref(u["@id"]) for u in usage]
            entities.append(action)
            entities.extend(usage)
        tools.append(_ref(tool))
        steps.append(_ref(step))
        controls.append(_ref(control))
    return tools, steps, controls, entities


def build_crate(run: dict, annotations: list, root_id: str, tasks: list = (), files: list = (),
                published: list = ()) -> dict:
    """The crate for one run. `run` is a workflowexecution row, `annotations` its
    workflowannotation rows, `tasks` its processexecution rows, `files` its
    /workflows/{id}/files rows, `published` its published files (workflowoutputfile
    rows, the run's `result`), `root_id` the run's absolute URL ending in '/'.
    Cached tasks (reused from an earlier launch) and tasks that never finished are
    left out and counted in the description. The run's own inputs are the files its
    tasks read that no task of the run (cached ones included) wrote."""
    wf_id = workflow_id(run)
    action_id = f"#run-{run['id']}"
    params = sorted((a for a in annotations
                     if a["key"].startswith("params.") and not a["key"].startswith("params.genomes.")),
                    key=lambda a: a["key"])
    others = sorted((a for a in annotations if not a["key"].startswith("params.")), key=lambda a: a["key"])
    institute = run.get("institute_id")
    agent = _ref(f"#institute-{institute}") if institute else None
    ran_ids = {t["id"] for t in tasks if t["final_status"] in STATUS}
    base = launch_dir(files)
    # Sorted by path, so a run always exports the same crate (database rows come in no fixed order).
    file_entities, inputs, outputs = _files(sorted(files, key=lambda f: f["filename"]), ran_ids, base)
    written = {file_id(f["filename"], base) for f in files if f["direction"] == "output"}
    read = {r["@id"] for refs in inputs.values() for r in refs}
    run_inputs = [_ref(i) for i in file_entities if i in read and i not in written]
    run_results = []
    for o in sorted(published, key=lambda o: o["filename"]):
        fid = file_id(o["filename"], base)
        entity = file_entities.setdefault(fid, _file_entity(o, fid))
        # The task output it was copied from, when that task ran in this launch (cached ones are left out).
        source = file_id(o["source_filename"], base) if o.get("source_filename") else None
        if source in file_entities and source != fid:
            entity["isBasedOn"] = _ref(source)
        run_results.append(_ref(fid))
    tools, steps, controls, task_entities = _tasks(list(tasks), agent, inputs, outputs)
    cached = sum(t["final_status"] == "CACHED" for t in tasks)
    unfinished = sum(t["final_status"] not in ("CACHED", *STATUS) for t in tasks)

    root = {
        "@id": root_id, "@type": "Dataset",
        "name": f"GW-RePO run {run.get('run_name') or run['id']}",
        "description": (f"Provenance of Nextflow run {run.get('run_name')} ({run['id']}) of "
                        f"{' '.join(filter(None, [run.get('repository') or 'a local script', run.get('revision_id')]))}, "
                        "exported by GW-RePO. Metadata only: files are described, not included. "
                        f"Left out: {cached} cached tasks (reused from an earlier launch) and "
                        f"{unfinished} tasks that never finished."),
        "datePublished": datetime.now(timezone.utc).isoformat(),
        "license": _ref(LICENSE),
        "conformsTo": [_ref(p) for p, _, _ in PROFILES],
        "mainEntity": _ref(wf_id),
        "hasPart": [_ref(wf_id), *(_ref(i) for i in file_entities)],
        "mentions": [_ref(action_id), *(_ref(e["@id"]) for e in task_entities if e["@type"] == "CreateAction")],
    }
    keywords = [str(_value(a)) for a in others]
    if keywords:
        root["keywords"] = keywords

    workflow = {
        "@id": wf_id, "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow", "HowTo"],
        "name": run.get("repository") or "main script",
        "programmingLanguage": _ref(NEXTFLOW),
        "input": [_ref(f"#param-{a['key']}") for a in params],
        "hasPart": tools,
        "step": steps,
    }
    if run.get("revision_id"):
        workflow["version"] = run["revision_id"]
    if run.get("repository"):
        workflow["codeRepository"] = run["repository"]

    action = {
        "@id": action_id, "@type": "CreateAction",
        "name": f"Run of {run.get('repository') or 'main script'}",
        "description": f"Nextflow run {run.get('run_name')}, profile {run.get('profile')}, final state {run.get('final_state')}",
        "instrument": _ref(wf_id),
        "actionStatus": STATUS.get(run.get("final_state"), "http://schema.org/ActiveActionStatus"),
        "object": [*(_ref(f"#pv-{a['key']}") for a in params), *run_inputs],
        "result": run_results,
        "additionalProperty": [_ref(f"#annotation-{a['key']}") for a in others],
    }
    if run.get("start_time") is not None:
        action["startTime"] = _iso(run["start_time"])
        if run.get("duration") is not None:
            action["endTime"] = _iso(run["start_time"] + run["duration"])
    if agent:
        action["agent"] = agent
    engine_run = {"@id": "#engine-run", "@type": "OrganizeAction", "name": "Nextflow orchestration of the run",
                  "instrument": _ref(ENGINE), "result": _ref(action_id), "object": controls,
                  **{k: action[k] for k in ("startTime", "endTime") if k in action}}
    if run.get("final_state") in STATUS:
        engine_run["actionStatus"] = STATUS[run["final_state"]]

    graph = [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": _ref(root_id),
         "conformsTo": [_ref("https://w3id.org/ro/crate/1.1"), _ref("https://w3id.org/workflowhub/workflow-ro-crate/1.0")]},
        root,
        *({"@id": p, "@type": "CreativeWork", "name": name, "version": version} for p, name, version in PROFILES),
        {"@id": LICENSE, "@type": "CreativeWork", "name": "Creative Commons Attribution 4.0 International"},
        workflow,
        {"@id": NEXTFLOW, "@type": "ComputerLanguage", "name": "Nextflow",
         "identifier": _ref("https://www.nextflow.io/"), "url": _ref("https://www.nextflow.io/"),
         **({"version": run["nextflow_version"]} if run.get("nextflow_version") else {})},
        action,
        {"@id": ENGINE, "@type": "SoftwareApplication", "name": "Nextflow", "url": _ref("https://www.nextflow.io/"),
         **({"softwareVersion": run["nextflow_version"]} if run.get("nextflow_version") else {})},
        engine_run,
        *task_entities,
        *file_entities.values(),
    ]
    if institute:
        graph.append({"@id": f"#institute-{institute}", "@type": "Organization", "name": institute})
    for a in params:
        name = a["key"][len("params."):]
        graph.append({"@id": f"#param-{a['key']}", "@type": "FormalParameter", "name": name,
                      "additionalType": "Text" if a.get("value_text") is not None else "Float"})
        graph.append({"@id": f"#pv-{a['key']}", "@type": "PropertyValue", "name": name, "value": _value(a),
                      "exampleOfWork": _ref(f"#param-{a['key']}")})
    for a in others:
        graph.append({"@id": f"#annotation-{a['key']}", "@type": "PropertyValue", "name": a["key"], "value": _value(a)})
    for entity in graph:  # drop empty lists, unwrap one-item lists (RO-Crate prefers single values)
        for key, value in list(entity.items()):
            if value == []:
                del entity[key]
            elif isinstance(value, list) and len(value) == 1:
                entity[key] = value[0]
    # The workflow-run terms define resourceUsage, which the RO-Crate 1.1 context lacks.
    return {"@context": ["https://w3id.org/ro/crate/1.1/context", "https://w3id.org/ro/terms/workflow-run"],
            "@graph": graph}
