import typer
import requests
import json
import subprocess
import re
#import hashlib
import os
import xxhash
from urllib.parse import urlparse, unquote
from pathlib import Path
from datetime import datetime

app = typer.Typer()

API_BASE_URL = "http://localhost:8000"

# Converts duration as represented in 'nextflow log' output to seconds
def duration_to_seconds(duration: str) -> float:
    if duration == "-":
        return 0.0  
    
    pattern = re.compile(r'(?:(\d+\.?\d*)d)?\s*(?:(\d+\.?\d*)h)?\s*(?:(\d+\.?\d*)m)?\s*(?:(\d+\.?\d*)s)?')
    match = pattern.fullmatch(duration.strip())

    if not match:
        raise ValueError(f"Error in converting duration: {duration}")

    days = float(match.group(1)) if match.group(1) else 0
    hours = float(match.group(2)) if match.group(2) else 0
    minutes = float(match.group(3)) if match.group(3) else 0
    seconds = float(match.group(4)) if match.group(4) else 0

    return days * 86400 + hours * 3600 + minutes * 60 + seconds

# Extract Nextflow version from BCO provenance file
def get_nextflow_version(bco_data: dict):
    """Get Nextflow version from BCO file"""
    nextflow_info = next((item for item in bco_data['execution_domain']['software_prerequisites'] if item['name'] == 'Nextflow'), None)
    nextflow_version = nextflow_info['version'] if nextflow_info else None
    return nextflow_version

# Get file path of trace file from log file
def get_trace_filepath(log_file: Path) -> Path | None:
    pattern = re.compile(r"trace file: (/.+\.txt)")
    with open(log_file, "r") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                return Path(match.group(1))
    return None    

# Extract latest workflow execution info from 'nextflow log'
def get_nextflow_log(log_file: Path, bco_data: dict):
    """Get workflow execution details using nextflow log command"""
    result = subprocess.run(["nextflow", "log"], capture_output=True, text=True)
    if result.returncode != 0:
        typer.echo("Error running 'nextflow log'", err=True)
        return None
    
    lines = result.stdout.strip().split("\n")
    headers = [item.strip() for item in lines[0].split("\t")]
    latest_run = [item.strip() for item in lines[-1].split("\t")]

    execution_data = dict(zip(headers, latest_run))

    return {
        "id": execution_data.get("SESSION ID"),
        "start_time": datetime.strptime(execution_data["TIMESTAMP"], "%Y-%m-%d %H:%M:%S").timestamp(),
        "duration": duration_to_seconds(execution_data.get("DURATION")),
        "run_name": execution_data.get("RUN NAME"),
        "nextflow_version": get_nextflow_version(bco_data),
        "revision_id": execution_data.get("REVISION ID"),
        "final_state": execution_data.get("STATUS"),
    }

# Extract process execution data from Nextflow trace file
def get_process_execution_data(trace_file, workflow_id): 
    """Parse Nextflow trace file and extract process execution data"""
    import uuid
    
    with open(trace_file, "r") as f:
        lines = f.readlines()

    headers = lines[0].strip().split("\t")
    data = [dict(zip(headers, line.strip().split("\t"))) for line in lines[1:]]

    process_execution_data = []
    for item in data:
        process_execution_data.append({
            "id": item.get('hash'),  
            "workflow_execution_id": workflow_id,  
            "process_name": item.get("process"),
            "module_name": item.get("module"),
            "container_name": item.get("container"),
            "final_status": item.get("status"),
            "exit_code": int(item.get("exit")),
            "start_time": datetime.strptime(item["start"], "%Y-%m-%d %H:%M:%S.%f").timestamp(),  
            "duration": float(duration_to_seconds(item.get("duration"))),
            "cpus_requested": int(item.get("cpus")),
            "time_requested": float(duration_to_seconds(item.get("time", "0s"))),  
            "storage_requested": parse_memory_value(item.get("disk", "0 MB")),  
            "memory_requested": parse_memory_value(item.get("memory", "0 MB")),  
            "realtime": float(item.get("realtime", "0s").rstrip("s")),
            "queue_name": item.get("queue"),
            "percent_cpu": float(item.get("%cpu").rstrip("%")),
            "percent_memory": float(item.get("%mem").rstrip("%")),
            "peak_rss": parse_memory_value(item.get("peak_rss")),
            "peak_vmem": parse_memory_value(item.get("peak_vmem")),
            "read_char": parse_memory_value(item.get("rchar")),
            "write_char": parse_memory_value(item.get("wchar")),
        })    
    return process_execution_data

