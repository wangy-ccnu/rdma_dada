"""C1 portable policy tests; fake only the Linux/RDMA/GPU boundaries.

The compiler fixture exercises the real MS04 resolver and manifest validation.
It does not establish actual compiler, SysV IPC, CUDA or receiver acceptance.
"""
import copy
import argparse
import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from contextlib import contextmanager, ExitStack
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.multiserver.bundle import build_bundle
from scripts.multiserver.ownership import HostJournal
from scripts.multiserver.host_agent import HostAgent
from multiserver_fixture import write_example_inputs

COMPILER = None


@contextmanager
def local_retained_supervisor(root):
    """Real finite Popen children; mock only Linux identity on macOS."""
    from scripts.multiserver.processes import ProcessSupervisor
    supervisor=ProcessSupervisor(str(root))
    with ExitStack() as stack:
        if sys.platform!='linux':
            def identity(pid,**kwargs):
                return dict(pid=pid,start_ticks=1,boot_id='00000000-0000-4000-8000-000000000001',
                            uid=os.getuid(),pgid=os.getpgid(pid),sid=os.getsid(pid))
            stack.enter_context(patch('scripts.multiserver.processes._read_process',side_effect=identity))
            stack.enter_context(patch('scripts.multiserver.processes.sys',SimpleNamespace(platform='linux')))
        try:
            yield supervisor
        finally:
            # Fixture programs are finite even when ownership is unresolved.
            # Test teardown reaps only exact retained handles, never names/PIDs.
            for child in supervisor._children.values():
                child.wait(timeout=5)
            supervisor.close()

try:
    from scripts.multiserver.pipeline_runtime import PipelineRuntime, LinuxPlatform, ring_footprint
except ImportError:
    PipelineRuntime = None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeSupervisor:
    """Retains role-bound identities, with explicit unknown/blocked exits."""
    def __init__(self):
        self.reports = {}
        self.launches = []
        self.signals = []
        self.waits = []
        self.blocked = set()
        self.unknown = set()

    def launch(self, owner, role, argv, cwd, env):
        self.launches.append((copy.deepcopy(owner), role, argv, cwd, env))
        identity = dict(owner=owner, role=role, pid=100 + len(self.launches),
                        start_ticks=10, boot_id='boot', uid=os.getuid(), pgid=1, sid=1)
        exited = role.startswith('ring-')
        result = dict(identity=identity, state='EXITED' if exited else 'RUNNING',
                      returncode=0 if exited else None, cleanup='UNKNOWN', reason=None,
                      signal_sent=False, logs=dict(stdout='/unused', stderr='/unused'))
        self.reports[(owner['generation'], role)] = result
        return copy.deepcopy(result)

    def inspect(self, identity):
        result = copy.deepcopy(self.reports[(identity['owner']['generation'], identity['role'])])
        if identity['role'] in self.unknown:
            result.update(state='UNKNOWN', returncode=None)
        return result

    def wait(self, identity, timeout_seconds):
        self.waits.append(timeout_seconds)
        return self.inspect(identity)

    def signal(self, identity, signum):
        result = self.inspect(identity)
        if result['state'] != 'RUNNING':
            return result
        self.signals.append((identity['owner']['generation'], identity['role'], signum))
        result['signal_sent'] = True
        if identity['role'] not in self.blocked or signum == signal.SIGKILL:
            self.reports[(identity['owner']['generation'], identity['role'])].update(
                state='EXITED', returncode=0 if signum != signal.SIGKILL else -9)
            # Real workers/sink drain naturally when receiver closes raw EOD.
            if identity['role'] == 'receiver':
                for (generation, role), report in self.reports.items():
                    if generation == identity['owner']['generation'] and role not in self.blocked:
                        report.update(state='EXITED', returncode=0)
        return result


