# gw-repo-prototype
An API prototype for storing provenance and resource usage information from Nextflow workflow executions

# API deployment (not needed if some deployment of the API is already available to you)

Add a `.env` files to `api/` with these variable definitions:

```bash
API_KEY='add-the-desired-api-key-here'
DATABASE_URL='postgresql://postgres:postgres-password@hostname/database-name'
```

Add a `.env` file to `db/` (directory needs to be created):

```bash
POSTGRES_PASSWORD=postgres-password
POSTGRES_DB=database-name
```

Start the containers:

```bash
docker compose up -d --build
```

# Client usage

## Requirements

Install the requirements:

```bash
pip install typer requests xxhash python-dotenv
```

## Nextflow configuration

One needs to enable process trace and the nf-prov plugin with BCO output in `nextflow.config`:
```groovy
plugins {
    id 'nf-prov'
}

prov {
    enabled = true
    formats {
        bco {
            file = "bco-${new Date().format('yyyyMMdd')}-${System.nanoTime().toString().take(8)}.json"
        }
    }
}

trace {
    enabled = true
    fields = 'hash,process,name,status,exit,module,container,attempt,submit,start,complete,duration,cpus,time,disk,memory,realtime,queue,%cpu,%mem,peak_rss,peak_vmem,rchar,wchar'
}
```

After that you can run your workflows as usual.

## Client configuration

Create a `.env` file in the `client/` directory with the following variables:

```bash
API_BASE_URL=http://localhost:80
API_KEY=your_api_key_here
```

Alternatively, you can set these as environment variables or provide the API key as a command line argument.

## Client usage

`client.py` can be used for extracting the execution metrics and provenance information and sending them to the API:

```
python client.py submit <log_file> <bco_file> [--api-key <your_api_key>]
```

Parameters:

- `log_file`: Path to the Nextflow log file (`.nextflow.log`)
- `bco_file`: Path to the BCO provenance file (`bco-*.json`)
- `--api-key`: API key for authentication (optional if set in environment)
