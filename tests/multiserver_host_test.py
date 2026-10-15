"""B3 host lifecycle tests; Linux fixtures never allocate project resources."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from multiserver.host_agent import HostAgent, HostError, decode_command, PrivateRoot, instance_key
from multiserver.ownership import HostJournal, ActionConflictError


FACTS = dict(target_data_complete=False, output_published=False, eod_complete=False)
BINDING = dict(run_id='run', bundle_id='bundle', server_id='host', gpu_id=0,
               observation_sha256='a' * 64)


def command(operation='prepare', action='prepare-1', generation=None, **extra):
    value = dict(BINDING, operation=operation, action_id=action,
                 expected_generation=generation)
    if operation in ('status', 'stop'):
        value['pending_action_ids'] = []
    if operation == 'stop':
        value['mode'] = 'force'
    if operation == 'start':
        value.update(utc_start='2030-01-01-00:00:00', duration_seconds='3')
    value.update(extra)
    return value


def deployment(root):
    return dict(root=str(root), stop_timeout_seconds=1, request_timeout_seconds=3,
                runtime=dict(kind='unavailable'), instances=[dict(
                    BINDING, bundle_path=str(root / 'installed'), resources=dict(
                        gpu_uuid='GPU-fixture', cpus=[dict(logical_cpu=0, socket_id=0, core_id=0)],
                        ipc_keys=[1234], flow=dict(physical_port='fixture', match={}),
                        run_dir=str(root / 'run')))])


class Runtime:
    """Explicit injected observations; this double has no process effects."""
    def __init__(self):
        self.calls = []
        self.result = dict(sequence=1, state='READY', cleanup='PENDING',
                           evidence=FACTS.copy(), error=None, action_outcomes={})
        self.crash = False

    def prepare(self, owner, bundle_path):
        self.calls.append(('prepare', owner, bundle_path))
        if self.crash:
            raise SystemExit('simulated crash after intent')
        return dict(self.result, outcome='APPLIED')

    def start(self, owner, utc_start, duration_seconds):
        self.calls.append(('start', utc_start, duration_seconds))
        self.result.update(sequence=self.result['sequence'] + 1, state='STARTING')
        return dict(self.result, outcome='APPLIED')

    def observe(self, owner):
        self.calls.append(('observe', owner))
        return copy.deepcopy(self.result)

    def stop(self, owner, mode, timeout_seconds):
        self.calls.append(('stop', mode, timeout_seconds))
        self.result.update(sequence=self.result['sequence'] + 1, state='INTERRUPTED')
        return dict(self.result, outcome='APPLIED')


class HostTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='ms-b3-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.config = deployment(self.root)
        self.journal = HostJournal(self.root / 'journal.sqlite')
        self.addCleanup(self.journal.close)
        self.runtime = Runtime()

    def agent(self, runtime=True):
        return HostAgent(self.journal, object(), self.runtime if runtime else None, self.config)

    def test_retirement_requires_released_claim_and_no_pending_request(self):
        from multiserver_host import retirement_ready
        root = PrivateRoot(str(self.root))
        self.addCleanup(root.close)
        self.assertTrue(retirement_ready(self.config, root, self.journal))
        agent = self.agent()
        prepared = agent.handle(command())
        self.assertFalse(retirement_ready(self.config, root, self.journal))
        owner = self.runtime.calls[0][1]
        self.runtime.result.update(sequence=2, cleanup='CONFIRMED', cleanup_evidence=dict(
            owner=owner, resources=self.config['instances'][0]['resources'],
            processes_gone=True, descendants_gone=True, resources_gone=True))
        stopped = agent.handle(command('stop', 'stop', prepared['generation']))
        self.assertEqual(stopped['cleanup'], 'CONFIRMED')
        self.assertTrue(retirement_ready(self.config, root, self.journal))
        root.write('request-pending', dict(command=command()))
        self.assertFalse(retirement_ready(self.config, root, self.journal))
        root.remove('request-pending')
        root.write('instance-unrecognized', {})
        self.assertFalse(retirement_ready(self.config, root, self.journal))

    def test_preinstalled_runs_share_host_claims_until_exact_release(self):
        from multiserver.ownership import OwnershipConflictError
        self.config['instances'].append(dict(self.config['instances'][0], run_id='second-run'))
        first_runtime = MailboxFixtureRuntime(self.config)
        first_agent = HostAgent(self.journal, object(), first_runtime, self.config)
        first = first_agent.handle(command())
        second_runtime = MailboxFixtureRuntime(self.config)
        second_agent = HostAgent(self.journal, object(), second_runtime, self.config)
        second_request = command(action='second-prepare', run_id='second-run')
        with self.assertRaises(OwnershipConflictError):
            second_agent.handle(second_request)
        self.assertEqual(second_runtime.calls, [])
        first_agent.handle(command('stop', 'stop', first['generation']))
        second = second_agent.handle(second_request)
        self.assertEqual(second['state'], 'READY')
        self.assertNotEqual(second['generation'], first['generation'])

    def test_real_controller_schedule_reaches_host_and_journal_unchanged(self):
        from multiserver_fixture import write_example_inputs
        from multiserver_controller_test import FakeClock
        from scripts.multiserver.bundle import build_bundle
        from scripts.multiserver.controller import ObservationController

        global_path, profiles, inventory = write_example_inputs(self.root / 'inputs')
        document = json.loads(global_path.read_text())
        bundle = build_bundle(global_path, profiles, self.root / 'bundle', inventory_path=inventory)
        agents, runtimes, journals = {}, {}, {}
        for item in bundle['instances']:
            key = (item['server_id'], item['gpu_id'])
            host_root = self.root / item['server_id']
            host_root.mkdir(mode=0o700)
            config = deployment(host_root)
            binding = dict(run_id='scheduled-run', bundle_id=bundle['bundle_id'],
                           server_id=key[0], gpu_id=key[1],
                           observation_sha256=item['observation_sha256'])
            config['instances'][0].update(binding)
            journal = HostJournal(host_root / 'journal.sqlite')
            self.addCleanup(journal.close)
            runtime = Runtime()
            agents[key] = HostAgent(journal, object(), runtime, config)
            runtimes[key], journals[key] = runtime, journal

        class Hosts:
            def send(self, key, request):
                return agents[key].handle(request)

        clock = FakeClock()
        controller = ObservationController(self.root / 'controller.sqlite', Hosts(),
            utc_now=clock.utc_now, monotonic_ns=clock.monotonic_ns,
            heartbeat_timeout_ns=100, drain_timeout_ns=200)
        self.addCleanup(controller.close)
        controller.register_run('scheduled-run', bundle, document)
        for operation in ('prepare', 'start'):
            result = controller.execute(dict(schema_version=1, run_id='scheduled-run',
                request_id=operation, operation=operation))
            self.assertEqual(result['outcome'], 'APPLIED', result)
        for key, runtime in runtimes.items():
            self.assertEqual(runtime.calls[-1], ('start', '2026-09-12-00:00:00', '1.048576'))
            owner = runtime.calls[0][1]
            starts = [action['command'] for action in journals[key].load(owner)['actions'].values()
                      if action['command']['operation'] == 'start']
            self.assertEqual(len(starts), 1)
            self.assertEqual((starts[0]['utc_start'], starts[0]['duration_seconds']),
                             ('2026-09-12-00:00:00', '1.048576'))

    def test_duplicate_and_reopen_do_not_replay(self):
        first = self.agent().handle(command())
        self.assertEqual(first['state'], 'READY')
        self.assertTrue(first['generation'])
        self.journal.close()
        self.journal = HostJournal(self.root / 'journal.sqlite')
        self.addCleanup(self.journal.close)
        self.assertEqual(self.agent().handle(command())['outcome'], 'APPLIED')
        self.assertEqual([call[0] for call in self.runtime.calls], ['prepare'])
        with self.assertRaises((HostError, ActionConflictError)):
            self.agent().handle(command('prepare', generation=first['generation']))

    def test_original_schedule_arms_only_and_stop_suppresses_new_start(self):
        first = self.agent().handle(command())
        generation = first['generation']
        armed = self.agent().handle(command('start', 'start-1', generation))
        self.assertEqual(armed['state'], 'STARTING')
        self.assertFalse(any(armed['evidence'].values()))
        self.assertEqual(self.runtime.calls[-1], ('start', '2030-01-01-00:00:00', '3'))
        stopped = self.agent().handle(command('stop', 'stop-1', generation))
        self.assertEqual(stopped['state'], 'INTERRUPTED')
        refused = self.agent().handle(command('start', 'start-2', generation))
        self.assertEqual(refused['outcome'], 'REJECTED')
        self.assertEqual(sum(c[0] == 'start' for c in self.runtime.calls), 1)

    def test_intent_crash_reconcile_without_prepare_replay(self):
        self.runtime.crash = True
        with self.assertRaises(SystemExit):
            self.agent().handle(command())
        unknown = self.agent(False).handle(command('status', 'query', pending_action_ids=['prepare-1', 'foreign']))
        self.assertEqual(unknown['outcome'], 'UNKNOWN')
        self.assertEqual(unknown['action_outcomes'], {'prepare-1': 'UNKNOWN'})
        self.runtime.crash = False
        self.runtime.result.update(action_outcomes={'prepare-1': 'APPLIED', 'foreign': 'APPLIED'})
        report = self.agent().handle(command('status', 'query-2', pending_action_ids=['prepare-1', 'foreign']))
        self.assertEqual(report['action_outcomes'], {'prepare-1': 'APPLIED'})
        self.assertEqual(self.agent().handle(command())['outcome'], 'APPLIED')
        self.assertEqual(sum(c[0] == 'prepare' for c in self.runtime.calls), 1)

    def test_unknown_duplicate_is_not_a_runtime_call(self):
        self.runtime.crash = True
        with self.assertRaises(SystemExit):
            self.agent().handle(command())
        report = self.agent().handle(command())
        self.assertEqual(report['outcome'], 'UNKNOWN')
        self.assertEqual(len(self.runtime.calls), 1)

    def test_stale_generation_and_client_paths_refused(self):
        self.agent().handle(command())
        for value in (command('start', 's', 'stale'), command(argv=['sh']),
                      command(bundle_path='/tmp/client'), command(gpu_id=True)):
            with self.subTest(value=value), self.assertRaises(HostError):
                self.agent().handle(value)
        self.assertEqual(len(self.runtime.calls), 1)

    def test_cached_observation_does_not_increase_sequence(self):
        first = self.agent().handle(command())
        query = self.agent().handle(command('status', 'q', first['generation']))
        self.assertEqual(query['sequence'], first['sequence'])
        self.runtime.result['sequence'] += 1
        fresh = self.agent().handle(command('status', 'q2', first['generation']))
        self.assertGreater(fresh['sequence'], query['sequence'])
        self.runtime.result['sequence'] -= 1
        self.runtime.result['state'] = 'FAILED'
        stale = self.agent().handle(command('status', 'q3', first['generation']))
        self.assertEqual(stale['state'], 'READY')
        self.assertEqual(stale['sequence'], fresh['sequence'])

    def test_terminal_and_same_sequence_contradictions_fail_closed(self):
        generation = self.agent().handle(command())['generation']
        self.runtime.result['state'] = 'RUNNING'
        with self.assertRaises(HostError):
            self.agent().handle(command('status', 'q', generation))
        self.runtime.result['state'] = 'READY'
        self.agent().handle(command('stop', 'stop', generation))
        self.runtime.result.update(sequence=3, state='READY')
        with self.assertRaises(HostError):
            self.agent().handle(command('status', 'q2', generation))

    def test_cleanup_needs_exact_runtime_attestation(self):
        first = self.agent().handle(command())
        owner = dict(BINDING, generation=first['generation'])
        stopped = self.agent().handle(command('stop', 'stop', first['generation']))
        self.assertNotEqual(stopped['cleanup'], 'CONFIRMED')
        self.assertFalse(self.journal.load(owner)['released'])
        record = self.journal.load(owner)
        self.runtime.result.update(sequence=3, cleanup='CONFIRMED', cleanup_evidence=dict(
            owner=owner, resources=record['resources'], processes_gone=True,
            descendants_gone=True, resources_gone=True))
        fresh = self.agent().handle(command('status', 'q', first['generation']))
        self.assertEqual(fresh['cleanup'], 'CONFIRMED')
        self.assertTrue(self.journal.load(owner)['released'])

    def test_unavailable_runtime_does_not_fake_ready(self):
        report = self.agent(False).handle(command())
        self.assertEqual(report['outcome'], 'REJECTED')
        self.assertEqual(report['state'], 'FAILED')
        self.assertEqual(self.runtime.calls, [])

    def test_strict_json_input(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e999}', b'\xff',
                    b'{}{}', b'[]', b'{"x":"\\ud800"}', b'x' * (1024 * 1024 + 1)):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                decode_command(raw)

    def test_durable_stop_before_prepare_cancels_all_launches(self):
        root = PrivateRoot(str(self.root))
        try:
            root.write('stop-' + instance_key(BINDING), command('stop', 'early-stop'))
        finally:
            root.close()
        report = self.agent().handle(command())
        self.assertEqual(report['outcome'], 'REJECTED')
        self.assertEqual(self.runtime.calls, [])

    def test_lifecycle_cannot_regress_from_running_to_ready(self):
        generation = self.agent().handle(command())['generation']
        self.agent().handle(command('start', 'start', generation))
        self.runtime.result.update(sequence=3, state='RUNNING')
        self.agent().handle(command('status', 'q', generation))
        self.runtime.result.update(sequence=4, state='READY')
        with self.assertRaises(HostError):
            self.agent().handle(command('status', 'q2', generation))

    def test_same_sequence_cannot_invent_new_cleanup_proof(self):
        generation = self.agent().handle(command())['generation']
        self.agent().handle(command('stop', 'stop', generation))
        owner = dict(BINDING, generation=generation)
        self.runtime.result['cleanup_evidence'] = dict(owner=owner,
            resources=self.journal.load(owner)['resources'], processes_gone=True,
            descendants_gone=True, resources_gone=True)
        with self.assertRaises(HostError):
            self.agent().handle(command('status', 'q', generation))

    def test_status_observe_failure_does_not_launch_or_release(self):
        first = self.agent().handle(command())
        def failure(owner):
            raise RuntimeError('observation unavailable')
        self.runtime.observe = failure
        with self.assertRaises(RuntimeError):
            self.agent().handle(command('status', 'q', first['generation']))
        report = self.agent(False).handle(command('status', 'offline', first['generation']))
        self.assertEqual(report['sequence'], first['sequence'])
        self.assertEqual(report['cleanup'], 'PENDING')

    def test_lost_start_response_reconciles_original_intent_only(self):
        generation = self.agent().handle(command())['generation']
        real_start = self.runtime.start
        def lost(owner, utc_start, duration_seconds):
            real_start(owner, utc_start, duration_seconds)
            raise SystemExit('lost response after arming')
        self.runtime.start = lost
        start = command('start', 'start', generation)
        with self.assertRaises(SystemExit):
            self.agent().handle(start)
        self.runtime.result['action_outcomes'] = {'start': 'APPLIED'}
        report = self.agent().handle(command('status', 'q', generation, pending_action_ids=['start']))
        self.assertEqual(report['action_outcomes'], {'start': 'APPLIED'})
        self.assertEqual(report['state'], 'STARTING')
        self.assertEqual(self.agent().handle(start)['outcome'], 'APPLIED')
        self.assertEqual(sum(c[0] == 'start' for c in self.runtime.calls), 1)

    def test_claim_and_unknown_intent_exist_before_runtime_sideeffect(self):
        prepare = self.runtime.prepare
        def checked(owner, path):
            record = self.journal.load(owner)
            self.assertEqual(record['actions']['prepare-1']['outcome'], 'UNKNOWN')
            self.assertFalse(record['released'])
            return prepare(owner, path)
        self.runtime.prepare = checked
        self.assertEqual(self.agent().handle(command())['outcome'], 'APPLIED')

    def test_start_rejection_cannot_claim_arming(self):
        generation = self.agent().handle(command())['generation']
        real_start = self.runtime.start
        def rejected(owner, utc_start, duration_seconds):
            return dict(real_start(owner, utc_start, duration_seconds), outcome='REJECTED')
        self.runtime.start = rejected
        with self.assertRaises(HostError):
            self.agent().handle(command('start', 'start', generation))

    def test_failure_is_local_to_one_installed_instance(self):
        other = copy.deepcopy(self.config['instances'][0])
        other.update(gpu_id=1, observation_sha256='b' * 64)
        other['resources'].update(gpu_uuid='GPU-second', ipc_keys=[1235],
            cpus=[dict(logical_cpu=1, socket_id=0, core_id=1)],
            flow=dict(physical_port='fixture-2', match={}), run_dir=str(self.root / 'run2'))
        self.config['instances'].append(other)
        prepare = self.runtime.prepare
        def failing(owner, path):
            if owner['gpu_id'] == 0:
                raise RuntimeError('one instance failed')
            return prepare(owner, path)
        self.runtime.prepare = failing
        failed = self.agent().handle(command())
        self.assertEqual(failed['state'], 'FAILED')
        self.assertEqual(failed['outcome'], 'UNKNOWN')
        second = dict(command(action='other-prepare'), gpu_id=1, observation_sha256='b' * 64)
        healthy = self.agent().handle(second)
        self.assertEqual(healthy['state'], 'READY')
        self.assertEqual(healthy['outcome'], 'APPLIED')

    def test_corrupt_instance_binding_cannot_observe_foreign_owner(self):
        first = self.agent().handle(command())
        root = PrivateRoot(str(self.root))
        try:
            name = 'instance-' + instance_key(BINDING)
            snapshot = root.read(name)
            snapshot['owner']['run_id'] = 'foreign'
            root.write(name, snapshot)
        finally:
            root.close()
        with self.assertRaises(HostError):
            self.agent().handle(command('status', 'q', first['generation']))
        self.assertEqual(sum(c[0] == 'observe' for c in self.runtime.calls), 0)

    def test_partial_cleanup_does_not_release(self):
        generation = self.agent().handle(command())['generation']
        self.agent().handle(command('stop', 'stop', generation))
        owner = dict(BINDING, generation=generation)
        self.runtime.result.update(sequence=3, cleanup='CONFIRMED', cleanup_evidence=dict(
            owner=owner, resources=self.journal.load(owner)['resources'], processes_gone=True,
            descendants_gone=False, resources_gone=True))
        report = self.agent().handle(command('status', 'q', generation))
        self.assertEqual(report['cleanup'], 'UNKNOWN')
        self.assertFalse(self.journal.load(owner)['released'])


ENTRYPOINT = Path(__file__).resolve().parents[1] / 'scripts' / 'multiserver_host.py'


class CLITests(unittest.TestCase):
    def retire(self):
        return subprocess.run([sys.executable, '-B', str(ENTRYPOINT), '--deployment',
            str(self.config_path), '--development-fixture', '--retire'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6)

    def test_supervisor_retirement_refuses_live_claim_then_exits_after_cleanup(self):
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=3)
        try:
            first = json.loads(self.invoke(command(), fixture=True).stdout)
            refused = self.retire()
            self.assertEqual(refused.returncode, 0, refused.stderr)
            self.assertEqual(json.loads(refused.stdout)['state'], 'REFUSED')
            stopped = json.loads(self.invoke(command('stop', 'stop', first['generation']), fixture=True).stdout)
            self.assertEqual(stopped['cleanup'], 'CONFIRMED')
            retired = self.retire()
            self.assertEqual(retired.returncode, 0, retired.stderr)
            self.assertEqual(json.loads(retired.stdout), dict(state='RETIRED', cleanup='CONFIRMED'))
            marker = json.loads((self.root / 'daemon.json').read_text())
            self.assertEqual(marker['phase'], 'EXITED')
            self.assertEqual(json.loads(self.retire().stdout)['state'], 'RETIRED')
            later = self.invoke(command('status', 'after-retire', first['generation']), fixture=True)
            self.assertEqual(json.loads(later.stdout)['outcome'], 'UNKNOWN')
            self.assertEqual(json.loads((self.root / 'daemon.json').read_text())['pid'], marker['pid'])
        finally:
            time.sleep(3.1)  # Fixed fixture lifetime, including failed assertions.

    def test_production_factory_uses_fixed_pipeline_runtime_and_retained_owners(self):
        from unittest.mock import patch
        import multiserver_host
        config = deployment(Path('/trusted/host'))
        config['runtime'] = dict(kind='pipeline', binaries={}, environment={}, ipc={},
            ready_timeout_seconds=1, drain_timeout_seconds=1, poll_interval_seconds=.01)
        supervisor, journal = object(), object()
        with patch('multiserver_host.PipelineRuntime') as factory:
            result = multiserver_host.build_runtime(config, supervisor, journal=journal)
            factory.assert_called_once_with(config, supervisor, journal=journal)
            self.assertIs(result, factory.return_value)
        with self.assertRaises(HostError):
            multiserver_host.validate_runtime(dict(config, runtime={'kind': 'client.module'}), False)
        for fixture in (False, True):
            with self.assertRaises(HostError):
                multiserver_host.validate_runtime(dict(config,
                    runtime=dict(config['runtime'], fault={})), fixture)

    def test_fault_runtime_requires_both_fixed_kind_and_development_flag(self):
        from unittest.mock import patch, Mock
        import multiserver_host
        config = deployment(Path('/trusted/host'))
        config['runtime'] = dict(kind='pipeline-fixture', binaries={}, environment={}, ipc={},
            ready_timeout_seconds=1, drain_timeout_seconds=1, poll_interval_seconds=.01, fault={})
        with self.assertRaises(HostError):
            multiserver_host.build_runtime(config, object(), journal=object())
        module = Mock()
        spec = Mock()
        with patch('multiserver_host.importlib.util.spec_from_file_location', return_value=spec) as select, \
                patch('multiserver_host.importlib.util.module_from_spec', return_value=module):
            supervisor, journal = object(), object()
            result = multiserver_host.build_runtime(config, supervisor, True, journal=journal)
            self.assertEqual(select.call_args[0][1],
                str(ENTRYPOINT.parent.parent / 'tests/fixtures/multiserver_acceptance_peer.py'))
            module.FaultPipelineRuntime.assert_called_once_with(config, supervisor, journal=journal)
            self.assertIs(result, module.FaultPipelineRuntime.return_value)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='ms-b3-cli-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.config = deployment(self.root)
        self.config_path = self.root / 'deployment.json'
        self.config_lock = threading.Lock()

    def invoke(self, value, fixture=False):
        with self.config_lock:
            desired = json.dumps(self.config)
            if not self.config_path.exists() or self.config_path.read_text() != desired:
                with tempfile.NamedTemporaryFile(mode='w', dir=str(self.root), delete=False) as stream:
                    stream.write(desired)
                    temporary = stream.name
                os.replace(temporary, self.config_path)
        argv = [sys.executable, '-B', str(ENTRYPOINT), '--deployment', str(self.config_path)]
        if fixture:
            argv.append('--development-fixture')
        raw = value if type(value) is bytes else json.dumps(value).encode()
        return subprocess.run(argv, input=raw, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=6)

    def test_cli_rejects_uninstalled_runtime_before_creating_host_state(self):
        child = self.invoke(command())
        self.assertNotEqual(child.returncode, 0)
        self.assertEqual(child.stdout, b'')
        self.assertFalse((self.root / 'journal.sqlite').exists())
        self.assertFalse((self.root / 'daemon.json').exists())
        self.assertFalse(any(self.root.glob('request-*')))

    def test_cli_rejects_protocol_input_without_stdout(self):
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=0.4)
        for raw in (b'{}{}', b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'x' * (1024 * 1024 + 1)):
            with self.subTest(raw=raw[:24]):
                child = self.invoke(raw, fixture=True)
                self.assertNotEqual(child.returncode, 0)
                self.assertEqual(child.stdout, b'')

    def test_cli_waits_for_eof_and_does_not_enqueue_partial_command(self):
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=0.4)
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)
        child = subprocess.Popen([sys.executable, '-B', str(ENTRYPOINT), '--deployment', str(self.config_path), '--development-fixture'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            child.stdin.write(json.dumps(command('status', 'q')).encode())
            child.stdin.flush()
            time.sleep(0.1)
            self.assertIsNone(child.poll())
            self.assertFalse(any(self.root.glob('request-*')))
            child.stdin.close()
            child.stdin = None
            stdout, stderr = child.communicate(timeout=6)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)['outcome'], 'UNKNOWN')
            self.assertFalse((self.root / 'daemon.json').exists())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream:
                    stream.close()

    def test_status_without_supervisor_does_not_dispatch_pending_prepare(self):
        # A durable queue left before spawn is not permission for status to launch.
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=0.4)
        (self.root / 'request-abandoned').write_text(json.dumps(dict(command=command())))
        (self.root / 'request-abandoned').chmod(0o600)
        reply = self.invoke(command('status', 'q', pending_action_ids=['prepare-1']), fixture=True)
        self.assertEqual(reply.returncode, 0, reply.stderr)
        report = json.loads(reply.stdout)
        self.assertEqual(report['outcome'], 'UNKNOWN')
        self.assertEqual(report['state'], 'NEW')
        self.assertEqual(report['action_outcomes'], {})
        self.assertFalse((self.root / 'daemon.json').exists())
        self.assertFalse((self.root / 'journal.sqlite').exists())

    def test_queued_action_conflict_cannot_publish_stop(self):
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=0.4)
        original = command()
        name = 'submitted-' + hashlib.sha256(b'prepare-1').hexdigest()
        path = self.root / name
        path.write_text(json.dumps(dict(command=original)))
        path.chmod(0o600)
        reply = self.invoke(command('stop', 'prepare-1'), fixture=True)
        self.assertNotEqual(reply.returncode, 0)
        self.assertFalse(any(self.root.glob('stop-*')))
        self.assertFalse((self.root / 'daemon.json').exists())

    def test_regular_file_stdin_returns_one_json(self):
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=0.4)
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)
        path = self.root / 'stdin.txt'
        path.write_text(json.dumps(command('status', 'q')))
        with path.open('rb') as stream:
            child = subprocess.run([sys.executable, '-B', str(ENTRYPOINT), '--deployment',
                str(self.config_path), '--development-fixture'], stdin=stream, capture_output=True, timeout=6)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout)['outcome'], 'UNKNOWN')
        self.assertEqual(len(child.stdout.splitlines()), 1)
        self.assertFalse((self.root / 'daemon.json').exists())

    def test_crashed_supervisor_is_never_restarted_by_reconnect(self):
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=3)
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)
        root = PrivateRoot(str(self.root))
        token = 'test-retained-daemon'
        root.write('daemon.json', dict(token=token, phase='STARTING', pid=None, deployment=self.config))
        child = subprocess.Popen([sys.executable, '-B', str(ENTRYPOINT), '--deployment',
            str(self.config_path), '--development-fixture', '--daemon', '--launch-token', token],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            prepared = self.invoke(command(), fixture=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            first = json.loads(prepared.stdout)
            # This test retains the exact Popen child, unlike a PID-only cleanup.
            child.kill()
            child.wait(timeout=2)
            query = command('status', 'q', first['generation'], pending_action_ids=['prepare-1'])
            response = self.invoke(query, fixture=True)
            report = json.loads(response.stdout)
            self.assertEqual(report['outcome'], 'UNKNOWN')
            self.assertEqual(report['sequence'], first['sequence'])
            self.assertEqual(report['action_outcomes'], {'prepare-1': 'APPLIED'})
            later = self.invoke(command('start', 'start', first['generation']), fixture=True)
            self.assertEqual(json.loads(later.stdout)['outcome'], 'UNKNOWN')
            self.assertEqual(root.read('daemon.json')['pid'], child.pid)
            self.assertFalse(any(self.root.glob('response-*')))
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            root.close()

    def test_portable_mailbox_single_daemon_and_response_loss(self):
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=0.8)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                children = list(pool.map(lambda _: self.invoke(command(), fixture=True), range(2)))
            for child in children:
                self.assertEqual(child.returncode, 0, child.stderr)
            first, repeated = [json.loads(child.stdout) for child in children]
            self.assertEqual(first['generation'], repeated['generation'])
            self.assertEqual(first['state'], 'READY')
            # Discard a successful reply, then recover it through a new CLI.
            query = command('status', 'q', first['generation'], pending_action_ids=['prepare-1'])
            recovered = self.invoke(query, fixture=True)
            report = json.loads(recovered.stdout)
            self.assertEqual(report['action_outcomes'], {'prepare-1': 'APPLIED'})
            self.assertEqual(report['sequence'], first['sequence'])
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda value: self.invoke(value, fixture=True), (
                    query, command('stop', 'stop', first['generation']))))
            self.assertEqual(json.loads(results[1].stdout)['state'], 'INTERRUPTED')
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                marker = json.loads((self.root / 'daemon.json').read_text())
                if marker['phase'] == 'EXITED':
                    break
                time.sleep(0.02)
            else:
                self.fail('bounded memory fixture daemon did not exit')
            later = self.invoke(query, fixture=True)
            self.assertEqual(json.loads(later.stdout)['outcome'], 'UNKNOWN')
            self.assertEqual(json.loads((self.root / 'daemon.json').read_text())['pid'], marker['pid'])
        finally:
            time.sleep(1)  # Fixed deadline even on assertion failure; no child pipeline.

    def test_stop_cancels_queued_prepare_before_releasing_claim(self):
        self.config['runtime'] = dict(kind='memory-fixture', lifetime_seconds=0.5)
        root = PrivateRoot(str(self.root))
        try:
            root.write('request-original-prepare', dict(command=command()))
            stop = command('stop', 'stop', pending_action_ids=['prepare-1'])
            reply = self.invoke(stop, fixture=True)
            self.assertEqual(reply.returncode, 0, reply.stderr)
            report = json.loads(reply.stdout)
            self.assertEqual(report['action_outcomes'], {'prepare-1': 'REJECTED'})
            self.assertEqual(report['cleanup'], 'CONFIRMED')
        finally:
            root.close()
            time.sleep(1)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux /proc and B2 real child identities required')
    def test_detached_reconnect_concurrent_stop_and_expired_daemon_unknown(self):
        self.config['runtime'] = dict(kind='fixture', lifetime_seconds=1.5)
        # The fixed fixture exits itself and its retained child within 1.5 s.
        try:
            first = self.invoke(command(), fixture=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            prepared = json.loads(first.stdout)
            self.assertEqual(prepared['state'], 'READY')
            generation = prepared['generation']
            marker = json.loads((self.root / 'daemon.json').read_text())
            self.assertGreater(marker['pid'], 1)
            repeated = self.invoke(command(), fixture=True)
            self.assertEqual(json.loads(repeated.stdout)['generation'], generation)
            self.assertEqual(json.loads((self.root / 'daemon.json').read_text())['pid'], marker['pid'])
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.invoke, value, True) for value in (
                    command('status', 'q', generation), command('stop', 'stop', generation))]
                replies = [json.loads(f.result().stdout) for f in futures]
            self.assertEqual(replies[1]['state'], 'INTERRUPTED')
            self.assertEqual(replies[1]['cleanup'], 'CONFIRMED')
            refused = self.invoke(command('start', 'start', generation), fixture=True)
            self.assertNotEqual(refused.returncode, 0)  # B1 refuses new actions after release.
            self.assertEqual(refused.stdout, b'')
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if json.loads((self.root / 'daemon.json').read_text())['phase'] == 'EXITED':
                    break
                time.sleep(0.02)
            else:
                self.fail('bounded daemon failed to exit')
            later = self.invoke(command('status', 'later', generation), fixture=True)
            self.assertEqual(json.loads(later.stdout)['outcome'], 'UNKNOWN')
            self.assertEqual(json.loads((self.root / 'daemon.json').read_text())['pid'], marker['pid'])
            logs = list((self.root / 'processes').glob('*/stdout.log'))
            self.assertEqual(len(logs), 1)
            self.assertIn('fixture output', logs[0].read_text())
        finally:
            # Natural bound, exact supervisor cleanup and no project resources.
            time.sleep(2)


class MailboxFixtureRuntime(Runtime):
    """No child/resources; supplies explicit cleanup proof for mailbox tests."""
    def __init__(self, config):
        super().__init__()
        self.config = config

    def stop(self, owner, mode, timeout_seconds):
        report = super().stop(owner, mode, timeout_seconds)
        report.update(cleanup='CONFIRMED', cleanup_evidence=dict(owner=owner,
            resources=self.config['instances'][0]['resources'], processes_gone=True,
            descendants_gone=True, resources_gone=True))
        self.result.update(report)
        return report


class ProcessFixtureRuntime(Runtime):
    """Only imported by the explicit trusted --development-fixture entrypoint."""
    def __init__(self, config, supervisor):
        super().__init__()
        self.config, self.supervisor = config, supervisor
        self.identities = {}

    def prepare(self, owner, bundle_path):
        report = self.supervisor.launch(owner, 'fixed-fixture', [sys.executable, '-B',
            str(Path(__file__).resolve()), '--fixture-child',
            str(self.config['runtime']['lifetime_seconds'])], self.config['root'], {})
        self.identities[owner['generation']] = report['identity']
        if report['state'] != 'RUNNING':
            raise RuntimeError('fixture did not launch')
        return super().prepare(owner, bundle_path)

    def stop(self, owner, mode, timeout_seconds):
        import signal
        identity = self.identities.get(owner['generation'])
        if identity:
            self.supervisor.signal(identity, signal.SIGTERM)
            result = self.supervisor.wait(identity, timeout_seconds)
            if result['state'] != 'EXITED':
                raise RuntimeError('exact fixture child not reaped')
        report = super().stop(owner, mode, timeout_seconds)
        # Fixed child has no fork/exec/resource allocation code. This explicit
        # fixture proof must never be generalized to arbitrary pipeline children.
        report.update(cleanup='CONFIRMED', cleanup_evidence=dict(owner=owner,
            resources=self.config['instances'][0]['resources'], processes_gone=True,
            descendants_gone=True, resources_gone=True))
        self.result.update(report)
        return report

    def close(self):
        for identity in self.identities.values():
            self.stop(identity['owner'], 'force', 1)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--fixture-child':
        print('fixture output', flush=True)
        print('fixture diagnostic', file=sys.stderr, flush=True)
        assert sys.stdin.read() == ''
        time.sleep(min(float(sys.argv[2]), 3))
    else:
        unittest.main()