class FakePlatform:
    def __init__(self):
        self.foreign = False
        self.ready_roles = {'sink', 'gpu', 'unpack', 'receiver'}
        self.owned = []
        self.removed = []
        self.results = {}
        self.proven_leaf = True
        self.resource_unknown = False

    def check_host(self, profile, inventory):
        pass

    def ensure_absent(self, footprint):
        if self.foreign:
            raise ValueError('FOREIGN_IPC')

    def capture_ring(self, ring, report, footprint):
        token = dict(ring=copy.deepcopy(ring), identity=report['identity'], objects=footprint)
        self.owned.append(token)
        return token

    def remove_ring(self, token):
        if self.resource_unknown:
            return False
        self.removed.append(token)
        return True

    def ready(self, role, report, directory, ids, ring):
        return role in self.ready_roles

    def result(self, role, report, directory, ids):
        return copy.deepcopy(self.results.get(role))

    def descendants_gone(self, reports, binaries):
        return self.proven_leaf


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PipelineRuntime, 'C1 PipelineRuntime is not implemented')
        self.temp = tempfile.TemporaryDirectory(prefix='ms07c-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        inputs = write_example_inputs(self.root / 'inputs')
        if getattr(self,'duration',None):
            source=json.loads(inputs[0].read_text())
            source['observation']['duration_seconds']=self.duration
            inputs[0].write_text(json.dumps(source))
        deployments = json.loads(inputs[1].read_text())
        deployments['feature_root'] = str(self.root)
        for profile in deployments['profiles'].values():
            profile['rings'] = dict(raw_key='0x1100', compute_key='0x1200', output_key='0x1300')
            profile['cpu']['unpack'] = [1, 6]  # coordinator and writer; workers are separate
        inputs[1].write_text(json.dumps(deployments))
        self.bundle_path = self.root / 'bundle'
        self.bundle = build_bundle(inputs[0], inputs[1], self.bundle_path, inventory_path=inputs[2])
        instance = self.bundle['instances'][0]
        self.owner = dict(run_id='run', bundle_id=self.bundle['bundle_id'], server_id='node-a',
                          gpu_id=0, observation_sha256=instance['observation_sha256'], generation='one')
        self.resources = dict(gpu_uuid='GPU-FAKE-A', cpus=[
            dict(logical_cpu=n, socket_id=0, core_id=n) for n in (0, 1, 2, 3, 4, 5, 6, 12)],
            ipc_keys=sorted({key for base in (0x1100, 0x1200, 0x1300)
                             for h in (0, 1) for key in
                             [base+h, base+h+0x10000, base+h+0x20000] +
                             [base+h+i*0x10000 for i in range(10, 18)]}),
            flow={k:instance['deployment']['flow'][k] for k in ('physical_port','match')},
            run_dir=str(self.root / 'runs/node-a-gpu0'))
        # The independent fixture inventory uses socket_id=cpu//8, core_id=cpu%8.
        inventory = json.loads(inputs[2].read_text())['hosts'][0]
        for cpu in self.resources['cpus']:
            source = inventory['cpus'][str(cpu['logical_cpu'])]
            cpu.update(socket_id=source['socket_id'], core_id=source['core_id'])
        executable = str(Path(sys.executable).resolve())
        binary = dict(path=executable, sha256=digest(Path(executable)), leaf=True)
        self.config = dict(runtime=dict(kind='pipeline', binaries={
            name: copy.deepcopy(binary) for name in
            ('compiler', 'numactl', 'dada_db', 'dada_dbnull', 'gpu', 'unpack', 'receiver')},
            environment={}, ready_timeout_seconds=0.03, drain_timeout_seconds=0.03,
            poll_interval_seconds=0.002,
            ipc=dict(max_readers=8, header_buffers=8, exclusive_create=True)), instances=[
                dict({k: self.owner[k] for k in self.owner if k != 'generation'},
                     bundle_path=str(self.bundle_path), resources=self.resources)])
        self.journal = HostJournal(self.root / 'journal.sqlite')
        self.addCleanup(self.journal.close)
        self.journal.claim(self.owner, self.resources)
        self.supervisor = FakeSupervisor()
        self.platform = FakePlatform()
        self.clock = datetime.datetime(2026, 9, 11, tzinfo=datetime.timezone.utc)
        self.runtime = PipelineRuntime(self.config, self.supervisor, journal=self.journal,
                                       platform=self.platform, utc_now=lambda: self.clock)
        self.addCleanup(self.runtime.close)
        self.resolver_patch = patch('scripts.multiserver.pipeline_runtime.PipelineRuntime._run_compiler',
                                    side_effect=lambda session,argv,timeout:self.compile(argv))
        self.resolver_patch.start()
        self.addCleanup(self.resolver_patch.stop)

    def compile(self, argv, **kwargs):
        observation = json.loads(Path(argv[2]).read_text())
        groups=2049 if observation['observation']['duration_seconds']=='1.049088' else 2048
        channels=observation['observation']['nchan']
        record_bytes=channels*1024+32
        output = Path(argv[4]); output.mkdir()
        report = dict(valid=True, schema_version=1, config_id='a'*64, geometry_id='b'*64,
                      observation_id=observation['observation']['observation_id'],
                      inputs=dict(observation=argv[2], wire_profile=str(Path(argv[2]).parent/'wire-profile.json')),
                      derived=dict(expected_groups=groups, compute_block_bytes=channels*131072,
                                   raw_block_bytes=record_bytes*128, raw_record_bytes=record_bytes),
                      checks=[dict(name=n, status='PASS') for n in ('observation_schema', 'wire_contract',
                          'checked_geometry', 'identity_verification', 'header_transform')])
        ids = dict(config_id='a'*64, geometry_id='b'*64)
        rings = dict(raw=dict(key=observation['rings']['raw_key'], block_bytes=record_bytes*128, blocks=8, header_bytes=4096),
                     compute=dict(key=observation['rings']['compute_key'], block_bytes=channels*131072, blocks=8, header_bytes=4096),
                     output=dict(key=observation['rings']['output_key'], block_bytes=channels*1024, blocks=8, header_bytes=4096))
        for name, value in (('resolved_observation.json', ids), ('ring_plan.json', dict(ids, rings=rings)),
                            ('validation_report.json', report)):
            (output/name).write_text(json.dumps(value))
        for name in ('raw', 'unpacked', 'converted', 'beamformed', 'output'):
            (output/(name+'.header')).write_bytes(b'\0'*4096)
        (output/'MANIFEST.sha256').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in sorted(output.iterdir())))
        return subprocess.CompletedProcess(argv, 0, json.dumps(report), '')

    def prepare(self):
        return self.runtime.prepare(self.owner, str(self.bundle_path))

    def start(self):
        return self.runtime.start(self.owner, '2026-09-12-00:00:00', getattr(self,'duration','1.048576'))

    def assert_unallocated_claim_releasable(self, result):
        self.assertEqual(result['state'],'FAILED',result)
        self.assertEqual(result['outcome'],'NOT_APPLIED',result)
        self.assertEqual(result['cleanup'],'CONFIRMED',result)
        self.assertEqual(result['cleanup_evidence'],dict(owner=self.owner,resources=self.resources,
            processes_gone=True,descendants_gone=True,resources_gone=True))
        self.assertFalse(any(result['evidence'].values()))
        self.assertEqual(self.supervisor.launches,[])
        self.assertEqual(self.supervisor.signals,[])
        self.assertEqual(self.platform.owned,[])
        self.assertEqual(self.platform.removed,[])
        self.assertTrue(self.journal.release(self.owner,result['cleanup_evidence'])['released'])

    def finish_results(self, station_counts=None, output_bytes=262144):
        self.platform.results = {
            'receiver': dict(ok=True, committed_bytes=33816576, eod=True),
            'unpack': dict(ok=True, input_eod=True, expected_groups=2048, emitted_bytes=33554432,
                           station_ids=[1000,1001,1002,1003],
                           station_highest_ordinals=[2047]*4,
                           station_accepted_packets=station_counts or [2048]*4, eod=True),
            'gpu': dict(ok=True, input_bytes=33554432, output_bytes=output_bytes,
                        published_blocks=64, input_eod=True, output_eod=True)}

    def test_prepare_orders_consumers_before_ingress_and_binds_argv(self):
        report = self.prepare()
        self.assertEqual(report['state'], 'READY')
        self.assertEqual(report['outcome'], 'APPLIED')
        roles = [item[1] for item in self.supervisor.launches]
        self.assertEqual(roles, ['ring-raw', 'ring-compute', 'ring-output', 'sink', 'gpu', 'unpack', 'receiver'])
        receiver = self.supervisor.launches[-1]
        self.assertEqual(receiver[0], self.owner)
        self.assertEqual(receiver[2][-6:], ['--recv-wr-num', '64', '--poll-batch', '32', '--poll-cpu', '0'])
        self.assertIn('--ready-file', receiver[2])
        self.assertNotIn('CUDA_VISIBLE_DEVICES', receiver[4])
        self.assertEqual(receiver[2][:4], [self.config['runtime']['binaries']['numactl']['path'],
                         '--physcpubind=0', '--membind=0', self.config['runtime']['binaries']['receiver']['path']])
        self.assertEqual(len(self.platform.owned), 3)
        self.assertFalse(any(report['evidence'].values()))
        unpack=next(i for i in self.supervisor.launches if i[1]=='unpack')
        self.assertEqual(unpack[2][-2:],['--thread-cpus','1,3,4,6'])
        raw=self.supervisor.launches[0][2]
        self.assertEqual(raw[1:],['-k','1100','-b','528384','-n','8','-a','4096','-r','1','-c','0'])

    def test_compiler_failure_allocates_nothing(self):
        with patch('scripts.multiserver.pipeline_runtime.PipelineRuntime._run_compiler',
                   return_value=subprocess.CompletedProcess([],7,'','bad')):
            result = self.prepare()
        self.assert_unallocated_claim_releasable(result)

    def test_changed_gpu_binary_is_rejected_before_allocation(self):
        self.runtime.binaries['gpu']['sha256']='0'*64
        result=self.prepare()
        self.assert_unallocated_claim_releasable(result)

    def test_changed_installed_weights_before_start_is_rejected(self):
        self.prepare()
        (self.bundle_path/'instances/node-a/gpu-0/weights.npy').write_bytes(b'changed')
        self.assertEqual(self.start()['outcome'],'REJECTED')

    def test_duration_spelling_must_remain_the_original_value(self):
        self.prepare()
        result=self.runtime.start(self.owner,'2026-09-12-00:00:00','1.0485760')
        self.assertEqual(result['outcome'],'REJECTED')

    def test_foreign_ipc_refused_before_first_allocation(self):
        self.platform.foreign = True
        result = self.prepare()
        self.assert_unallocated_claim_releasable(result)
        self.assertTrue(self.platform.foreign)

    def test_bundle_tamper_and_owner_mismatch_allocate_nothing(self):
        (self.bundle_path/'inventory.json').write_text('{}')
        self.assert_unallocated_claim_releasable(self.prepare())

    def test_bundle_path_binding_failure_releases_only_declared_claim(self):
        result=self.runtime.prepare(self.owner,str(self.root/'wrong-bundle'))
        self.assert_unallocated_claim_releasable(result)

    def test_host_refusal_before_allocation_needs_no_leaf_cleanup_assumption(self):
        self.platform.proven_leaf=False
        with patch.object(self.platform,'check_host',side_effect=ValueError('host validation refused')):
            result=self.prepare()
        self.assert_unallocated_claim_releasable(result)

    def test_unknown_prior_run_directory_cannot_supply_no_attempt_proof(self):
        directory=Path(self.resources['run_dir']);directory.mkdir(parents=True)
        marker=directory/'owner.json';marker.write_text(json.dumps(self.owner))
        original=marker.read_bytes()
        result=self.prepare()
        self.assertEqual(result['state'],'FAILED')
        self.assertEqual(result['outcome'],'UNKNOWN')
        self.assertEqual(result['cleanup'],'UNKNOWN')
        self.assertNotIn('cleanup_evidence',result)
        self.assertEqual(marker.read_bytes(),original)
        self.assertEqual(self.supervisor.launches,[])
        self.assertEqual(self.platform.removed,[])
        self.assertFalse(self.journal.load(self.owner)['released'])

    def test_run_directory_creation_race_keeps_prior_effects_unknown(self):
        directory=Path(self.resources['run_dir'])
        def competing_preparer(profile,inventory):
            directory.mkdir(parents=True)
            (directory/'owner.json').write_text(json.dumps(self.owner))
        with patch.object(self.platform,'check_host',side_effect=competing_preparer):
            result=self.prepare()
        self.assertEqual(result['state'],'FAILED')
        self.assertEqual(result['outcome'],'UNKNOWN')
        self.assertEqual(result['cleanup'],'UNKNOWN')
        self.assertNotIn('cleanup_evidence',result)
        self.assertEqual(self.supervisor.launches,[])
        self.assertEqual(self.platform.removed,[])
        self.assertEqual(json.loads((directory/'owner.json').read_text()),self.owner)

    def test_host_agent_releases_pending_prepare_claim_after_compiler_refusal(self):
        journal=HostJournal(self.root/'unallocated-host.sqlite');self.addCleanup(journal.close)
        deployment=copy.deepcopy(self.config)
        deployment.update(root=str(self.root/'unallocated-host'),stop_timeout_seconds=.03,request_timeout_seconds=.1)
        runtime=PipelineRuntime(deployment,self.supervisor,journal=journal,
                                platform=self.platform,utc_now=lambda:self.clock)
        self.addCleanup(runtime.close)
        host=HostAgent(journal,self.supervisor,runtime,deployment)
        command=dict({k:self.owner[k] for k in self.owner if k!='generation'},
                     expected_generation=None,action_id='unallocated-prepare',operation='prepare')
        with patch('scripts.multiserver.pipeline_runtime.PipelineRuntime._run_compiler',
                   return_value=subprocess.CompletedProcess([],7,'','refused')):
            result=host.handle(command)
        self.assertEqual(result['state'],'FAILED',result)
        self.assertEqual(result['outcome'],'NOT_APPLIED',result)
        self.assertEqual(result['cleanup'],'CONFIRMED',result)
        owner=dict(self.owner,generation=result['generation'])
        record=journal.load(owner)
        self.assertTrue(record['released'])
        self.assertEqual(record['cleanup_evidence']['owner'],owner)
        self.assertEqual(record['cleanup_evidence']['resources'],self.resources)
        self.assertEqual(record['actions']['unallocated-prepare']['outcome'],'NOT_APPLIED')
        self.assertEqual(self.supervisor.launches,[])
        self.assertEqual(self.platform.removed,[])

    def test_ready_timeout_rolls_back_only_owned_instance(self):
        self.platform.ready_roles.remove('gpu')
        result = self.prepare()
        self.assertEqual(result['state'], 'FAILED')
        self.assertNotIn('receiver', [i[1] for i in self.supervisor.launches])
        self.assertTrue(self.platform.removed)
        self.assertTrue(all(i[0]=='one' for i in self.supervisor.signals))

    def test_final_ready_rechecks_previous_consumers(self):
        original=self.platform.ready
        def fail_previous(role,*args):
            if role=='receiver':
                self.supervisor.reports[('one','gpu')].update(state='EXITED',returncode=7)
            return original(role,*args)
        with patch.object(self.platform,'ready',side_effect=fail_previous):
            result=self.prepare()
        self.assertEqual(result['state'],'FAILED')

    def test_same_sequence_cached_report_cannot_change_facts(self):
        prepared = self.prepare()
        first = self.runtime.observe(self.owner)
        second = self.runtime.observe(self.owner)
        if first['sequence'] == second['sequence']:
            self.assertEqual(first, second)
        prepared['evidence']['target_data_complete'] = True
        self.assertFalse(self.runtime.observe(self.owner)['evidence']['target_data_complete'])

    def test_original_schedule_is_required_and_missed_start_rejected(self):
        self.prepare()
        result = self.runtime.start(self.owner, '2026-09-12-00:00:01', '1.048576')
        self.assertEqual(result['outcome'], 'REJECTED')
        self.clock = datetime.datetime(2026,9,12,0,0,0,1,tzinfo=datetime.timezone.utc)
        self.assertEqual(self.start()['outcome'], 'REJECTED')

    def test_deadline_advances_without_observe_and_requires_real_data(self):
        self.prepare(); self.finish_results()
        self.assertEqual(self.start()['state'], 'STARTING')
        self.clock = datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        deadline = time.monotonic()+1
        while not self.supervisor.signals and time.monotonic()<deadline:
            time.sleep(.005)
        self.assertTrue(self.supervisor.signals, 'no autonomous ingress stop')
        result = self.runtime.observe(self.owner)
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertTrue(all(result['evidence'].values()))
        self.assertEqual(result['cleanup_evidence']['owner'], self.owner)
        self.assertEqual(result['cleanup_evidence']['resources'], self.resources)

    def test_missing_station_and_zero_input_never_complete(self):
        self.prepare(); self.finish_results([2048,2048,0,2048]); self.start()
        self.clock = datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        time.sleep(.06)
        result = self.runtime.observe(self.owner)
        self.assertEqual(result['state'], 'FAILED')
        self.assertFalse(result['evidence']['target_data_complete'])

    def test_exit_zero_and_clock_alone_never_complete(self):
        self.prepare(); self.start()
        self.clock = datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        time.sleep(.06)
        self.assertEqual(self.runtime.observe(self.owner)['state'], 'FAILED')

    def test_manual_stop_is_ingress_first_and_never_normal_completion(self):
        self.prepare(); self.finish_results(); self.start()
        result = self.runtime.stop(self.owner, 'graceful', .03)
        self.assertEqual(self.supervisor.signals[0][1], 'receiver')
        self.assertEqual(result['state'], 'INTERRUPTED')
        self.assertEqual(result['cleanup'], 'CONFIRMED')
        self.assertTrue(all(timeout==0 for timeout in self.supervisor.waits),
                        'positive B2 waits hold its global mutex and can delay a peer')

    def test_unknown_process_never_signalled_or_claimed_clean(self):
        self.prepare(); self.supervisor.unknown.add('gpu')
        result = self.runtime.stop(self.owner, 'force', .01)
        self.assertFalse(any(item[1]=='gpu' for item in self.supervisor.signals))
        self.assertNotEqual(result['cleanup'], 'CONFIRMED')
        self.assertNotIn('cleanup_evidence', result)

    def test_transient_unknown_does_not_fail_or_renew_ready_and_can_recover(self):
        prepared=self.prepare()
        self.supervisor.unknown.add('gpu')
        for _ in range(2):
            observed=self.runtime.observe(self.owner)
            self.assertEqual(observed, {k:v for k,v in prepared.items() if k!='outcome'})
        self.assertEqual(self.supervisor.signals,[])
        self.assertEqual(self.platform.removed,[])
        self.supervisor.unknown.remove('gpu')
        recovered=self.runtime.observe(self.owner)
        self.assertEqual(recovered['state'],'READY')
        self.assertGreater(recovered['sequence'],prepared['sequence'])
        self.assertIsNone(recovered['error'])
        self.assertEqual(self.start()['outcome'],'APPLIED')

    def test_persistent_unknown_running_stays_cached_until_authorized_deadline(self):
        self.prepare();self.start()
        self.clock=datetime.datetime(2026,9,12,tzinfo=datetime.timezone.utc)
        running=self.runtime.observe(self.owner)
        self.assertEqual(running['state'],'RUNNING')
        self.supervisor.unknown.add('gpu')
        for _ in range(2):
            self.assertEqual(self.runtime.observe(self.owner),running)
        self.assertEqual(self.supervisor.signals,[])
        self.clock=datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        result=self.runtime.observe(self.owner)
        self.assertEqual(result['state'],'FAILED')
        self.assertEqual(result['cleanup'],'UNKNOWN')
        self.assertNotIn('cleanup_evidence',result)
        self.assertTrue(any(role=='receiver' for _,role,_ in self.supervisor.signals))
        self.assertFalse(any(role=='gpu' for _,role,_ in self.supervisor.signals))

    def test_authoritative_nonzero_exit_still_fails_even_with_another_unknown(self):
        self.prepare();self.start()
        self.supervisor.unknown.add('unpack')
        self.supervisor.reports[('one','gpu')].update(state='EXITED',returncode=7)
        result=self.runtime.observe(self.owner)
        self.assertEqual(result['state'],'FAILED')
        self.assertIn('worker failure',result['error'])
        self.assertEqual(self.supervisor.signals[0][1],'receiver')
        self.assertFalse(any(role=='unpack' for _,role,_ in self.supervisor.signals))

    def test_force_is_bounded_individual_and_failed_drain_visible(self):
        self.prepare(); self.supervisor.blocked.add('gpu')
        result = self.runtime.stop(self.owner, 'graceful', .01)
        self.assertIn(('one','gpu',signal.SIGKILL), self.supervisor.signals)
        self.assertEqual(result['state'], 'INTERRUPTED')
        self.assertFalse(result['evidence']['eod_complete'])

    def test_unknown_descendants_or_resources_prevent_release(self):
        self.prepare(); self.platform.proven_leaf=False
        result = self.runtime.stop(self.owner,'force',.01)
        self.assertNotEqual(result['cleanup'],'CONFIRMED')
        self.assertNotIn('cleanup_evidence',result)

    def test_restart_unknown_owner_does_not_adopt_or_signal(self):
        self.prepare()
        other = PipelineRuntime(self.config,self.supervisor,journal=self.journal,
                                platform=self.platform,utc_now=lambda:self.clock)
        self.addCleanup(other.close)
        result=other.stop(self.owner,'force',.01)
        self.assertEqual(result['cleanup'],'UNKNOWN')
        self.assertEqual(self.supervisor.signals,[])

    def test_launch_exception_after_possible_spawn_remains_unknown(self):
        with patch.object(self.supervisor,'launch',side_effect=OSError('lost spawn identity')):
            result=self.prepare()
        self.assertEqual(result['outcome'],'UNKNOWN')
        self.assertEqual(result['cleanup'],'UNKNOWN')
        self.assertNotIn('cleanup_evidence',result)
        self.assertEqual(self.platform.removed,[])

    def test_partial_ring_create_retains_unknown_and_durable_footprint(self):
        original=self.platform.capture_ring
        def partial(ring,report,footprint):
            if ring['key']=='0x1200':
                raise ValueError('partial create; foreign semaphore won race')
            return original(ring,report,footprint)
        with patch.object(self.platform,'capture_ring',side_effect=partial):
            result=self.prepare()
        self.assertEqual(result['cleanup'],'UNKNOWN')
        self.assertEqual(len(self.platform.removed),1)
        directory=Path(self.resources['run_dir'])
        intent=json.loads((directory/'ring-compute.intent').read_text())
        self.assertEqual(intent['owner'],self.owner)
        self.assertEqual(intent['ring']['key'],'0x1200')
        self.assertTrue((directory/'ring-raw.objects').exists())

    def test_missing_output_eod_cannot_become_normal_completion(self):
        self.prepare();self.finish_results();self.platform.results['gpu']['output_eod']=False
        self.start();self.clock=datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        time.sleep(.06)
        result=self.runtime.observe(self.owner)
        self.assertEqual(result['state'],'FAILED')
        self.assertFalse(result['evidence']['eod_complete'])

    def test_input_silence_does_not_complete_even_with_filled_zero_buffers(self):
        self.prepare();self.finish_results([0,0,0,0])
        self.platform.results['receiver']['committed_bytes']=0
        self.start();self.clock=datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        time.sleep(.06)
        result=self.runtime.observe(self.owner)
        self.assertEqual(result['state'],'FAILED')
        self.assertFalse(result['evidence']['target_data_complete'])

    def test_exact_expanded_claim_required(self):
        self.runtime.config['instances'][0]['resources']['ipc_keys'].pop()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.supervisor.launches,[])

    def test_cleanup_cannot_change_terminal_evidence_on_retry(self):
        self.prepare();self.finish_results();self.platform.resource_unknown=True
        self.start();self.clock=datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        time.sleep(.06)
        first=self.runtime.observe(self.owner)
        self.assertEqual(first['state'],'COMPLETED')
        self.platform.resource_unknown=False
        last=self.runtime.stop(self.owner,'force',.01)
        self.assertEqual(last['state'],'COMPLETED')
        self.assertEqual(first['evidence'],last['evidence'])

    def test_b3_missed_start_returns_legal_failed_snapshot(self):
        journal=HostJournal(self.root/'b3.sqlite');self.addCleanup(journal.close)
        deployment=copy.deepcopy(self.config)
        deployment.update(root=str(self.root/'host'),stop_timeout_seconds=.03,request_timeout_seconds=.1)
        runtime=PipelineRuntime(deployment,self.supervisor,journal=journal,
                                platform=self.platform,utc_now=lambda:self.clock)
        self.addCleanup(runtime.close)
        host=HostAgent(journal,self.supervisor,runtime,deployment)
        command=dict({k:self.owner[k] for k in self.owner if k!='generation'},
                     expected_generation=None,action_id='prepare',operation='prepare')
        ready=host.handle(command)
        self.assertEqual(ready['state'],'READY')
        self.clock=datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        command.update(operation='start',action_id='start',expected_generation=ready['generation'],
                       utc_start='2026-09-12-00:00:00',duration_seconds=1.048576)
        # Frozen B3 currently rejects the MS06 time format before dispatch.
        # Exercise its state validator separately, without rewriting the UTC.
        observed=runtime.start(dict(self.owner,generation=ready['generation']),
                               '2026-09-12-00:00:00',1.048576)
        from scripts.multiserver.host_agent import PrivateRoot,instance_key
        root=PrivateRoot(deployment['root'])
        try:
            snapshot=root.read('instance-'+instance_key(command))
            snapshot.update(state='STARTING',start_id='start')
            self.assertTrue(host._accept(snapshot,observed))
            self.assertEqual(snapshot['state'],'FAILED')
        finally:root.close()

    def test_journal_unknown_action_is_resolved_only_after_actual_effect(self):
        command=dict({k:self.owner[k] for k in self.owner if k!='generation'},
                     expected_generation=self.owner['generation'],operation='prepare',action_id='real-prepare')
        self.journal.begin_action(self.owner,command)
        result=self.prepare()
        self.assertEqual(result['action_outcomes'],{'real-prepare':'APPLIED'})
        # Background scheduler did not finish or access this SQLite intent.
        self.assertEqual(self.journal.load(self.owner)['actions']['real-prepare']['outcome'],'UNKNOWN')

    def test_ring_key_families_skip_max_reader_slots(self):
        family=ring_footprint(dict(key='0x1100',blocks=2,block_bytes=64,header_bytes=4096),
                              dict(max_readers=8,header_buffers=8,exclusive_create=True))
        shm=[i['key'] for i in family if i['kind']=='shm']
        self.assertIn(0xa1100,shm)
        self.assertIn(0x111101,shm)
        self.assertNotIn(0x31100,shm)

    def test_uint32_ring_keys_keep_their_unsigned_identity(self):
        family=ring_footprint(dict(key='0xf0001100',blocks=2,block_bytes=64,header_bytes=4096),
                              dict(max_readers=8,header_buffers=8,exclusive_create=True))
        self.assertEqual(family[0]['key'],0xf0001100)

    def test_start_after_failed_prepare_has_no_new_effect(self):
        self.platform.foreign=True
        self.prepare()
        self.assertEqual(self.start()['outcome'],'REJECTED')
        self.assertEqual(self.supervisor.launches,[])

    def test_station_disappearing_early_is_not_complete_after_zero_fill(self):
        self.prepare();self.finish_results([2048,1,2048,2048])
        self.platform.results['unpack']['station_highest_ordinals']=[2047,0,2047,2047]
        self.start();self.clock=datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        time.sleep(.06)
        self.assertEqual(self.runtime.observe(self.owner)['state'],'FAILED')

    def test_partial_tail_accounts_exact_bytes_and_publications(self):
        case=RuntimeTest();case.duration='1.049088';case.setUp()
        try:
            self.assertEqual(case.prepare()['state'],'READY')
            case.finish_results([2049]*4,output_bytes=262272)
            case.platform.results['receiver']['committed_bytes']=33833088
            case.platform.results['unpack'].update(expected_groups=2049,emitted_bytes=33570816,
                                                   station_highest_ordinals=[2048]*4)
            case.platform.results['gpu'].update(input_bytes=33570816,published_blocks=65)
            case.start();case.clock=datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
            time.sleep(.06)
            result=case.runtime.observe(case.owner)
            self.assertEqual(result['state'],'COMPLETED',result)
        finally:case.doCleanups()

    def test_real_b1_b3_controller_start_preserves_original_envelope(self):
        """Controller, host and runtime preserve the canonical schedule verbatim."""
        # Real composition reproducer for I1; no timing/format rewrite in C1.
        from multiserver_controller_test import FakeHosts
        from scripts.multiserver.controller import ObservationController
        journal=HostJournal(self.root/'composed.sqlite');self.addCleanup(journal.close)
        deployment=copy.deepcopy(self.config)
        deployment.update(root=str(self.root/'host-composed'),stop_timeout_seconds=.03,request_timeout_seconds=.1)
        runtime=PipelineRuntime(deployment,self.supervisor,journal=journal,
                                platform=self.platform,utc_now=lambda:self.clock)
        self.addCleanup(runtime.close)
        host=HostAgent(journal,self.supervisor,runtime,deployment)
        fallback=FakeHosts(self.bundle)
        class Adapter:
            def send(adapter,key,command):
                return host.handle(command) if key==('node-a',0) else fallback.send(key,command)
        controller=ObservationController(self.root/'controller.sqlite',Adapter(),
            utc_now=lambda:self.clock,monotonic_ns=lambda:1000,heartbeat_timeout_ns=10000,drain_timeout_ns=20000)
        self.addCleanup(controller.close)
        controller.register_run('run',self.bundle,json.loads((self.bundle_path/'global-observation.json').read_text()))
        prepared=controller.execute(dict(schema_version=1,run_id='run',request_id='p',operation='prepare'))
        self.assertEqual(prepared['status']['phase'],'READY')
        result=controller.execute(dict(schema_version=1,run_id='run',request_id='s',operation='start'))
        self.assertEqual(result['outcome'],'APPLIED')
        self.assertEqual(result['status']['phase'],'STARTING')

    def add_peer(self):
        from multiserver_deployment_test import use_second_gpu_on_node_a
        inputs=write_example_inputs(self.root/'peer-inputs')
        deployments=json.loads(inputs[1].read_text());source=json.loads(inputs[0].read_text())
        use_second_gpu_on_node_a(deployments,source)
        deployments['feature_root']=str(self.root)
        for index,profile in enumerate(deployments['profiles'].values()):
            profile['rings']=dict(zip(('raw_key','compute_key','output_key'),
                                     ('0x1100','0x1200','0x1300') if index==0 else ('0x2100','0x2200','0x2300')))
        deployments['profiles']['example-node-b-gpu0']['cpu']['unpack']=[14,20]
        inventory_doc=json.loads(inputs[2].read_text())
        inventory_doc['hosts'][0]['cpus']['20']=dict(socket_id=1,core_id=8,numa_node=1,smt_siblings=[20])
        inputs[2].write_text(json.dumps(inventory_doc))
        inputs[0].write_text(json.dumps(source));inputs[1].write_text(json.dumps(deployments))
        path=self.root/'peer-bundle'
        bundle=build_bundle(inputs[0],inputs[1],path,inventory_path=inputs[2])
        selected=bundle['instances'][1];profile=selected['deployment']
        owner=dict(self.owner,bundle_id=bundle['bundle_id'],gpu_id=1,generation='two',
                   observation_sha256=selected['observation_sha256'])
        inventory=json.loads(inputs[2].read_text())['hosts'][0]
        resources=dict(gpu_uuid=profile['gpu_uuid'],cpus=[dict(logical_cpu=n,
            socket_id=inventory['cpus'][str(n)]['socket_id'],core_id=inventory['cpus'][str(n)]['core_id'])
            for n in sorted({c for values in profile['cpu'].values() for c in values})],
            ipc_keys=[key+0x1000 for key in self.resources['ipc_keys']],
            flow={k:profile['flow'][k] for k in ('physical_port','match')},
            run_dir=str(self.root/'runs/node-a-gpu1'))
        self.journal.claim(owner,resources)
        self.runtime.config['instances'].append(dict({k:v for k,v in owner.items() if k!='generation'},
                                                     resources=resources,bundle_path=str(path)))
        return owner,path

    def test_one_runtime_failure_leaves_healthy_same_host_gpu_running(self):
        owner,path=self.add_peer()
        self.assertEqual(self.prepare()['state'],'READY')
        peer=self.runtime.prepare(owner,str(path));self.assertEqual(peer['state'],'READY',peer)
        self.start();self.runtime.start(owner,'2026-09-12-00:00:00','1.048576')
        self.clock=datetime.datetime(2026,9,12,tzinfo=datetime.timezone.utc)
        self.assertEqual(self.runtime.observe(owner)['state'],'RUNNING')
        self.supervisor.reports[('one','gpu')].update(state='EXITED',returncode=7)
        self.assertEqual(self.runtime.observe(self.owner)['state'],'FAILED')
        self.assertEqual(self.runtime.observe(owner)['state'],'RUNNING')
        self.assertFalse(any(generation=='two' for generation,_,_ in self.supervisor.signals))

    def test_slow_failed_instance_drain_cannot_delay_peer_ingress_deadline(self):
        owner,path=self.add_peer()
        self.prepare();self.runtime.prepare(owner,str(path))
        self.start();self.runtime.start(owner,'2026-09-12-00:00:00','1.048576')
        self.clock=datetime.datetime(2026,9,12,tzinfo=datetime.timezone.utc)
        self.runtime.options['drain_timeout_seconds']=.25
        self.supervisor.blocked.add('unpack')
        self.supervisor.reports[('one','gpu')].update(state='EXITED',returncode=7)
        self.runtime.wakeup.set()
        limit=time.monotonic()+1
        while not any(g=='one' and r=='receiver' for g,r,_ in self.supervisor.signals) and time.monotonic()<limit:
            time.sleep(.002)
        self.assertTrue(any(g=='one' and r=='receiver' for g,r,_ in self.supervisor.signals))
        self.clock=datetime.datetime(2026,9,12,0,0,2,tzinfo=datetime.timezone.utc)
        limit=time.monotonic()+.1
        while not any(g=='two' and r=='receiver' for g,r,_ in self.supervisor.signals) and time.monotonic()<limit:
            time.sleep(.002)
        self.assertTrue(any(g=='two' and r=='receiver' for g,r,_ in self.supervisor.signals),
                        'healthy peer deadline waited for another instance drain')


