# nf-gwrepo

Nextflow plugin that streams provenance and resource-usage data to a
[GW-RePO](../../README.md) API **as a pipeline runs**, replacing the post-hoc
`client.py` trace/BCO parsing.

Status: **Step 2** — workflow + per-task resource metrics are streamed to the API.
`onFlowCreate` registers the institute and a `WorkflowExecution` stub, `onTaskComplete`
/ `onTaskCached` POST one `ProcessExecution` row per task, `onFlowComplete` re-POSTs the
workflow with `duration` / `final_state`. Provenance (input/output file checksums) is
Step 3. See [`../../PLAN.md`](../../PLAN.md) and
[`../../doc/step0-lineage-field-mapping.md`](../../doc/step0-lineage-field-mapping.md).

POSTs go through a single background thread (`GwRepoClient`) with a bounded queue and
retry/backoff, so the task-completion path never blocks on the network.

## Layout

| Path | Purpose |
|---|---|
| `src/main/groovy/nextflow/gwrepo/GwRepoPlugin.groovy` | pf4j plugin entry point |
| `.../GwRepoConfig.groovy` | `gwrepo { ... }` config scope (`enabled`, `endpoint`, `apiKey`, `institute`, `dataSizeTag`) |
| `.../GwRepoObserverFactory.groovy` | registers the observer (`TraceObserverFactoryV2`) |
| `.../GwRepoObserver.groovy` | `TraceObserverV2` — the lifecycle hooks |
| `nf-gwrepo-test/` | minimal pipeline for local verification |

## Build

```bash
make assemble        # ./gradlew assemble
make test            # ./gradlew test
make install         # install into ~/.nextflow/plugins for local runs
```

## Try it locally

```bash
docker compose up -d --build        # from the repo root -- starts the GW-RePO API
make install
cd nf-gwrepo-test
export GWREPO_API_KEY=$(grep API_KEY ../../../.env | cut -d= -f2)
nextflow run main.nf
```

Expect `[nf-gwrepo] workflow <id> registered` / `finalized` lines in the console, and
rows at `GET /workflows/<id>` and `GET /processes/`.

## Configuration

```groovy
plugins {
    id 'nf-gwrepo'
}

gwrepo {
    enabled     = true                    // default when the plugin is loaded
    endpoint    = 'http://localhost:80'   // GW-RePO API base URL
    apiKey      = secrets.GWREPO_API_KEY  // or the GWREPO_API_KEY env var
    institute   = 'DKFZ'
    dataSizeTag = 'mixed'                 // small | medium | large | mixed
}
```
