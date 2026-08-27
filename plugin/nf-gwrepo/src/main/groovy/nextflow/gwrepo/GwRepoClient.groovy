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
 * Fire-and-forget HTTP POST channel to the GW-RePO API.
 *
 * POSTs are queued and sent from a single background thread so the workflow's
 * task-completion path never blocks on network I/O (nf-lineage's synchronous
 * store writes are the anti-pattern this avoids).
 */
@Slf4j
@CompileStatic
class GwRepoClient {

    private static final int QUEUE_CAPACITY = 10_000
    private static final int MAX_RETRIES = 3

    private final String endpoint
    private final String apiKey
    private final HttpClient http
    private final BlockingQueue<Post> queue = new ArrayBlockingQueue<Post>(QUEUE_CAPACITY)
    private final Thread worker
    private volatile boolean running = true

    private static class Post {
        final String path
        final String body
        Post(String path, String body) { this.path = path; this.body = body }
    }

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
        if( !queue.offer(new Post(path, body)) )
            log.warn "[nf-gwrepo] HTTP queue full -- dropping POST ${path}"
    }

    private void run() {
        while( running || !queue.isEmpty() ) {
            final task = queue.poll(500, TimeUnit.MILLISECONDS)
            if( task != null )
                send(task)
        }
    }

    private void send(Post task) {
        final req = HttpRequest.newBuilder(URI.create(endpoint + task.path))
            .timeout(Duration.ofSeconds(30))
            .header('Content-Type', 'application/json')
            .header('Authorization', "Bearer ${apiKey}")
            .POST(HttpRequest.BodyPublishers.ofString(task.body))
            .build()

        for( int attempt = 1; attempt <= MAX_RETRIES; attempt++ ) {
            try {
                final resp = http.send(req, HttpResponse.BodyHandlers.ofString())
                final code = resp.statusCode()
                if( code < 300 ) {
                    log.debug "[nf-gwrepo] POST ${task.path} -> ${code}"
                    return
                }
                if( code < 500 ) {
                    log.warn "[nf-gwrepo] POST ${task.path} -> ${code} ${resp.body()} (not retrying)"
                    return
                }
                log.warn "[nf-gwrepo] POST ${task.path} -> ${code} (attempt ${attempt}/${MAX_RETRIES})"
            }
            catch( Exception e ) {
                log.warn "[nf-gwrepo] POST ${task.path} failed: ${e.message} (attempt ${attempt}/${MAX_RETRIES})"
            }
            if( attempt < MAX_RETRIES )
                sleepBackoff(attempt)
        }
        log.error "[nf-gwrepo] POST ${task.path} gave up after ${MAX_RETRIES} attempts"
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