class CompilerDeadlineTest(unittest.TestCase):
    def setUp(self):
        self.case=RuntimeTest();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.runtime.close();self.case.resolver_patch.stop()

    def run_compiler_case(self, *, unknown=False, fail=False, large_output=False,
                          launch_error=False, log_error=False):
        case=self.case
        binary=case.root/'finite-compiler'
        code='#!'+str(Path(sys.executable).resolve())+'\n'+'''import os,sys,time
from pathlib import Path
artifacts=Path(sys.argv[sys.argv.index('--output-dir')+1]);artifacts.mkdir()
(artifacts/'partial').write_text('compiler diagnostic')
print('compiler-partial-out',flush=True)
print('compiler-partial-err',file=sys.stderr,flush=True)
'''
        if large_output:code+="os.write(1,b'x'*(1024*1024+100))\n"
        code+=('sys.exit(7)\n' if fail or launch_error else 'time.sleep(4)\n')
        binary.write_text(code);binary.chmod(0o700)
        config=copy.deepcopy(case.config)
        config['runtime']['binaries']['compiler']=dict(path=str(binary),sha256=digest(binary),leaf=True)
        # macOS's developer Python takes ~1 s before executing this shebang.
        # Leave startup room while still bounding well below natural hang exit.
        config['runtime']['ready_timeout_seconds']=2
        config.update(root=str(case.root/'compiler-host'),stop_timeout_seconds=.03,request_timeout_seconds=1)
        journal=HostJournal(case.root/'compiler-journal.sqlite')
        try:
            with local_retained_supervisor(case.root/'compiler-processes') as supervisor:
                runtime=PipelineRuntime(config,supervisor,journal=journal,platform=case.platform,utc_now=lambda:case.clock)
                try:
                    host=HostAgent(journal,supervisor,runtime,config)
                    command=dict({k:case.owner[k] for k in case.owner if k!='generation'},
                                 expected_generation=None,action_id='compiler-prepare',operation='prepare')
                    started=time.monotonic()
                    with ExitStack() as stack:
                        if launch_error:
                            from scripts.multiserver.processes import ProcessStorageError
                            save=supervisor._save
                            def lose_capture(fd,value):
                                if value['state']=='RUNNING':
                                    raise ProcessStorageError('fixture lost post-spawn identity save')
                                return save(fd,value)
                            stack.enter_context(patch.object(supervisor,'_save',side_effect=lose_capture))
                        if log_error:
                            stack.enter_context(patch.object(runtime,'_compiler_output',side_effect=OSError('fixture log read failed')))
                        if unknown:
                            from scripts.multiserver.processes import ProcessIdentityError
                            stack.enter_context(patch('scripts.multiserver.processes._read_process',
                                                       side_effect=ProcessIdentityError('fixture unavailable')))
                        report=host.handle(command)
                    elapsed=time.monotonic()-started
                    self.assertLess(elapsed,4,'compiler deadline did not bound the host command')
                    self.assertEqual(report['state'],'FAILED',report)
                    uncertain=unknown or launch_error
                    self.assertEqual(report['cleanup'],'UNKNOWN' if uncertain else 'CONFIRMED',report)
                    self.assertEqual(report['outcome'],'UNKNOWN' if uncertain else 'NOT_APPLIED',report)
                    owner=dict(case.owner,generation=report['generation'])
                    record=journal.load(owner)
                    self.assertEqual(record['released'],not uncertain)
                    paths=list(Path(case.resources['run_dir']).glob('.multiserver-resolve-*/resolution.json'))
                    self.assertEqual(len(paths),1)
                    evidence=json.loads(paths[0].read_text())
                    self.assertEqual(evidence['error']['code'],'COMPILER_SUPERVISION' if launch_error else
                                     'COMPILER_EXIT' if fail else 'COMPILER_TIMEOUT')
                    if launch_error:
                        intents=list((case.root/'compiler-processes').glob('*/launch.json'))
                        self.assertEqual(len(intents),1)
                        intent=json.loads(intents[0].read_text())
                        self.assertEqual(intent['state'],'INTENT')
                        self.assertEqual(intent['owner'],owner)
                        self.assertFalse(case.platform.owned)
                        self.assertTrue(supervisor._children)
                        return evidence
                    if log_error:
                        self.assertIn('fixture log read failed',evidence['compiler_log_error'])
                        self.assertEqual(evidence['compiler_process']['state'],'EXITED')
                        self.assertEqual(evidence['error']['code'],'COMPILER_TIMEOUT')
                        return evidence
                    self.assertIn('compiler-partial-out',evidence['stdout'])
                    self.assertIn('compiler-partial-err',evidence['stderr'])
                    process=evidence['compiler_process']
                    self.assertEqual(process['identity']['owner'],owner)
                    self.assertEqual(process['identity']['role'],'compiler')
                    self.assertEqual(process['state'],'UNKNOWN' if unknown else 'EXITED')
                    self.assertEqual(case.platform.owned,[])
                    self.assertEqual(case.platform.removed,[])
                    if not unknown:
                        self.assertTrue(all(c.returncode is not None for c in supervisor._children.values()))
                    if large_output:
                        self.assertLessEqual(len(evidence['stdout'].encode()),1024*1024)
                        self.assertTrue(evidence['output_truncated']['stdout'])
                        self.assertGreater(Path(process['logs']['stdout']).stat().st_size,1024*1024)
                    query=dict(command,operation='status',action_id='after-compiler',
                               expected_generation=owner['generation'],pending_action_ids=[])
                    started=time.monotonic();status=host.handle(query)
                    self.assertLess(time.monotonic()-started,.5)
                    self.assertEqual(status['state'],'FAILED')
                    return evidence
                finally:runtime.close()
        finally:journal.close()

    def test_finite_hang_is_killed_reaped_and_releases_unallocated_claim(self):
        self.run_compiler_case()

    def test_unknown_compiler_identity_is_not_killed_or_released(self):
        self.run_compiler_case(unknown=True)

    def test_compiler_nonzero_exit_preserves_failure_and_releases_claim(self):
        self.run_compiler_case(fail=True)

    def test_timeout_output_is_bounded_and_full_owned_logs_retained(self):
        self.run_compiler_case(large_output=True)

    def test_lost_post_spawn_identity_cannot_release_no_ring_claim(self):
        self.run_compiler_case(launch_error=True)

    def test_log_error_does_not_replace_original_timeout(self):
        self.run_compiler_case(log_error=True)


class LinuxBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.platform=LinuxPlatform({})
        self.ring=dict(key='0x1100',blocks=2,block_bytes=64,header_bytes=4096)
        self.family=ring_footprint(self.ring,dict(max_readers=8,header_buffers=8,exclusive_create=True))
        self.rows={}
        for index,item in enumerate(self.family):
            row=dict(key=item['key'],uid=os.getuid(),cuid=os.getuid(),ctime=1,perms=0o600)
            if item['kind']=='shm':
                row.update(shmid=100+index,size=item['size'] or 768,cpid=42,nattch=0)
            else:row.update(semid=100+index,nsems=item['nsems'])
            self.rows[(item['kind'],item['key'])]=row
        self.report=dict(state='EXITED',returncode=0,identity=dict(pid=42))
        self.patch=patch.object(self.platform,'_objects',side_effect=lambda:copy.deepcopy(self.rows))
        self.patch.start();self.addCleanup(self.patch.stop)

    def test_creator_mismatch_rejected_despite_matching_keys(self):
        self.rows[('shm',0x1100)]['cpid']=43
        with self.assertRaisesRegex(ValueError,'creator'):
            self.platform.capture_ring(self.ring,self.report,self.family)

    def test_failed_create_cannot_claim_sem_from_precheck_emptiness(self):
        self.report.update(returncode=1)
        with self.assertRaises(ValueError):
            self.platform.capture_ring(self.ring,self.report,self.family)

    def test_object_replacement_prevents_id_cleanup(self):
        token=self.platform.capture_ring(self.ring,self.report,self.family)
        self.rows[('shm',0x1100)]['shmid']=999
        with patch.object(self.platform.libc,'shmctl') as remove:
            self.assertFalse(self.platform.remove_ring(token))
            remove.assert_not_called()


    def test_attached_memory_is_not_removed(self):
        token=self.platform.capture_ring(self.ring,self.report,self.family)
        self.rows[('shm',0x1100)]['nattch']=1
        with patch.object(self.platform.libc,'shmctl') as remove:
            self.assertFalse(self.platform.remove_ring(token))
            remove.assert_not_called()

    def test_wrong_geometry_is_rejected(self):
        self.rows[('shm',0xa1100)]['size']=1
        with self.assertRaises(ValueError):
            self.platform.capture_ring(self.ring,self.report,self.family)

    def test_sync_geometry_is_checked_too(self):
        self.rows[('shm',0x1100)]['size']=1
        with self.assertRaises(ValueError):
            self.platform.capture_ring(self.ring,self.report,self.family)

    def test_entire_footprint_checked_before_any_removal(self):
        token=self.platform.capture_ring(self.ring,self.report,self.family)
        self.rows[('shm',0x111101)]['shmid']=999
        with patch.object(self.platform.libc,'shmctl',return_value=-1) as remove:
            self.assertFalse(self.platform.remove_ring(token))
            remove.assert_not_called()


