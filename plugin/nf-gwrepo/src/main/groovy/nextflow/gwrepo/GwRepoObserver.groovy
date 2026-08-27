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
import nextflow.processor.TaskProcessor
import nextflow.trace.TraceObserverV2
import nextflow.trace.event.FilePublishEvent
import nextflow.trace.event.TaskEvent
import nextflow.trace.event.WorkflowOutputEvent

/**
 * Consumes workflow lifecycle events and (eventually) streams them to the GW-RePO API.
 *
 * Step 1: every callback only logs. No HTTP calls, no state.
 */
@Slf4j
@CompileStatic
class GwRepoObserver implements TraceObserverV2 {

    private final GwRepoConfig config

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
        log.info "[nf-gwrepo] onFlowCreate: runName=${session.runName} sessionId=${session.uniqueId} nextflow=${session.workflowMetadata?.nextflow?.version}"
    }

    @Override
    void onFlowBegin() {
        log.info "[nf-gwrepo] onFlowBegin"
    }

    @Override
    void onFlowComplete() {
        log.info "[nf-gwrepo] onFlowComplete"
    }

    @Override
    void onProcessCreate(TaskProcessor process) {
        log.info "[nf-gwrepo] onProcessCreate: ${process.name}"
    }

    @Override
    void onProcessTerminate(TaskProcessor process) {
        log.info "[nf-gwrepo] onProcessTerminate: ${process.name}"
    }

    @Override
    void onTaskPending(TaskEvent event) {
        log.debug "[nf-gwrepo] onTaskPending: ${event.handler.task.name}"
    }

    @Override
    void onTaskSubmit(TaskEvent event) {
        log.debug "[nf-gwrepo] onTaskSubmit: ${event.handler.task.name}"
    }

    @Override
    void onTaskStart(TaskEvent event) {
        log.debug "[nf-gwrepo] onTaskStart: ${event.handler.task.name}"
    }

    @Override
    void onTaskComplete(TaskEvent event) {
        final task = event.handler.task
        log.info "[nf-gwrepo] onTaskComplete: name=${task.name} hash=${task.hash} status=${event.trace?.get('status')} exit=${event.trace?.get('exit')} peak_rss=${event.trace?.get('peak_rss')}"
    }

    @Override
    void onTaskCached(TaskEvent event) {
        log.info "[nf-gwrepo] onTaskCached: name=${event.handler.task.name} hash=${event.handler.task.hash}"
    }

    @Override
    void onFlowError(TaskEvent event) {
        log.info "[nf-gwrepo] onFlowError: name=${event.handler.task.name} exit=${event.trace?.get('exit')}"
    }

    @Override
    void onWorkflowOutput(WorkflowOutputEvent event) {
        log.info "[nf-gwrepo] onWorkflowOutput: name=${event.name}"
    }

    @Override
    void onFilePublish(FilePublishEvent event) {
        log.debug "[nf-gwrepo] onFilePublish: ${event.target}"
    }
}
