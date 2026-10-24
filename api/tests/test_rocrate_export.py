"""Unit tests for api/rocrate_export.py. Run from the repo root:
    python -m pytest api/tests"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import rocrate_export as rc  # noqa: E402

ROOT = 'http://gwrepo.example/workflows/wf1/'
RUN = dict(id='wf1', run_name='happy_turing', repository='https://github.com/nf-core/rnaseq', commit_id='abc123',
           revision_id='3.26.0', script_id='s1', profile='docker', nextflow_version='26.04.6',
           final_state='COMPLETED', institute_id='GreenRobust', start_time=1792269174.916, duration=60.5)
ANNOTATIONS = [dict(key='params.aligner', value_text='star_salmon', value_num=None),
               dict(key='params.max_cpus', value_text=None, value_num=8.0),
               dict(key='params.genomes.R64-1-1.fasta', value_text='s3://igenomes/fa', value_num=None),
               dict(key='campaign', value_text='yeast-variations', value_num=None),
               dict(key='depth', value_text='250k', value_num=None)]


def task(id, process, status='COMPLETED', start=100.0, attempt=1, exit_code=0):
    return dict(id=id, process_name=f'WF:{process}', task_name=f'WF:{process} (s1)', final_status=status,
                exit_code=exit_code, attempt=attempt, start_time=start, duration=5.0, complete_time=start + 5.0,
                code_checksum=f'cs-{process}', realtime=4.2, percent_cpu=150.0, peak_rss=1.5, read_bytes=2048,
                memory_requested=None)  # as stored: seconds and MiB; memory not recorded


# B starts first; A fails once (exit 137), then succeeds; C was cached; D never finished.
TASKS = [task('a1', 'A', 'FAILED', start=110, exit_code=137), task('a2', 'A', start=120, attempt=2),
         task('b1', 'B', start=100), task('c1', 'C', 'CACHED', start=50), task('d1', 'D', 'NEW', start=0, exit_code=-1)]


def entities(crate):
    return {e['@id']: e for e in crate['@graph']}


def as_list(value):
    """One-item lists are written as single values."""
    return value if isinstance(value, list) else [value]


def test_root_and_workflow():
    by_id = entities(rc.build_crate(RUN, ANNOTATIONS, ROOT))
    root = by_id[ROOT]
    wf_id = 'https://github.com/nf-core/rnaseq/blob/abc123/main.nf'
    assert by_id['ro-crate-metadata.json']['about'] == {'@id': ROOT}
    assert root['mainEntity'] == {'@id': wf_id}
    assert {'@id': 'https://w3id.org/ro/wfrun/workflow/0.5'} in root['conformsTo']
    assert {'@id': 'https://w3id.org/ro/wfrun/provenance/0.5'} in root['conformsTo']
    assert root['license'] == {'@id': rc.LICENSE} and root['name'] and root['description'] and root['datePublished']
    assert root['keywords'] == ['yeast-variations', '250k']
    assert 'ComputationalWorkflow' in by_id[wf_id]['@type'] and by_id[wf_id]['version'] == '3.26.0'
    assert by_id[rc.NEXTFLOW]['version'] == '26.04.6'


def test_run_action():
    by_id = entities(rc.build_crate(RUN, ANNOTATIONS, ROOT))
    action = by_id['#run-wf1']
    assert action['actionStatus'] == 'http://schema.org/CompletedActionStatus'
    assert action['startTime'] == '2026-10-17T20:32:54.916+00:00'
    assert action['endTime'] == '2026-10-17T20:33:55.416+00:00'
    assert action['agent'] == {'@id': '#institute-GreenRobust'}


def test_parameters_and_annotations():
    by_id = entities(rc.build_crate(RUN, ANNOTATIONS, ROOT))
    action = by_id['#run-wf1']
    # Parameters, without the genomes catalog, each realising a FormalParameter of the workflow.
    assert action['object'] == [{'@id': '#pv-params.aligner'}, {'@id': '#pv-params.max_cpus'}]
    assert by_id['#pv-params.max_cpus']['value'] == 8.0
    assert by_id['#pv-params.aligner']['exampleOfWork'] == {'@id': '#param-params.aligner'}
    assert by_id['#param-params.aligner']['name'] == 'aligner'
    assert not any('genomes' in i for i in by_id)
    # Other annotations as additionalProperty key/value pairs.
    assert action['additionalProperty'] == [{'@id': '#annotation-campaign'}, {'@id': '#annotation-depth'}]
    assert by_id['#annotation-campaign']['value'] == 'yeast-variations'


def test_tasks_tools_and_steps():
    crate = rc.build_crate(RUN, ANNOTATIONS, ROOT, TASKS)
    by_id = entities(crate)
    workflow = by_id[by_id[ROOT]['mainEntity']['@id']]
    # One tool and one step per process that ran, steps in order of first start.
    assert workflow['hasPart'] == [{'@id': '#process-WF:B'}, {'@id': '#process-WF:A'}]
    assert [by_id[s['@id']]['position'] for s in workflow['step']] == ['1', '2']
    assert by_id['#step-WF:A']['workExample'] == {'@id': '#process-WF:A'}
    assert by_id['#process-WF:A']['identifier'] == 'cs-A'
    # One CreateAction per attempt that ran; cached and unfinished tasks left out but counted.
    actions = [e for e in crate['@graph'] if e['@id'].startswith('#task-')]
    assert sorted(a['@id'] for a in actions) == ['#task-a1', '#task-a2', '#task-b1']
    failed = by_id['#task-a1']
    assert failed['actionStatus'] == 'http://schema.org/FailedActionStatus' and failed['error'] == 'Exit code 137'
    assert 'error' not in by_id['#task-a2'] and by_id['#task-a2']['instrument'] == {'@id': '#process-WF:A'}
    # Every action is mentioned from the root; one-item lists become single values.
    assert by_id[ROOT]['mentions'] == [{'@id': i} for i in ('#run-wf1', '#task-b1', '#task-a1', '#task-a2')]
    assert by_id['#control-WF:B']['object'] == {'@id': '#task-b1'}
    assert '1 cached tasks' in by_id[ROOT]['description'] and '1 tasks that never finished' in by_id[ROOT]['description']


def f(direction, task_id, filename, sha256, size=10):
    return dict(direction=direction, process_execution_id=task_id, filename=filename, sha256=sha256, size_bytes=size)


REMOTE = 'https://example.org/data/reads_1.fastq.gz'
LAUNCH = '/home/u/run'
B, A2, C = (f'{LAUNCH}/work/{h}/{h[0] * 30}' for h in ('bb', 'aa', 'cc'))
# B reads a remote file and a file outside the launch directory, and writes x and w, which
# have identical content; A's attempts read x and the cached C's output z; A's second attempt
# writes y and a file already stored relative to the launch directory.
FILES = [f('input', 'b1', REMOTE, 'h-remote'), f('input', 'b1', '/home/u/data/ref.fa', 'h-ref'),
         f('output', 'b1', f'{B}/x.txt', 'h-same'), f('output', 'b1', f'{B}/w copy.txt', 'h-same'),
         f('input', 'a1', f'{B}/x.txt', 'h-same'), f('input', 'a1', f'{C}/z.txt', 'h-z'),
         f('input', 'a2', f'{B}/x.txt', 'h-same'), f('input', 'a2', f'{C}/z.txt', 'h-z'),
         f('output', 'a2', f'{A2}/y.txt', 'h-y', size=None), f('output', 'a2', 'results/report.html', 'h-r'),
         f('output', 'c1', f'{C}/z.txt', 'h-z')]
X, W, Y, Z = (f'#work/{d}/{d[0] * 30}/{n}' for d, n in (('bb', 'x.txt'), ('bb', 'w%20copy.txt'),
                                                         ('aa', 'y.txt'), ('cc', 'z.txt')))


def test_file_ids():
    assert rc.launch_dir(FILES) == LAUNCH
    assert rc.launch_dir([f('input', 'b1', REMOTE, 'h')]) is None
    assert rc.file_id(f'{B}/x.txt', LAUNCH) == X
    assert rc.file_id('/home/u/data/ref.fa', LAUNCH) == '#../data/ref.fa'
    assert rc.file_id(f'{B}/out dir/', LAUNCH) == f'#work/bb/{"b" * 30}/out%20dir/'
    assert rc.file_id('results/report.html', LAUNCH) == '#results/report.html'
    assert rc.file_id('/data/ref.fa') == '#/data/ref.fa'  # no launch directory known
    assert rc.file_id(REMOTE, LAUNCH) == REMOTE


def test_files():
    crate = rc.build_crate(RUN, ANNOTATIONS, ROOT, TASKS, FILES)
    by_id = entities(crate)
    # One File per path: identical content in two paths stays two entities.
    files = {e['@id'] for e in crate['@graph'] if e['@type'] == 'File'}
    assert files == {REMOTE, '#../data/ref.fa', X, W, Z, Y, '#results/report.html'}
    assert not any('/home/u' in json.dumps(e) for e in crate['@graph'])
    assert by_id[X]['identifier'] == by_id[W]['identifier'] == 'sha256:h-same'
    assert by_id[X]['name'] == 'x.txt' and by_id[X]['contentSize'] == '10'
    assert by_id[X]['encodingFormat'] == 'text/plain' and by_id[REMOTE]['encodingFormat'] == 'application/gzip'
    assert 'encodingFormat' not in by_id['#../data/ref.fa']  # extension not in the MIME table
    assert 'contentSize' not in by_id[Y]
    assert files <= {r['@id'] for r in by_id[ROOT]['hasPart']}
    assert by_id['#task-b1']['object'] == [{'@id': '#../data/ref.fa'}, {'@id': REMOTE}]  # sorted by path
    assert by_id['#task-b1']['result'] == [{'@id': W}, {'@id': X}]
    assert by_id['#task-a2']['result'] == [{'@id': Y}, {'@id': '#results/report.html'}]
    # The run's inputs: read in the run, written by none of its tasks (z was written by cached C).
    assert [r for r in by_id['#run-wf1']['object'] if not r['@id'].startswith('#pv-')] == \
        [{'@id': '#../data/ref.fa'}, {'@id': REMOTE}]


def test_published_outputs():
    # B's x published as a copy, and a published directory.
    outputs = [dict(filename=f'{LAUNCH}/results/b/x.txt', sha256='h-same', size_bytes=10,
                    source_process_execution_id='b1', source_filename=f'{B}/x.txt'),
               dict(filename=f'{LAUNCH}/results/a/', sha256='h-dir', size_bytes=None,
                    source_process_execution_id='a2', source_filename=f'{A2}/out/')]
    by_id = entities(rc.build_crate(RUN, ANNOTATIONS, ROOT, TASKS, FILES, outputs))
    assert by_id['#run-wf1']['result'] == [{'@id': '#results/a/'}, {'@id': '#results/b/x.txt'}]
    published = by_id['#results/b/x.txt']
    assert published['identifier'] == by_id[X]['identifier'] and published['encodingFormat'] == 'text/plain'
    assert published['isBasedOn'] == {'@id': X}  # the task output it was copied from
    assert 'isBasedOn' not in by_id['#results/a/']  # its source isn't a file of the crate
    assert {'@id': '#results/a/'} in by_id[ROOT]['hasPart'] and by_id['#results/a/']['name'] == 'a'
    assert 'result' not in entities(rc.build_crate(RUN, ANNOTATIONS, ROOT, TASKS, FILES))['#run-wf1']


def test_lineage_from_files():
    by_id = entities(rc.build_crate(RUN, ANNOTATIONS, ROOT, TASKS, FILES))
    producers = {r['@id']: a for a in by_id if a.startswith('#task-') for r in as_list(by_id[a].get('result', []))}
    edges = {(producers[r['@id']], a) for a in by_id if a.startswith('#task-')
             for r in as_list(by_id[a].get('object', [])) if r['@id'] in producers}
    assert edges == {('#task-b1', '#task-a1'), ('#task-b1', '#task-a2')}


def test_resource_usage():
    crate = rc.build_crate(RUN, ANNOTATIONS, ROOT, TASKS)
    by_id = entities(crate)
    assert 'https://w3id.org/ro/terms/workflow-run' in crate['@context']
    usage = [by_id[r['@id']] for r in by_id['#task-b1']['resourceUsage']]
    assert [u['name'] for u in usage] == ['realTime', 'percentCPU', 'peakRss', 'readBytes']  # memory not recorded
    real, cpu, rss, _ = usage
    # Back in Nextflow's trace units: milliseconds and bytes.
    assert (real['value'], real['unitCode']) == (4200, 'https://qudt.org/vocab/unit/MilliSEC')
    assert (rss['value'], rss['unitCode']) == (1572864, 'https://qudt.org/vocab/unit/BYTE')
    assert (cpu['value'], cpu['unitCode']) == (150.0, 'https://qudt.org/vocab/unit/PERCENT')
    assert real['propertyID'] == 'https://w3id.org/ro/terms/nf-trace#realTime'
    assert '#usage-c1-realTime' not in by_id  # cached tasks are left out


def test_orchestration():
    by_id = entities(rc.build_crate(RUN, ANNOTATIONS, ROOT, TASKS))
    control = by_id['#control-WF:A']
    assert control['instrument'] == {'@id': '#step-WF:A'}
    assert control['object'] == [{'@id': '#task-a1'}, {'@id': '#task-a2'}]
    engine = by_id['#engine-run']
    assert engine['@type'] == 'OrganizeAction' and engine['instrument'] == {'@id': rc.ENGINE}
    assert engine['result'] == {'@id': '#run-wf1'}
    assert engine['object'] == [{'@id': '#control-WF:B'}, {'@id': '#control-WF:A'}]


def test_every_reference_resolves():
    crate = rc.build_crate(RUN, ANNOTATIONS, ROOT, TASKS, FILES)
    by_id = entities(crate)
    external = {p for p, _, _ in rc.PROFILES} | {rc.LICENSE, 'https://www.nextflow.io/',
                                                 'https://w3id.org/ro/crate/1.1'}

    def refs(value):
        if isinstance(value, dict) and set(value) == {'@id'}:
            yield value['@id']
        elif isinstance(value, list):
            for v in value:
                yield from refs(v)

    for entity in crate['@graph']:
        for key, value in entity.items():
            for ref in refs(value):
                assert ref in by_id or ref in external, f"{entity['@id']}.{key} -> {ref}"


def test_local_script_and_running_run():
    run = dict(RUN, repository=None, commit_id=None, revision_id=None, final_state=None, duration=None)
    by_id = entities(rc.build_crate(run, [], ROOT))
    assert by_id[ROOT]['mainEntity'] == {'@id': '#workflow-s1'}
    action = by_id['#run-wf1']
    assert action['actionStatus'] == 'http://schema.org/ActiveActionStatus' and 'endTime' not in action
    assert 'object' not in action and 'keywords' not in by_id[ROOT]  # empty lists dropped