@unittest.skipUnless(COMPILER,'real compiler requires explicit --compiler; hardware is still faked')
class InstalledCompilerTest(unittest.TestCase):
    def test_real_compiler_runs_at_installed_path_before_allocation(self):
        case=RuntimeTest();case.setUp()
        try:
            case.runtime.close();case.resolver_patch.stop()
            case.config['runtime']['binaries']['compiler']=dict(path=str(COMPILER),sha256=digest(COMPILER),leaf=True)
            case.config['runtime']['ready_timeout_seconds']=5
            with local_retained_supervisor(case.root/'actual-compiler') as compiler_supervisor:
                class Supervisor(FakeSupervisor):
                    def launch(self,owner,role,argv,cwd,env):
                        return (compiler_supervisor.launch(owner,role,argv,cwd,env) if role=='compiler' else
                                super().launch(owner,role,argv,cwd,env))
                    def inspect(self,identity):
                        return (compiler_supervisor.inspect(identity) if identity['role']=='compiler' else
                                super().inspect(identity))
                    def wait(self,identity,timeout_seconds):
                        return (compiler_supervisor.wait(identity,timeout_seconds) if identity['role']=='compiler' else
                                super().wait(identity,timeout_seconds))
                    def signal(self,identity,signum):
                        return (compiler_supervisor.signal(identity,signum) if identity['role']=='compiler' else
                                super().signal(identity,signum))
                runtime=PipelineRuntime(case.config,Supervisor(),journal=case.journal,
                                        platform=case.platform,utc_now=lambda:case.clock)
                try:
                    report=runtime.prepare(case.owner,str(case.bundle_path))
                    self.assertEqual(report['state'],'READY',report)
                    directory=Path(case.resources['run_dir'])/'compiled'
                    resolution=json.loads((directory/'resolution.json').read_text())
                    self.assertEqual(resolution['argv'][2],str(case.bundle_path/'instances/node-a/gpu-0/observation.json'))
                    self.assertEqual(resolution['report']['derived']['raw_block_bytes'],528384)
                    self.assertEqual(resolution['report']['derived']['compute_block_bytes'],524288)
                    self.assertFalse(resolution['runtime_ready'])
                    self.assertEqual(resolution['compiler_process']['state'],'EXITED')
                    self.assertEqual(resolution['compiler_process']['identity']['owner'],case.owner)
                    self.assertEqual(resolution['compiler_process']['identity']['role'],'compiler')
                    self.assertEqual(len(case.platform.owned),3)
                finally:runtime.close()
        finally:case.doCleanups()


