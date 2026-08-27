# nf-gwrepo

Nextflow plugin that streams provenance and resource-usage data to a
[GW-RePO](../../README.md) API **as a pipeline runs**, replacing the post-hoc
`client.py` trace/BCO parsing.

Status: **Step 1** — scaffold only. Every observer callback logs; no HTTP calls yet.
See [`../../PLAN.md`](../../PLAN.md) and
[`../../doc/step0-lineage-field-mapping.md`](../../doc/step0-lineage-field-mapping.md).

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
make install
cd nf-gwrepo-test
nextflow run main.nf
```

Expect `[nf-gwrepo] onFlowCreate`, `onProcessCreate`, `onTaskComplete`,
`onFlowComplete` lines in the console/`.nextflow.log`.

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
