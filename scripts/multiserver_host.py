#!/usr/bin/env python3
"""One strict JSON command on stdin -> one MS-06 report on stdout.

Operator usage: python3 -B scripts/multiserver_host.py --deployment /absolute/host.json
SSH endpoints supply this trusted deployment argument; no system installation is assumed.
Deployment must be a private regular file owned by the effective deployment user;
root is a canonical, private directory. Never obtain deployment paths, runtime
selection or argv from the incoming command. The production runtime mapping is fixed.

The daemon owns the B1 connection and B2 Popen handles. Durable mailbox requests
survive CLI exit; a permanent launch marker plus two flock locks prevent restart
or duplicate supervisor launches. A dead daemon is UNKNOWN and requires operator
resolution; there is no adoption, automatic replay, PID/PGID kill or HA policy.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import selectors
import stat
import subprocess
import sys
import time
import uuid

from multiserver.host_agent import (HostAgent, HostError, PrivateRoot, decode_command,
                                    instance_key, validate_command, validate_deployment, validate_snapshot)
from multiserver.ownership import HostJournal, OwnerNotFoundError
from multiserver.processes import ProcessSupervisor, _private
from multiserver.pipeline_runtime import PipelineRuntime
from multiserver.transport import _encode, _LIMIT


def read_stdin(timeout_seconds=5):
    if stat.S_ISREG(os.fstat(sys.stdin.fileno()).st_mode):
        return decode_command(sys.stdin.buffer.read(_LIMIT + 1))
    deadline = time.monotonic() + timeout_seconds
    result = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin.fileno(), selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise HostError('stdin EOF deadline exceeded')
            part = os.read(sys.stdin.fileno(), min(65536, _LIMIT + 1 - len(result)))
            if not part:
                return decode_command(bytes(result))
            result.extend(part)
            if len(result) > _LIMIT:
                raise HostError('stdin exceeds host limit')


def load_deployment(path):
    if not os.path.isabs(path):
        raise HostError('deployment path must be absolute')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        _private(stream.fileno())
        return validate_deployment(decode_command(stream.read(_LIMIT + 1)))


def validate_runtime(config, development_fixture):
    runtime = config['runtime']
    pipeline_fields = {'kind', 'binaries', 'environment', 'ipc', 'ready_timeout_seconds',
                       'drain_timeout_seconds', 'poll_interval_seconds'}
    if runtime.get('kind') == 'pipeline' and set(runtime) == pipeline_fields:
        return  # PipelineRuntime validates the trusted binary/IPC contract before effects.
    if (development_fixture and runtime.get('kind') == 'pipeline-fixture'
            and set(runtime) == pipeline_fields | {'fault'}):
        return  # Fixed test implementation validates the owner-bound injection.
    if development_fixture and set(runtime) == {'kind', 'lifetime_seconds'} and runtime['kind'] in ('fixture', 'memory-fixture'):
        if type(runtime['lifetime_seconds']) in (int, float) and 0.2 <= runtime['lifetime_seconds'] <= 3:
            return
    raise HostError('production runtime is not installed or fixture configuration invalid')


def build_runtime(config, supervisor, development_fixture=False, *, journal=None):
    """Fixed runtime mapping from operator-owned deployment, never command JSON.

    Runtime receives this supervisor once and retains it across all requests.
    The same journal correlates UNKNOWN
    action IDs with owner-bound runtime evidence; runtime methods remain the
    four specified owner-based methods. Journal intent alone is not effect proof.
    No dynamic client-selected imports, shell commands or executable maps.
    The fixed test module is reachable only through BOTH operator configuration
    and the explicit development entrypoint flag; it is not production readiness.
    """
    validate_runtime(config, development_fixture)
    runtime = config['runtime']
    if runtime['kind'] == 'pipeline':
        return PipelineRuntime(config, supervisor, journal=journal)
    if development_fixture and runtime['kind'] == 'pipeline-fixture':
        path = Path(__file__).resolve().parents[1] / 'tests/fixtures/multiserver_acceptance_peer.py'
        spec = importlib.util.spec_from_file_location('_fixed_acceptance_fixture', str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.FaultPipelineRuntime(config, supervisor, journal=journal)
    if development_fixture:
        path = Path(__file__).resolve().parents[1] / 'tests' / 'multiserver_host_test.py'
        spec = importlib.util.spec_from_file_location('_fixed_host_fixture', str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return (module.ProcessFixtureRuntime(config, supervisor)
                if runtime['kind'] == 'fixture' else module.MailboxFixtureRuntime(config))
    raise HostError('production runtime is not installed')


def offline_report(config, command, root):
    """Read stored facts without creating a journal, intent or observation."""
    snapshot = root.read('instance-' + instance_key(command))
    validate_snapshot(snapshot, command)
    generation = command['expected_generation']
    if generation is not None and (snapshot is None or generation != snapshot['owner']['generation']):
        raise HostError('stale generation')
    record = None
    journal_path = Path(config['root']) / 'journal.sqlite'
    if snapshot is not None and journal_path.exists():
        journal = HostJournal(journal_path)
        try:
            try:
                record = journal.load(snapshot['owner'])
            except OwnerNotFoundError:
                pass
        finally:
            journal.close()
    outcome = 'UNKNOWN'
    if command['operation'] != 'status' and record:
        action = record['actions'].get(command['action_id'])
        if action:
            if action['command'] != command:
                raise HostError('action content conflict')
            outcome = action['outcome']
    return HostAgent(None, None, None, config)._report(command, snapshot, record, outcome)


def daemon_available(root):
    marker = root.read('daemon.json')
    if marker is None:
        return False
    try:
        with root.lock('daemon.lock', blocking=False):
            return marker['phase'] == 'STARTING'
    except BlockingIOError:
        return True


def retirement_ready(config, root, journal):
    """Read-only gate; a claimed or ambiguous owner cannot be retired."""
    expected = {'instance-' + instance_key(i): i for i in config['instances']}
    for name in root.names():
        if name.startswith('request-') or (name.startswith('instance-') and name not in expected):
            return False
    for name, binding in expected.items():
        snapshot = root.read(name)
        validate_snapshot(snapshot, binding)
        if snapshot is None:
            continue  # This pre-installed run never acquired an owner.
        if snapshot['state'] not in ('COMPLETED', 'FAILED', 'INTERRUPTED') or snapshot['cleanup'] != 'CONFIRMED':
            return False
        try:
            record = journal.load(snapshot['owner'])
        except OwnerNotFoundError:
            return False
        if not record['released'] or any(a['outcome'] == 'UNKNOWN' for a in record['actions'].values()):
            return False
    return True


def retire(config):
    """Operator-only request: no signalling, launch, reconfiguration or replay."""
    root = PrivateRoot(config['root'])
    try:
        with root.lock('agent.lock'):
            marker = root.read('daemon.json')
            if marker is None or marker['deployment'] != config:
                raise HostError('no matching supervisor')
            if marker['phase'] == 'EXITED' and not daemon_available(root):
                return root.read('retirement-result.json') or dict(state='UNKNOWN', cleanup='UNKNOWN')
            if not daemon_available(root):
                return dict(state='UNKNOWN', cleanup='UNKNOWN')
            if root.read('retirement-intent.json') is None:
                if root.read('retirement-result.json') is not None:
                    root.remove('retirement-result.json')
                root.write('retirement-intent.json', dict(token=marker['token']))
        deadline = time.monotonic() + config['request_timeout_seconds']
        while time.monotonic() < deadline:
            result = root.read('retirement-result.json')
            if result is not None and (result['state'] != 'RETIRED' or not daemon_available(root)):
                return result
            time.sleep(0.01)
        return dict(state='UNKNOWN', cleanup='UNKNOWN')
    finally:
        root.close()


def ensure_daemon(config, root, deployment_path, development_fixture):
    with root.lock('launch.lock'):
        prior = root.read('daemon.json')
        if prior is not None:
            if prior['deployment'] != config:
                raise HostError('deployment changed since supervisor launch')
            return  # Ever launched: never infer permission to restart from absence.
        token = uuid.uuid4().hex
        root.write('daemon.json', dict(token=token, phase='STARTING', pid=None, deployment=config))
        fd = root._open('daemon.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            argv = [sys.executable, '-B', str(Path(__file__).resolve()), '--deployment', deployment_path,
                    '--daemon', '--launch-token', token]
            if development_fixture:
                argv.append('--development-fixture')
            # Same detached argv/session/file-redirection method as the observed
            # qths1 probe. The CLI lifetime never owns the pipeline's lifetime.
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                                     cwd=config['root'], env={}, close_fds=True,
                                     start_new_session=True, shell=False)
            # Poll once to reap an immediate failure; no termination on CLI exit.
            child.poll()
        except OSError:
            root.write('daemon.json', dict(token=token, phase='UNKNOWN', pid=None, deployment=config))
            raise HostError('supervisor launch uncertain') from None
        finally:
            os.close(fd)


def exchange(config, command, deployment_path, development_fixture):
    root = PrivateRoot(config['root'])
    try:
        key = instance_key(command)
        with root.lock('agent.lock'):
            # Validate generation/content before publishing an irreversible stop
            # latch, so a stale or conflicting request cannot cancel this owner.
            offline_report(config, command, root)
            marker = root.read('daemon.json')
            if marker is not None and marker['deployment'] != config:
                raise HostError('deployment changed since supervisor launch')
            if root.read('retirement-intent.json') is not None:
                if command['operation'] == 'status':
                    return offline_report(config, command, root)
                raise HostError('supervisor retirement pending; no new effects')
            if command['operation'] == 'status' and not daemon_available(root):
                return offline_report(config, command, root)
            if command['operation'] != 'status':
                reservation = 'submitted-' + hashlib.sha256(command['action_id'].encode('utf-8')).hexdigest()
                prior = root.read(reservation)
                if prior is not None and prior['command'] != command:
                    raise HostError('queued action content conflict')
                if prior is None:
                    root.write(reservation, dict(command=command))
            if command['operation'] == 'stop':
                root.write('stop-' + key, command)
            request_id = uuid.uuid4().hex
            root.write('request-' + request_id, dict(command=command))
        if command['operation'] != 'status':
            ensure_daemon(config, root, deployment_path, development_fixture)
        deadline = time.monotonic() + config['request_timeout_seconds']
        while time.monotonic() < deadline:
            response = root.read('response-' + request_id)
            if response is not None:
                root.remove('response-' + request_id)
                if set(response) != {'report'}:
                    raise HostError('host command refused; inspect durable state')
                return response['report']
            if not daemon_available(root):
                break
            time.sleep(0.01)
        with root.lock('agent.lock'):
            return offline_report(config, command, root)
    finally:
        root.close()


def serve(config, token, development_fixture):
    root = PrivateRoot(config['root'])
    journal = supervisor = runtime = None
    retiring = False
    try:
        with root.lock('daemon.lock', blocking=False):
            marker = root.read('daemon.json')
            if marker is None or marker['phase'] != 'STARTING' or marker['token'] != token or marker['deployment'] != config:
                raise HostError('supervisor already launched or deployment changed')
            marker.update(phase='RUNNING', pid=os.getpid())
            root.write('daemon.json', marker)
            journal = HostJournal(Path(config['root']) / 'journal.sqlite')
            supervisor = ProcessSupervisor(str(Path(config['root']) / 'processes'))
            runtime = build_runtime(config, supervisor, development_fixture, journal=journal)
            agent = HostAgent(journal, supervisor, runtime, config)
            deadline = (time.monotonic() + config['runtime']['lifetime_seconds']
                        if development_fixture and config['runtime']['kind'] in ('fixture', 'memory-fixture') else None)
            try:
                while deadline is None or time.monotonic() < deadline:
                    requests = []
                    for name in root.names():
                        if name.startswith('request-') and root.read('response-' + name[8:]) is None:
                            request = root.read(name)
                            queued = request['command']
                            cancelled_launch = (queued['operation'] in ('prepare', 'start') and
                                root.read('stop-' + instance_key(queued)) is not None)
                            # The durable stop latch already forbids effects.
                            # Record known queued launch rejections BEFORE stop
                            # can release this owner, preserving authoritative
                            # pending outcomes through B1's released-history rule.
                            priority = 0 if cancelled_launch else (1 if queued['operation'] == 'stop' else 2)
                            requests.append((priority, name, request))
                    for _, name, request in sorted(requests):
                        try:
                            result = dict(report=agent.handle(request['command']))
                        except Exception:
                            result = dict(error='command_failed')
                        root.write('response-' + name[8:], result)
                        root.remove(name)
                    with root.lock('agent.lock'):
                        intent = root.read('retirement-intent.json')
                        if intent is not None:
                            if intent != dict(token=token):
                                raise HostError('retirement binding mismatch')
                            if retirement_ready(config, root, journal):
                                retiring = True
                                break
                            root.write('retirement-result.json', dict(state='REFUSED', cleanup='UNKNOWN'))
                            root.remove('retirement-intent.json')
                    time.sleep(0.01)
            finally:
                # The production runtime controls its own operation lifetime.
                # Only the bounded trusted fixture has a natural teardown hook.
                if (development_fixture or retiring) and runtime is not None and hasattr(runtime, 'close'):
                    runtime.close()
                if supervisor is not None:
                    supervisor.close()
                if journal is not None:
                    journal.close()
                marker['phase'] = 'EXITED'
                root.write('daemon.json', marker)
                if retiring:
                    root.write('retirement-result.json', dict(state='RETIRED', cleanup='CONFIRMED'))
    finally:
        root.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', required=True)
    parser.add_argument('--development-fixture', action='store_true')
    parser.add_argument('--retire', action='store_true', help='exit only after all owned resources have confirmed cleanup')
    parser.add_argument('--daemon', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--launch-token', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        config = load_deployment(args.deployment)
        validate_runtime(config, args.development_fixture)
        if args.retire:
            if args.daemon or args.launch_token:
                raise HostError('retire cannot launch a daemon')
            sys.stdout.buffer.write(_encode(retire(config)))
            sys.stdout.buffer.flush()
            return 0
        if args.daemon:
            serve(config, args.launch_token, args.development_fixture)
            return 0
        command, _ = validate_command(read_stdin(), config)
        report = exchange(config, command, args.deployment, args.development_fixture)
        sys.stdout.buffer.write(_encode(report))
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        print('host command failed; outcome may be unknown', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