class RetainedLeafFixtureTest(unittest.TestCase):
    def test_real_b2_handles_are_retained_signalled_and_reaped(self):
        """No IPC/GPU: actual finite children, synthetic /proc only on macOS."""
        from scripts.multiserver.processes import ProcessSupervisor
        from contextlib import ExitStack
        case=RuntimeTest();case.setUp()
        supervisor=runtime=None
        try:
            case.runtime.close()
            code='#!'+str(Path(sys.executable).resolve())+'\n'+'''import json,os,signal,sys,time
from pathlib import Path
name=Path(sys.argv[0]).name
if name=='numactl': os.execv(sys.argv[3],sys.argv[3:])
if name=='dada_db': sys.exit(0)
role={'dada_dbnull':'sink'}.get(name,name)
directory=Path.cwd()
def option(key):
 return sys.argv[sys.argv.index(key)+1] if key in sys.argv else None
ready=option('--ready-file')
if ready:
 Path(ready).write_text(json.dumps(dict(schema_version=1,pid=os.getpid(),config_id='a'*64,geometry_id='b'*64,
   input_key=0x1200 if role=='gpu' else 0x1100,output_key=0x1300 if role=='gpu' else 0x1200)))
else: (directory/'sink.attached').touch()
stopped=False
def stop(signum,frame):
 global stopped
 stopped=True
 if role=='receiver': (directory/'fixture-eod').touch()
signal.signal(signal.SIGTERM,stop)
deadline=time.monotonic()+5
while not stopped and not (directory/'fixture-eod').exists() and time.monotonic()<deadline: time.sleep(.002)
path=option('--result-json')
if path: Path(path).write_text(json.dumps(dict(schema_version=1,pid=os.getpid(),config_id='a'*64,geometry_id='b'*64,ok=False)))
'''
            for name in ('numactl','dada_db','dada_dbnull','gpu','unpack','receiver'):
                path=case.root/name;path.write_text(code);path.chmod(0o700)
                case.config['runtime']['binaries'][name]=dict(path=str(path),sha256=digest(path),leaf=True)
            case.config['runtime'].update(ready_timeout_seconds=2,drain_timeout_seconds=.3,poll_interval_seconds=.005)
            class LeafPlatform(FakePlatform):
                def ready(platform,role,report,directory,ids,ring):
                    if role=='sink':return (directory/'sink.attached').exists()
                    return LinuxPlatform._record(directory/(role+'.ready'),report,ids) is not None
            supervisor=ProcessSupervisor(str(case.root/'retained-processes'))
            with ExitStack() as stack:
                if sys.platform!='linux':
                    # Mock precisely the unavailable Linux identity boundary.
                    def identity(pid,**kwargs):
                        return dict(pid=pid,start_ticks=1,boot_id='00000000-0000-4000-8000-000000000001',uid=os.getuid(),
                                    pgid=os.getpgid(pid),sid=os.getsid(pid))
                    stack.enter_context(patch('scripts.multiserver.processes._read_process',side_effect=identity))
                    stack.enter_context(patch('scripts.multiserver.processes.sys',SimpleNamespace(platform='linux')))
                runtime=PipelineRuntime(case.config,supervisor,journal=case.journal,
                                        platform=LeafPlatform(),utc_now=lambda:case.clock)
                try:
                    prepared=runtime.prepare(case.owner,str(case.bundle_path))
                    self.assertEqual(prepared['state'],'READY',prepared)
                    stopped=runtime.stop(case.owner,'graceful',.3)
                    self.assertEqual(stopped['state'],'INTERRUPTED',stopped)
                    self.assertEqual(stopped['cleanup'],'CONFIRMED',stopped)
                finally:
                    runtime.close()
                    # Exact fixture Popen objects, never PID/name/group cleanup.
                    for child in supervisor._children.values():
                        child.wait(timeout=6)
                self.assertTrue(all(c.returncode is not None for c in supervisor._children.values()))
                supervisor.close()
        finally:
            if supervisor is not None:
                for child in supervisor._children.values():
                    child.wait(timeout=6)
                supervisor.close()
            case.doCleanups()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--compiler',type=Path)
    args,remaining=parser.parse_known_args()
    if args.compiler:
        COMPILER=args.compiler.resolve(strict=True)
        InstalledCompilerTest.__unittest_skip__=False
    unittest.main(argv=[sys.argv[0]]+remaining)
