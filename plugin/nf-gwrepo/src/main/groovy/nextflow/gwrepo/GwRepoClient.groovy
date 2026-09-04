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

import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.BlockingQueue
import java.util.concurrent.TimeUnit

import groovy.json.JsonOutput
import groovy.transform.CompileStatic
import groovy.util.logging.Slf4j

/**
 * Fire-and-forget channel to the GW-RePO API.
 *
 * Work (POSTs, and the file hashing that precedes provenance POSTs) is queued and
 * run on a single background thread so the workflow's task-completion path never
 * blocks on network or disk I/O (nf-lineage's synchronous store writes are the
 * anti-pattern this avoids).
 */
@Slf4j
@CompileStatic
class GwRepoClient {

    private static final int QUEUE_CAPACITY = 10_000
    private static final int MAX_RETRIES = 3
    /** After this many consecutive give-ups (each already {@code MAX_RETRIES} failed
     *  attempts) the API is treated as down for the rest of the run: further sends are
     *  dropped without an HTTP attempt. Keeps a misconfigured / unreachable endpoint
     *  from adding minutes of retry backoff to a run. */
    private static final int CIRCUIT_THRESHOLD = 3

    private final String endpoint
    private final String apiKey
    private final HttpClient http
    private final BlockingQueue<Runnable> queue = new ArrayBlockingQueue<Runnable>(QUEUE_CAPACITY)
    private final Thread worker
    private volatile boolean running = true

    /** consecutive failed sends; touched only by the worker thread */
    private int consecutiveFailures = 0
    private boolean circuitOpen = false

    GwRepoClient(String endpoint, String apiKey) {
        this.endpoint = endpoint?.replaceAll('/+$', '') ?: ''
        this.apiKey = apiKey ?: ''
        this.http = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(10))
            .build()
        this.worker = new Thread({ -> run() } as Runnable, 'nf-gwrepo-http')
        this.worker.daemon = true
        this.worker.start()
    }

    /** Queue a POST. Returns immediately; drops it (with a warning) if the queue is full. */
    void post(String path, Map payload) {
        final body = JsonOutput.toJson(payload.findAll { it.value != null })
        submit({ -> send(path, body) } as Runnable)
    }

    /** Queue arbitrary work (e.g. hash files, then {@link #post}) onto the background thread. */
    void submit(Runnable job) {
        if( !queue.offer(job) )
            log.warn "[nf-gwrepo] work queue full -- dropping a task"
    }

    private void run() {
        while( running || !queue.isEmpty() ) {
            final job = queue.poll(500, TimeUnit.MILLISECONDS)
            if( job == null )
                continue
            try {
                job.run()
            }
            catch( Exception e ) {
                log.warn "[nf-gwrepo] queued job failed: ${e.message}"
            }
        }
    }

    private void send(String path, String body) {
        if( circuitOpen ) {
            log.debug "[nf-gwrepo] POST ${path} dropped -- API marked unreachable"
            return
        }

        final req = HttpRequest.newBuilder(URI.create(endpoint + path))
            .timeout(Duration.ofSeconds(30))
            .header('Content-Type', 'application/json')
            .header('Authorization', "Bearer ${apiKey}")
            .POST(HttpRequest.BodyPublishers.ofString(body))
            .build()

        for( int attempt = 1; attempt <= MAX_RETRIES; attempt++ ) {
            try {
                final resp = http.send(req, HttpResponse.BodyHandlers.ofString())
                final code = resp.statusCode()
                if( code < 300 ) {
                    log.debug "[nf-gwrepo] POST ${path} -> ${code}"
                    consecutiveFailures = 0
                    return
                }
                if( code < 500 ) {
                    log.warn "[nf-gwrepo] POST ${path} -> ${code} ${resp.body()} (not retrying)"
                    consecutiveFailures = 0
                    return
                }
                log.warn "[nf-gwrepo] POST ${path} -> ${code} (attempt ${attempt}/${MAX_RETRIES})"
            }
            catch( Exception e ) {
                final msg = e.message ?: e.class.simpleName
                log.warn "[nf-gwrepo] POST ${path} failed: ${msg} (attempt ${attempt}/${MAX_RETRIES})"
            }
            if( attempt < MAX_RETRIES )
                sleepBackoff(attempt)
        }
        log.error "[nf-gwrepo] POST ${path} gave up after ${MAX_RETRIES} attempts"
        if( ++consecutiveFailures >= CIRCUIT_THRESHOLD ) {
            circuitOpen = true
            log.warn "[nf-gwrepo] ${consecutiveFailures} POSTs failed in a row -- treating the API as down, dropping the rest of this run's data"
        }
    }

    private static void sleepBackoff(int attempt) {
        try { Thread.sleep(1000L * (1L << (attempt - 1))) }
        catch( InterruptedException e ) { Thread.currentThread().interrupt() }
    }

    /** Flush the queue and stop the worker. Blocks up to {@code timeoutSeconds}. */
    void shutdown(int timeoutSeconds) {
        running = false
        try { worker.join(timeoutSeconds * 1000L) }
        catch( InterruptedException e ) { Thread.currentThread().interrupt() }
        if( worker.isAlive() )
            log.warn "[nf-gwrepo] HTTP worker still busy after ${timeoutSeconds}s -- ${queue.size()} POST(s) may be lost"
    }
}
