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
import nextflow.config.spec.ConfigOption
import nextflow.config.spec.ConfigScope
import nextflow.config.spec.ScopeName
import nextflow.script.dsl.Description

/**
 * Configuration for the {@code nf-gwrepo} plugin, under the {@code gwrepo} config scope.
 */
@ScopeName('gwrepo')
@Description('''
    The `gwrepo` scope configures the `nf-gwrepo` plugin, which streams provenance and
    resource-usage data to a GW-RePO API as the workflow runs.
''')
@CompileStatic
class GwRepoConfig implements ConfigScope {

    @ConfigOption
    @Description('Enable streaming to the GW-RePO API (default: `true` if the plugin is loaded).')
    final boolean enabled

    @ConfigOption
    @Description('Base URL of the GW-RePO API, e.g. `http://localhost:80`.')
    final String endpoint

    @ConfigOption
    @Description('Bearer token for the GW-RePO API. Falls back to the `GWREPO_API_KEY` environment variable.')
    final String apiKey

    @ConfigOption
    @Description('Institute identifier recorded with every execution, e.g. `DKFZ`.')
    final String institute

    @ConfigOption
    @Description('Data-size tag recorded with the workflow execution: `small`, `medium`, `large`, or `mixed`.')
    final String dataSizeTag

    /* required by extension point -- do not remove */
    GwRepoConfig() {}

    GwRepoConfig(Map opts) {
        enabled = opts.enabled != null ? opts.enabled as boolean : true
        endpoint = opts.endpoint ?: 'http://localhost:80'
        apiKey = opts.apiKey ?: System.getenv('GWREPO_API_KEY')
        institute = opts.institute ?: 'UNKNOWN'
        dataSizeTag = opts.dataSizeTag ?: 'mixed'
    }
}
