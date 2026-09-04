/*
 * Copyright 2026, GW-RePO contributors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package nextflow.gwrepo

import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

import groovy.json.JsonOutput
import groovy.transform.CompileStatic
import groovy.util.logging.Slf4j
import nextflow.Session
import nextflow.processor.TaskRun
import nextflow.script.params.FileOutParam
import nextflow.trace.TraceObserverV2
import nextflow.trace.event.TaskEvent

/**
 * Streams workflow, per-task resource metrics and file provenance to the GW-RePO API
 * as a run proceeds.
 *
 * - {@code onFlowCreate}: register the institute + a {@code WorkflowExecution} stub, and
 *   drop a {@code .gwrepo-run.json} marker in the launch dir for the post-run CO2 importer.
 * - {@code onTaskComplete} / {@code onTaskCached}: one {@code ProcessExecution} row, plus
 *   {@code ProcessExecutionInputFile} / {@code ProcessExecutionOutputFile} rows carrying a
 *   SHA-256 of each file's content (hashed on the background thread).
 * - {@code onFlowComplete}: re-POST the workflow with {@code duration} / {@code final_state}.
 */
@Slf4j
@CompileStatic
class GwRepoObserver implements TraceObserverV2 {

    private final GwRepoConfig config

    private Session session
    private String workflowId
    private GwRepoClient client
    private boolean finalized = false

    /** task_id (as reported in the trace record) -> process_execution_id, for the CO2 importer. */
    private final Map<String,String> taskProcessIds = new ConcurrentHashMap<String,String>()

    GwRepoObserver(GwRepoConfig config) {
        this.config = config
    }

    /** Required so Nextflow collects per-task resource metrics (%cpu, peak_rss, rchar, ...). */
    @Override
    boolean enableMetrics() {
        return true
    }

    @Override
    void onFlowCreate(Session session) {
        this.session = session
        this.workflowId = session.uniqueId.toString()
        this.client = new GwRepoClient(config.endpoint, config.apiKey)

        if( !config.apiKey )
            log.warn "[nf-gwrepo] no API key set (gwrepo.apiKey / GWREPO_API_KEY) -- POSTs will likely be rejected"

        // ensure the institute FK target exists (a fresh GW-RePO DB has no rows)
        client.post('/institutes/', [id: config.institute, name: config.institute])

        client.post('/workflows/', workflowPayload())
        writeRunMarker()
        log.info "[nf-gwrepo] workflow ${workflowId} registered (run_name=${session.runName})"
    }

    /**
     * Write {@code <launchDir>/.gwrepo-run.json} so the post-run CO2 importer
     * (co2-import/submit_co2.py) can attach nf-co2footprint's output to the right rows:
     * {@code workflow_id}, {@code endpoint}, and a {@code task_id -> process_execution_id}
     * map (nf-co2footprint's provenance file identifies tasks only by {@code task_id}).
     * Written at {@code onFlowCreate} (map still empty) and again, complete, at
     * {@code onFlowComplete}. Overwritten each run; the importer takes the newest.
     */
    private void writeRunMarker() {
        try {
            final dir = session.workflowMetadata?.launchDir ?: Paths.get('').toAbsolutePath()
            final marker = dir.resolve('.gwrepo-run.json')
            final json = JsonOutput.toJson([
                workflow_id: workflowId,
                endpoint   : config.endpoint,
                processes  : new LinkedHashMap<String,String>(taskProcessIds),
            ])
            Files.write(marker, json.getBytes(StandardCharsets.UTF_8))
        }
        catch( Exception e ) {
            log.warn "[nf-gwrepo] could not write .gwrepo-run.json: ${e.message} -- pass --workflow-id to the CO2 importer"
        }
    }

    @Override
    void onFlowComplete() {
        // Nextflow calls onFlowComplete twice on a hard task failure (error path + shutdown)
        if( client == null || finalized )
            return
        finalized = true
        final payload = workflowPayload()
        final meta = session.workflowMetadata
        payload.duration = meta?.duration != null ? meta.duration.toMillis() / 1000d : null
        payload.final_state = meta?.success ? 'COMPLETED' : 'FAILED'
        client.post('/workflows/', payload)
        writeRunMarker()
        log.info "[nf-gwrepo] workflow ${workflowId} finalized (${payload.final_state})"
        client.shutdown(30)
    }

    @Override
    void onTaskComplete(TaskEvent event) {
        postProcess(event)
    }

    @Override
    void onTaskCached(TaskEvent event) {
        postProcess(event)
    }

    // ---------------------------------------------------------------- payloads

    private Map workflowPayload() {
        final meta = session.workflowMetadata
        [
            id              : workflowId,
            start_time      : epochSeconds(meta?.start),
            run_name        : session.runName,
            nextflow_version: meta?.nextflow?.version?.toString(),
            revision_id     : meta?.revision ?: meta?.commitId,
            institute_id    : config.institute,
            data_size_tag   : config.dataSizeTag,
        ] as Map
    }

