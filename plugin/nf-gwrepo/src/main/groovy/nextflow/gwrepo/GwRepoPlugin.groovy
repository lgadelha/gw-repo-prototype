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
import nextflow.plugin.BasePlugin
import org.pf4j.PluginWrapper

/**
 * Entry point for the nf-gwrepo plugin.
 *
 * Streams workflow provenance and resource-usage data to a GW-RePO API as a
 * pipeline runs, replacing the post-hoc {@code client.py} parsing.
 */
@CompileStatic
class GwRepoPlugin extends BasePlugin {

    GwRepoPlugin(PluginWrapper wrapper) {
        super(wrapper)
    }
}