def parse_memory_value(value):
    """Convert memory values to numeric values in MB"""
    if value == "-" or not value:
        return None
    try:
        num, unit = value.split()
        num = float(num)
        unit = unit.upper()
        conversion_factors = {"KB": 1/1024, "MB": 1, "GB": 1024, "TB": 1024**2}
        return num * conversion_factors.get(unit, 1)  
    except Exception:
        return None

# Convert BCO process id to process id format used in the trace file
def extract_process_id(name: str) -> str:
    return f"{name[:2]}/{name[2:8]}"

def get_file_xxhash128(filename):
    with open(filename, 'rb', buffering=0) as f:
        h = xxhash.xxh128()
        # Read the file in chunks to avoid memory issues with large files
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
        return h.hexdigest()

def get_directory_xxhash128(dirname):
    files = []
    for root, _, filenames in os.walk(dirname):
        for filename in filenames:
            files.append(os.path.join(root, filename))
    files.sort()
    
    xxhash_values = []
    for file in files:
        try:
            file_hash = xxhash.xxh128()
            with open(file, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    file_hash.update(chunk)
            xxhash_values.append(f"{file_hash.hexdigest()} {file}")
        except Exception as e:
            print(f"Skipping {file}: {e}")
    
    final_hash = xxhash.xxh128()
    for line in xxhash_values:
        final_hash.update(line.encode())
    return final_hash.hexdigest()

def get_obj_xxhash128(obj):
    parsed_url = urlparse(obj).path
    full_path = unquote(parsed_url)
    if os.path.isfile(full_path):
        return get_file_xxhash128(full_path)
    elif os.path.isdir(full_path):
        return get_directory_xxhash128(full_path)
    else:
        return None

# Extract provenance from BCO file
def get_provenance_data(bco_data):
    process_executions_inputs = []
    process_executions_outputs = []

    for step in bco_data.get("description_domain", {}).get("pipeline_steps", []):
        process_id = extract_process_id(step["name"])
    
        input_files = [file["uri"] for file in step.get("input_list", [])]
        output_files = [file["uri"] for file in step.get("output_list", [])]
    
        for input_file in input_files:          
            xxhash128 = get_obj_xxhash128(input_file)  
            process_executions_inputs.append({
                "process_execution_id": process_id,
                "filename": input_file,
                "xxhash128": xxhash128,  
            })
        for output_file in output_files:
            xxhash128 = get_obj_xxhash128(output_file)  
            process_executions_outputs.append({
                "process_execution_id": process_id,
                "filename": output_file,
                "xxhash128": xxhash128, 
            })

    return (process_executions_inputs, process_executions_outputs)



@app.command()
def submit(log_file: Path, bco_file: Path):
    """Submit Nextflow workflow and process execution information to GW-RePO API"""
    with open(bco_file, "r") as f:
        bco_data = json.load(f)

    # Get workflow execution data and submit to API
    workflow_execution_data = get_nextflow_log(log_file, bco_data)
    response = requests.post(f"{API_BASE_URL}/workflows/", json=workflow_execution_data)
    if response.status_code != 200:
        typer.echo("Failed to submit workflow execution", err=True)
        return
    typer.echo("Workflow execution submitted successfully")

    # Get process execution data and submit to API
    trace_file = get_trace_filepath(log_file)
    typer.echo (f"Processing trace file: {trace_file}")
    workflow_id = workflow_execution_data["id"]
    process_execution_data = get_process_execution_data(trace_file, workflow_id)
    for entry in process_execution_data:
        response = requests.post(f"{API_BASE_URL}/processes/", json=entry)
        if response.status_code != 200:
            typer.echo(f"Failed to submit process execution: {entry['process_name']}", err=True)
            return
        typer.echo(f"Process execution submitted successfully: {entry['process_name']}")
    typer.echo("All process executions submitted successfully")

    # Get provenance data and submit to API
    typer.echo (f"Processing provenance file: {bco_file}") 
    (file_inputs, file_outputs) = get_provenance_data(bco_data)
    for entry in file_inputs:
        response = requests.post(f"{API_BASE_URL}/input_files/", json=entry)
        if response.status_code != 200:
            typer.echo(f"Failed to submit input files: {entry['filename']}", err=True)
            return
        typer.echo(f"Input files submitted successfully: {entry['filename']}")
    for entry in file_outputs:
        response = requests.post(f"{API_BASE_URL}/output_files/", json=entry)
        if response.status_code != 200:
            typer.echo(f"Failed to submit output files: {entry['filename']}", err=True)
            return
        typer.echo(f"Output files submitted successfully: {entry['filename']}")
    typer.echo("All input and output files submitted successfully")

if __name__ == "__main__":
    app()