    private void postProcess(TaskEvent event) {
        final trace = event.trace
        if( trace == null ) {
            log.warn "[nf-gwrepo] no trace record for task ${event.handler.task.name} -- skipping"
            return
        }
        final task = event.handler.task
        final processId = "${workflowId}_${task.hash}".toString()

        final taskId = str(trace.get('task_id'))
        if( taskId != null )
            taskProcessIds.put(taskId, processId)

        client.post('/processes/', [
            id                   : processId,
            workflow_execution_id: workflowId,
            institute_id         : config.institute,
            process_name         : str(trace.get('process')) ?: task.processor?.name,
            module_name          : str(trace.get('module')),
            container_name       : str(trace.get('container')),
            final_status         : str(trace.get('status')) ?: 'UNKNOWN',
            exit_code            : exitCode(trace.get('exit')),
            start_time           : ms2s(trace.get('start')) ?: 0d,
            duration             : ms2s(trace.get('duration')) ?: 0d,
            realtime             : ms2s(trace.get('realtime')) ?: 0d,
            cpus_requested       : num(trace.get('cpus')),
            time_requested       : ms2s(trace.get('time')),
            storage_requested    : bytes2mb(trace.get('disk')),
            memory_requested     : bytes2mb(trace.get('memory')),
            queue_name           : str(trace.get('queue')),
            percent_cpu          : num(trace.get('%cpu')) ?: 0d,
            percent_memory       : num(trace.get('%mem')) ?: 0d,
            peak_rss             : bytes2mb(trace.get('peak_rss')) ?: 0d,
            peak_vmem            : bytes2mb(trace.get('peak_vmem')) ?: 0d,
            read_char            : bytes2mb(trace.get('rchar')) ?: 0d,
            write_char           : bytes2mb(trace.get('wchar')) ?: 0d,
            read_bytes           : longOrNull(trace.get('read_bytes')),
            write_bytes          : longOrNull(trace.get('write_bytes')),
            peak_rss_mb          : bytes2mb(trace.get('peak_rss')),
            peak_vmem_mb         : bytes2mb(trace.get('peak_vmem')),
            data_size_tag        : config.dataSizeTag,
        ])

        submitFileProvenance(processId, task)
    }

    // ---------------------------------------------------------------- provenance

    /** Queue SHA-256 hashing + POST of this task's input and output files. */
    private void submitFileProvenance(String processId, TaskRun task) {
        final inputs = new LinkedHashSet<Path>(task.inputFilesMap.values())
        final outputs = new LinkedHashSet<Path>(collectOutputs(task))
        if( inputs.isEmpty() && outputs.isEmpty() )
            return
        client.submit({ ->
            for( Path p : inputs )
                client.post('/input_files/', fileRow(processId, p))
            for( Path p : outputs )
                client.post('/output_files/', fileRow(processId, p))
        } as Runnable)
    }

    private static Map fileRow(String processId, Path path) {
        [process_execution_id: processId, filename: path.toString(), sha256: sha256(path)]
    }

    private static List<Path> collectOutputs(TaskRun task) {
        final result = new ArrayList<Path>()
        for( Object v : task.getOutputsByType(FileOutParam).values() ) {
            if( v instanceof Path )
                result.add((Path) v)
            else if( v instanceof Collection )
                for( Object item : (Collection) v )
                    if( item instanceof Path )
                        result.add((Path) item)
        }
        return result
    }

    private static String sha256(Path path) {
        try {
            if( !Files.isRegularFile(path) ) {
                log.warn "[nf-gwrepo] not a regular file, cannot hash: ${path}"
                return null
            }
            final md = MessageDigest.getInstance('SHA-256')
            final buf = new byte[8192]
            Files.newInputStream(path).withCloseable { InputStream is ->
                int n
                while( (n = is.read(buf)) != -1 )
                    md.update(buf, 0, n)
            }
            return md.digest().encodeHex().toString()
        }
        catch( Exception e ) {
            log.warn "[nf-gwrepo] could not hash ${path}: ${e.message}"
            return null
        }
    }

    // ------------------------------------------------------------- conversions

    private static Double epochSeconds(java.time.OffsetDateTime t) {
        (t != null ? t.toInstant().epochSecond : System.currentTimeMillis() / 1000L) as Double
    }

    private static String str(Object o) {
        if( o == null ) return null
        if( o instanceof Collection ) {
            final joined = ((Collection) o).findAll { it != null }.join(':')
            return joined.isEmpty() ? null : joined
        }
        final s = o.toString().trim()
        (s.isEmpty() || s == '-') ? null : s
    }

    private static Double num(Object o) {
        if( o == null ) return null
        if( o instanceof Number ) return ((Number) o).doubleValue()
        try { return Double.valueOf(o.toString().replace('%', '').trim()) }
        catch( NumberFormatException e ) { return null }
    }

    private static Double ms2s(Object o) {
        final n = num(o)
        n == null ? null : n / 1000d
    }

    private static Double bytes2mb(Object o) {
        final n = num(o)
        n == null ? null : n / (1024d * 1024d)
    }

    private static Integer exitCode(Object o) {
        final n = num(o)
        if( n == null ) return -1
        final i = n.intValue()
        (i == Integer.MAX_VALUE) ? -1 : i
    }

    private static Long longOrNull(Object o) {
        final n = num(o)
        n == null ? null : n.longValue()
    }
}
