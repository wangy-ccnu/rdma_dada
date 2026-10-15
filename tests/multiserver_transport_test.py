"""Local real-child tests and opt-in HF -> qths1 JSON-only probe."""
import json
import math
import os
import pathlib
import shlex
import subprocess
import sys
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.multiserver.transport import SSHHostAdapter
except ImportError:
    SSHHostAdapter = None

PEER = ROOT / 'tests/fixtures/multiserver_transport_peer.py'
REAL_POPEN = subprocess.Popen
LIMIT = 1024 * 1024
ENDPOINT = dict(ssh_host='user@qths1', python_path='/usr/bin/python3',
                host_entrypoint="/trusted/peer's path;$(bad).py",
                known_hosts_file='/trusted/known hosts')
COMMAND = dict(server_id='node-a', gpu_id=0, operation='prepare',
               action_id='action-1', run_id='run-1', bundle_id='bundle-1',
               observation_sha256='a' * 64, expected_generation=None)


class SSHTransportTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SSHHostAdapter, 'MS-07a adapter is not implemented')
        self.children = []
        self.calls = []
        self.addCleanup(self.check_reaped)

    def check_reaped(self):
        try:
            for child in self.children:
                self.assertIsNotNone(child.poll(), 'adapter left a local child running')
                self.assertTrue(all(stream.closed for stream in
                                    (child.stdin, child.stdout, child.stderr)))
                with self.assertRaises(ChildProcessError):
                    os.waitpid(child.pid, os.WNOHANG)
        finally:
            # Even a failing regression must not leave the test peer alive.
            for child in self.children:
                if child.poll() is None:
                    child.kill()
                child.wait()
                for stream in (child.stdin, child.stdout, child.stderr):
                    stream.close()

    def launch_local_peer(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        child = REAL_POPEN([sys.executable, '-B', str(PEER)], **kwargs)
        self.children.append(child)
        return child

    def adapter(self, timeout=2):
        return SSHHostAdapter({'node-a': ENDPOINT}, ssh_path='/usr/bin/ssh',
                              timeout_seconds=timeout)

    def send(self, mode='echo', **extra):
        command = dict(COMMAND, probe_mode=mode, **extra)
        with mock.patch('scripts.multiserver.transport.subprocess.Popen',
                        side_effect=self.launch_local_peer):
            return self.adapter().send(('node-a', 0), command)

    def test_fixed_quoted_argv_and_exact_utf8_stdin(self):
        payload = dict(COMMAND, probe_mode='wire', text="雪😀; ' & $(touch x)\n\u0000")
        with mock.patch('scripts.multiserver.transport.subprocess.Popen',
                        side_effect=self.launch_local_peer):
            result = self.adapter().send(('node-a', 0), payload)
        expected = (json.dumps(payload, ensure_ascii=False, allow_nan=False,
                               sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
        self.assertEqual(bytes.fromhex(result['wire_hex']), expected)
        self.assertEqual(self.calls[0][0], [
            '/usr/bin/ssh', '-T', '-o', 'BatchMode=yes', '-o',
            'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile="/trusted/known hosts"',
            '-o', 'ConnectTimeout=2', '-o', 'ConnectionAttempts=1', '--', 'user@qths1',
            '/usr/bin/python3 -B \'/trusted/peer\'"\'"\'s path;$(bad).py\''])
        self.assertFalse(self.calls[0][1].get('shell', False))
        self.assertEqual(len(self.calls), 1)

    def test_report_roundtrip_preserves_unicode_and_metacharacters_as_data(self):
        text = '汉字😀; `cmd` $HOME $(exit 3) | & > " \\ \n'
        self.assertEqual(self.send(text=text), dict(COMMAND, probe_mode='echo', text=text))

    def test_host_deployment_path_is_trusted_quoted_and_not_request_selected(self):
        path = "/trusted/host's config;$(never).json"
        endpoint = dict(ENDPOINT, deployment_path=path)
        adapter = SSHHostAdapter({'node-a': endpoint}, ssh_path='/usr/bin/ssh', timeout_seconds=2)
        endpoint['deployment_path'] = '/changed.json'
        command = dict(COMMAND, deployment_path='/untrusted.json')
        with mock.patch('scripts.multiserver.transport.subprocess.Popen',
                        side_effect=self.launch_local_peer):
            self.assertEqual(adapter.send(('node-a', 0), command), command)
        self.assertEqual(shlex.split(self.calls[0][0][-1]),
                         ['/usr/bin/python3', '-B', ENDPOINT['host_entrypoint'],
                          '--deployment', path])

    def test_invalid_host_deployment_path_rejected_before_launch(self):
        with mock.patch('scripts.multiserver.transport.subprocess.Popen') as launch:
            for path in (None, '', 'relative.json', '/bad\x00', '/bad\n', '/bad\ud800'):
                with self.subTest(path=repr(path)), self.assertRaises(ValueError):
                    SSHHostAdapter({'node-a': dict(ENDPOINT, deployment_path=path)},
                                   ssh_path='/usr/bin/ssh', timeout_seconds=2)
            launch.assert_not_called()

    def test_invalid_identity_does_not_spawn(self):
        cases = [(('missing', 0), COMMAND), (('node-a', True), COMMAND),
                 (('node-a', -1), COMMAND), (['node-a', 0], COMMAND),
                 (('node-a', 0), dict(COMMAND, server_id='node-b')),
                 (('node-a', 0), dict(COMMAND, gpu_id=True)),
                 (('node-a', 0), dict(COMMAND, gpu_id=1))]
        with mock.patch('scripts.multiserver.transport.subprocess.Popen') as launch:
            for key, command in cases:
                with self.subTest(key=key, command=command), self.assertRaises(ValueError):
                    self.adapter().send(key, command)
            launch.assert_not_called()

    def test_invalid_configuration_does_not_spawn(self):
        cases = [('ssh_host', '-oProxyCommand=bad'), ('ssh_host', 'qths1;bad'),
                 ('ssh_host', 'qths1\n'), ('python_path', 'python3'),
                 ('host_entrypoint', '/bad\x00'), ('known_hosts_file', 'relative'),
                 ('known_hosts_file', '/bad"path'), ('known_hosts_file', '/trusted/%h'),
                 ('known_hosts_file', '/trusted/${HOME}'), ('python_path', '/bad\ud800')]
        with mock.patch('scripts.multiserver.transport.subprocess.Popen') as launch:
            for field, value in cases:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    SSHHostAdapter({'node-a': dict(ENDPOINT, **{field: value})},
                                   ssh_path='/usr/bin/ssh', timeout_seconds=2)
            for value in [0, -1, True, math.inf, math.nan, '2']:
                with self.subTest(timeout=value), self.assertRaises(ValueError):
                    self.adapter(value)
            for value in ['', 'ssh', '/bad\n']:
                with self.subTest(ssh_path=value), self.assertRaises(ValueError):
                    SSHHostAdapter({'node-a': ENDPOINT}, ssh_path=value, timeout_seconds=2)
            for endpoints in [[], {}, {'node-a': {}}, {'node-a': dict(ENDPOINT, extra='bad')}]:
                with self.subTest(endpoints=endpoints), self.assertRaises(ValueError):
                    SSHHostAdapter(endpoints, ssh_path='/usr/bin/ssh', timeout_seconds=2)
            launch.assert_not_called()

    def test_invalid_json_request_rejected_before_launch(self):
        cycle = []; cycle.append(cycle)
        values = [math.nan, math.inf, {1: 'coerced'}, '\ud800', {'\ud800': 1},
                  cycle, {1, 2}, b'bytes', ('tuple',), 'x' * LIMIT]
        with mock.patch('scripts.multiserver.transport.subprocess.Popen') as launch:
            for value in values:
                with self.subTest(kind=type(value)), self.assertRaises(ValueError):
                    self.adapter().send(('node-a', 0), dict(COMMAND, value=value))
            launch.assert_not_called()

    def test_timeout_above_supported_maximum_is_rejected_before_spawn(self):
        cases = [('above_integer_boundary', 3601),
                 ('above_float_boundary', 3600.000001),
                 ('finite_extreme', 1e308), ('large_integer', 10 ** 1000)]
        with mock.patch('scripts.multiserver.transport.subprocess.Popen') as launch:
            for label, timeout in cases:
                with self.subTest(case=label), self.assertRaises(ValueError):
                    self.adapter(timeout)
            launch.assert_not_called()

    def test_maximum_timeout_roundtrip_uses_representable_ssh_and_selector_wait(self):
        for timeout in (3600, 3600.0):
            with self.subTest(timeout=timeout), mock.patch(
                    'scripts.multiserver.transport.subprocess.Popen',
                    side_effect=self.launch_local_peer):
                self.assertEqual(self.adapter(timeout).send(('node-a', 0), COMMAND), COMMAND)
            self.assertIn('ConnectTimeout=3600', self.calls[-1][0])
        self.assertEqual(len(self.calls), 2)

    def test_strict_json_response_rejects_noise_duplicates_nonfinite_and_surrogates(self):
        cases = [b'', b'[]', b'null', b'1', b'{} {}', b'banner\n{}', b'{',
                 b'{"x":1,"x":2}', b'{"nested":{"x":1,"x":2}}',
                 b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}',
                 b'{"x":"\\ud800"}', b'{"\\udfff":1}', b'{"x":"\xff"}']
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError) as raised:
                self.send('raw', raw_hex=raw.hex())
            if raw:
                self.assertNotIn(raw.hex(), str(raised.exception))
        self.assertEqual(len(self.calls), len(cases))

    def test_output_overflow_reaps_child_without_retry(self):
        for mode in ('stdout_overflow', 'stderr_overflow'):
            started = time.monotonic()
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.send(mode)
            self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(len(self.calls), 2)

    def test_output_at_boundary_is_allowed(self):
        self.assertEqual(self.send('stderr_boundary'), {})
        # Request hex would exceed the bound, so use a local peer which writes
        # this hand-counted response, keeping the actual request small.
        with mock.patch('scripts.multiserver.transport.subprocess.Popen',
                        side_effect=lambda argv, **kw: self.launch_boundary(**kw)):
            result = self.adapter().send(('node-a', 0), COMMAND)
        self.assertEqual(len(result['x']), LIMIT - 8)

    def launch_boundary(self, **kwargs):
        child = REAL_POPEN([sys.executable, '-B', '-c',
                           'import sys; sys.stdin.buffer.read(); '
                           'sys.stdout.buffer.write(b\'{"x":"\'+b"x"*(1048576-8)+b\'"}\')'], **kwargs)
        self.children.append(child)
        return child

    def test_whole_request_timeout_reaps_real_child_without_retry(self):
        with mock.patch('scripts.multiserver.transport.subprocess.Popen',
                        side_effect=self.launch_local_peer):
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                self.adapter(0.15).send(('node-a', 0), dict(COMMAND, probe_mode='sleep'))
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(len(self.calls), 1)

    def test_nonzero_and_255_are_oserror_without_retry_or_payload_leak(self):
        for mode in ('fail', 'exit255'):
            with self.subTest(mode=mode), self.assertRaises(OSError) as raised:
                self.send(mode, secret='NEVER_LOG_THIS')
            self.assertNotIn('NEVER_LOG_THIS', str(raised.exception))
            self.assertNotIn('deterministic JSON-only', str(raised.exception))
        self.assertEqual(len(self.calls), 2)

    def test_closed_stdin_cannot_acknowledge_an_unsent_request(self):
        def early_reply(argv, **kwargs):
            child = REAL_POPEN([sys.executable, '-B', '-c',
                               'import os,time; os.close(0); '
                               'print("{}", flush=True); time.sleep(0.05)'], **kwargs)
            self.children.append(child)
            return child
        with mock.patch('scripts.multiserver.transport.subprocess.Popen', side_effect=early_reply):
            with self.assertRaises(OSError):
                self.adapter().send(('node-a', 0), dict(COMMAND, text='x' * 500000))

    def test_timeout_includes_blocked_stdin_and_wait_after_output_eof(self):
        for code in ['import time; time.sleep(30)',
                     'import os,time; os.close(1); os.close(2); time.sleep(30)']:
            def launch(argv, **kwargs):
                child = REAL_POPEN([sys.executable, '-B', '-c', code], **kwargs)
                self.children.append(child)
                return child
            with self.subTest(code=code), mock.patch(
                    'scripts.multiserver.transport.subprocess.Popen', side_effect=launch):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    self.adapter(0.15).send(('node-a', 0), dict(COMMAND, text='x' * 500000))
                self.assertLess(time.monotonic() - started, 1.5)

    def test_trusted_endpoint_is_copied_and_request_cannot_select_an_executable(self):
        endpoint = dict(ENDPOINT)
        adapter = SSHHostAdapter({'node-a': endpoint}, ssh_path='/usr/bin/ssh', timeout_seconds=2)
        endpoint['ssh_host'] = 'different'
        command = dict(COMMAND, ssh_host='untrusted', python_path='/untrusted',
                       host_entrypoint='/untrusted', known_hosts_file='/untrusted')
        with mock.patch('scripts.multiserver.transport.subprocess.Popen',
                        side_effect=self.launch_local_peer):
            self.assertEqual(adapter.send(('node-a', 0), command), command)
        self.assertEqual(self.calls[0][0][-2], 'user@qths1')
        self.assertNotIn('/untrusted', self.calls[0][0][-1])

    def test_request_limit_counts_utf8_bytes_and_accepts_exact_boundary(self):
        base = b'{"gpu_id":0,"probe_mode":"request_length","server_id":"node-a","value":""}\n'
        command = dict(server_id='node-a', gpu_id=0, probe_mode='request_length',
                       value='x' * (LIMIT - len(base)))
        with mock.patch('scripts.multiserver.transport.subprocess.Popen',
                        side_effect=self.launch_local_peer):
            self.assertEqual(self.adapter().send(('node-a', 0), command), {'received_bytes': LIMIT})
        with mock.patch('scripts.multiserver.transport.subprocess.Popen') as launch:
            for text in [command['value'] + 'x', '雪' * (LIMIT // 3)]:
                with self.subTest(length=len(text)), self.assertRaises(ValueError):
                    self.adapter().send(('node-a', 0), dict(command, value=text))
            launch.assert_not_called()

    def test_stdout_is_drained_while_request_stdin_is_blocked(self):
        def launch(argv, **kwargs):
            code = '''import sys
sys.stdout.buffer.write(b'{"x":"' + b'x' * (1048576 - 8) + b'"}')
sys.stdout.buffer.flush()
sys.stdin.buffer.read()
'''
            child = REAL_POPEN([sys.executable, '-B', '-c', code], **kwargs)
            self.children.append(child)
            return child
        with mock.patch('scripts.multiserver.transport.subprocess.Popen', side_effect=launch):
            result = self.adapter().send(('node-a', 0), dict(COMMAND, value='x' * 500000))
        self.assertEqual(len(result['x']), LIMIT - 8)

    def test_launch_failure_is_sanitized_without_retry(self):
        with mock.patch('scripts.multiserver.transport.subprocess.Popen',
                        side_effect=OSError('sensitive config path')) as launch:
            with self.assertRaises(OSError) as raised:
                self.adapter().send(('node-a', 0), COMMAND)
            self.assertNotIn('sensitive config path', str(raised.exception))
            self.assertEqual(launch.call_count, 1)


class ControllerTransportIntegrationTest(unittest.TestCase):
    """Real MS-06 controller/store + adapter + local child; no real SSH."""
    @classmethod
    def setUpClass(cls):
        import tempfile
        from multiserver_fixture import write_example_inputs
        from scripts.multiserver.bundle import build_bundle
        cls.directory = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.directory.name).resolve()
        global_path, deployments, inventory = write_example_inputs(root / 'inputs')
        cls.global_document = json.loads(global_path.read_text())
        cls.bundle = build_bundle(global_path, deployments, root / 'bundle', inventory_path=inventory)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def exercise_lost_reply(self, operation, failure):
        import tempfile
        from multiserver_controller_test import FakeClock, FakeHosts, KEYS
        from scripts.multiserver.controller import ObservationController
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / 'runs.sqlite'
            clock, hosts = FakeClock(), FakeHosts(self.bundle)
            children, failure_target = [], [None]
            adapter = SSHHostAdapter({key[0]: ENDPOINT for key in KEYS},
                                     ssh_path='/usr/bin/ssh', timeout_seconds=0.3)

            class LocalReports:
                def send(self, key, command):
                    # The stateful host double can apply an action before the
                    # real transport fails. It owns no OS resources.
                    report = hosts.send(key, command)
                    code = ('import sys; sys.stdin.buffer.read(); print(' +
                            repr(json.dumps(report)) + ')')
                    if (key, command['operation']) == failure_target[0]:
                        if failure == 'timeout':
                            code = 'import time; time.sleep(30)'
                        elif failure == 'exit255':
                            code = 'import sys; sys.exit(255)'
                        else:
                            code = 'import sys; sys.stdin.buffer.read(); print("banner")'
                    def launch(argv, **kwargs):
                        child = REAL_POPEN([sys.executable, '-B', '-c', code], **kwargs)
                        children.append(child)
                        return child
                    with mock.patch('scripts.multiserver.transport.subprocess.Popen', side_effect=launch):
                        return adapter.send(key, command)

            reports = LocalReports()
            def open_controller():
                return ObservationController(db, reports, utc_now=clock.utc_now,
                                             monotonic_ns=clock.monotonic_ns,
                                             heartbeat_timeout_ns=100, drain_timeout_ns=200)
            controller = open_controller()
            self.addCleanup(controller.close)
            controller.register_run('run-a', self.bundle, self.global_document)
            def request(op):
                return dict(schema_version=1, run_id='run-a', request_id=op + '-1', operation=op)
            if operation == 'start':
                prepared = controller.execute(request('prepare'))
                self.assertEqual(prepared['outcome'], 'APPLIED', prepared)
            failure_target[0] = (KEYS[1], operation)
            if failure == 'protocol':
                with self.assertRaises(ValueError):
                    controller.execute(request(operation))
            else:
                self.assertEqual(controller.execute(request(operation))['outcome'], 'UNKNOWN')
            dispatched = [command for key, command in hosts.calls if command['operation'] == operation]
            self.assertEqual(len(dispatched), 2)
            action_id = dispatched[-1]['action_id']
            original_action_ids = [c['action_id'] for c in dispatched]
            with _store_context(db) as store:
                action = store.load('run-a')['snapshot']['actions'][action_id]
                self.assertEqual((action['outcome'], action['dispatch']), ('UNKNOWN', 'UNKNOWN'))
            self.assertEqual(controller.status('run-a')['result'], None)
            controller.close()
            controller = open_controller()
            self.addCleanup(controller.close)
            # Continuing loss during status queries neither cleans healthy
            # processing nor turns elapsed time into completion.
            failure_target[0] = (KEYS[1], 'status')
            if failure == 'protocol':
                with self.assertRaises(ValueError):
                    controller.reconcile('run-a')
            else:
                self.assertIsNone(controller.reconcile('run-a')['result'])
            self.assertFalse(any(c['operation'] == 'stop' for _, c in hosts.calls))
            failure_target[0] = None
            controller.reconcile('run-a')
            retry = controller.execute(request(operation))
            self.assertEqual(retry['outcome'], 'APPLIED')
            self.assertEqual([c['action_id'] for _, c in hosts.calls if c['operation'] == operation],
                             original_action_ids)
            self.assertFalse(any(c['operation'] == 'stop' for _, c in hosts.calls))
            for child in children:
                self.assertIsNotNone(child.poll())
                self.assertTrue(all(s.closed for s in (child.stdin, child.stdout, child.stderr)))
                with self.assertRaises(ChildProcessError):
                    os.waitpid(child.pid, os.WNOHANG)

    def test_prepare_timeout_keeps_durable_uncertainty_and_never_replays(self):
        self.exercise_lost_reply('prepare', 'timeout')

    def test_start_timeout_keeps_durable_uncertainty_and_never_replays(self):
        self.exercise_lost_reply('start', 'timeout')

    def test_ssh_255_keeps_uncertainty_without_automatic_stop(self):
        self.exercise_lost_reply('start', 'exit255')

    def test_protocol_error_keeps_uncertainty_without_prepare_replay(self):
        self.exercise_lost_reply('prepare', 'protocol')


def _store_context(path):
    from contextlib import contextmanager
    from scripts.multiserver.store import RunStore
    @contextmanager
    def opened():
        store = RunStore(path)
        try:
            yield store
        finally:
            store.close()
    return opened()


def remote_probe(arguments):
    """Opt-in, versioned read-only peer probe; never sends lifecycle commands."""
    import argparse
    parser = argparse.ArgumentParser(description=remote_probe.__doc__)
    parser.add_argument('--remote-probe', action='store_true', required=True)
    parser.add_argument('--ssh-path', required=True)
    parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--python-path', required=True)
    parser.add_argument('--host-entrypoint', required=True)
    parser.add_argument('--known-hosts-file', required=True)
    parser.add_argument('--timeout-seconds', type=float, required=True)
    args = parser.parse_args(arguments)
    endpoint = dict(ssh_host=args.ssh_host, python_path=args.python_path,
                    host_entrypoint=args.host_entrypoint, known_hosts_file=args.known_hosts_file)
    adapter = SSHHostAdapter({'probe-only': endpoint}, ssh_path=args.ssh_path,
                             timeout_seconds=args.timeout_seconds)
    command = dict(server_id='probe-only', gpu_id=0, probe_mode='echo',
                   probe_version=1, text='雪😀; \' $(never_execute) & | `data`\n')
    if adapter.send(('probe-only', 0), command) != command:
        raise AssertionError('JSON-only probe round-trip mismatch')
    try:
        adapter.send(('probe-only', 0), dict(command, probe_mode='fail'))
    except OSError as error:
        if 'exited with code 7;' not in str(error):
            raise AssertionError('JSON-only probe did not reach deterministic failure seam') from None
    else:
        raise AssertionError('JSON-only probe failure was not reported')
    print(json.dumps(dict(probe_version=1, roundtrip='PASS', deterministic_exit7='PASS',
                          lifecycle_commands=0, observation_resources=0)))


if __name__ == '__main__':
    if '--remote-probe' in sys.argv:
        remote_probe(sys.argv[1:])
    else:
        unittest.main()
