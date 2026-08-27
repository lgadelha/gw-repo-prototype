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

import groovy.transform.CompileStatic
import groovy.util.logging.Slf4j
import nextflow.Session
import nextflow.trace.TraceObserverV2
import nextflow.trace.event.TaskEvent

/**
 * Streams workflow + per-task resource metrics to the GW-RePO API as a run proceeds.
 *
 * Step 2: workflow stub on flow create, one process row per completed/cached task,
 * workflow re-POST on flow complete for {@code duration}/{@code final_state}.
 * Provenance (input/output file checksums) is Step 3.
 */
@Slf4j
@CompileStatic
class GwRepoObserver implements TraceObserverV2 {

    private final GwRepoConfig config

    private Session session
    private String workflowId
    private GwRepoClient client

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
        log.info "[nf-gwrepo] workflow ${workflowId} registered (run_name=${session.runName})"
    }

    @Override
    void onFlowComplete() {
        if( client == null )
            return
        final payload = workflowPayload()
        final meta = session.workflowMetadata
        payload.duration = meta?.duration != null ? meta.duration.toMillis() / 1000d : null
        payload.final_state = meta?.success ? 'COMPLETED' : 'FAILED'
        client.post('/workflows/', payload)
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

        client.post('/processes/', [
            id                   : "${workflowId}_${task.hash}".toString(),
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
