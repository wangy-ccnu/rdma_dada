"""Host lifecycle policy and private durable mailbox (Python 3.8, POSIX).

Deployment is operator-owned JSON: root, stop_timeout_seconds,
request_timeout_seconds, runtime, instances. Each instance has the five MS-06
binding fields plus bundle_path and B1 resources. Paths are host-selected.

Runtime methods return state/cleanup/evidence/error and a positive sequence
monotone within an owner generation. Equal sequence MUST mean identical observed
facts. Effect methods additionally return outcome. Optional action_outcomes
resolve only recorded UNKNOWN actions; optional cleanup_evidence is the exact
B1 attestation. These are trusted internal evidence, never client fields.
The runtime receives the same retained supervisor passed to HostAgent via its
factory; it owns binary-specific readiness, process handles and cleanup proof.

One daemon thread owns HostJournal/ProcessSupervisor. Independent CLI processes
use the durable mailbox, never share SQLite connections or spawn workers.
An offline reader uses runtime=None and cannot refresh observations. A separate
ever-launched marker deliberately prevents automatic daemon crash recovery.
"""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import os
import stat
import uuid

from .ownership import (JournalValidationError, CleanupIncompleteError, OwnerNotFoundError,
                        _command, _owner, _resources)
from .processes import _directory, _private, ProcessStorageError
from .transport import _decode, _encode, _LIMIT


BINDING = {'run_id', 'bundle_id', 'server_id', 'gpu_id', 'observation_sha256'}
FACTS = {'target_data_complete', 'output_published', 'eod_complete'}
TERMINAL = {'COMPLETED', 'INTERRUPTED', 'FAILED'}
STATES = {'NEW', 'PREPARING', 'READY', 'STARTING', 'RUNNING', 'DRAINING'} | TERMINAL
OUTCOMES = {'UNKNOWN', 'APPLIED', 'REJECTED', 'NOT_APPLIED'}
OBSERVATION = {'state', 'cleanup', 'evidence', 'error'}
TRANSITIONS = {
    'NEW': STATES,
    'PREPARING': {'PREPARING', 'READY', 'DRAINING'} | TERMINAL,
    'READY': {'READY', 'STARTING', 'DRAINING'} | TERMINAL,
    'STARTING': {'STARTING', 'RUNNING', 'DRAINING'} | TERMINAL,
    'RUNNING': {'RUNNING', 'DRAINING'} | TERMINAL,
    'DRAINING': {'DRAINING'} | TERMINAL,
}


class HostError(ValueError):
    """Invalid command, deployment or contradictory trusted runtime evidence."""


def decode_command(raw):
    if type(raw) is not bytes or not raw or len(raw) > _LIMIT:
        raise HostError('host input must be a bounded JSON object')
    return _decode(raw)


def _binding(value):
    return {name: value[name] for name in BINDING}


def instance_key(value):
    return hashlib.sha256(bytes(_encode(_binding(value)))).hexdigest()


class PrivateRoot:
    """All mutable IO stays relative to a pinned private directory descriptor."""
    def __init__(self, path):
        self.fd = _directory(path, create=True)
        try:
            _private(self.fd, True)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def _open(self, name, flags, mode=0o600):
        if '/' in name or name in ('', '.', '..'):
            raise HostError('invalid private storage name')
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     mode, dir_fd=self.fd)
        try:
            _private(fd)
        except ProcessStorageError:
            info = os.fstat(fd)
            # Atomic replacement may unlink the old, already-open read inode.
            # Reading that private immutable inode is safe; never allow links
            # or this exception for writable descriptors/locks.
            if (flags & os.O_ACCMODE != os.O_RDONLY or info.st_nlink != 0 or
                    not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
                os.close(fd)
                raise
        except BaseException:
            os.close(fd)
            raise
        return fd

    @contextmanager
    def lock(self, name, blocking=True):
        try:
            fd = self._open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            fd = self._open(name, os.O_RDWR)
        try:
            os.fsync(self.fd)
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield
        finally:
            os.close(fd)

    def read(self, name):
        try:
            fd = self._open(name, os.O_RDONLY)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            return decode_command(stream.read(_LIMIT + 1))

    def write(self, name, value):
        raw = _encode(value)
        temporary = 'tmp-' + uuid.uuid4().hex
        fd = self._open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def names(self):
        return os.listdir(self.fd)

    def remove(self, name):
        os.unlink(name, dir_fd=self.fd)
        os.fsync(self.fd)


def validate_snapshot(snapshot, command):
    if snapshot is None:
        return
    try:
        owner = _owner(snapshot['owner'])
        if _binding(owner) != _binding(command):
            raise HostError('stored instance binding mismatch')
        if (type(snapshot['sequence']) is not int or snapshot['sequence'] <= 0 or
                snapshot['state'] not in STATES or type(snapshot['stop']) is not bool):
            raise HostError('invalid stored host snapshot')
    except (KeyError, TypeError, JournalValidationError) as error:
        raise HostError('invalid stored host snapshot') from error


def validate_deployment(value):
    value = decode_command(bytes(_encode(value)))
    if set(value) != {'root', 'stop_timeout_seconds', 'request_timeout_seconds', 'runtime', 'instances'}:
        raise HostError('invalid deployment fields')
    if type(value['root']) is not str or not os.path.isabs(value['root']):
        raise HostError('deployment root must be absolute')
    for name in ('stop_timeout_seconds', 'request_timeout_seconds'):
        if type(value[name]) not in (int, float) or not 0 < value[name] <= 60:
            raise HostError('deployment timeouts must be in (0, 60]')
    if type(value['runtime']) is not dict or type(value['instances']) is not list or not value['instances']:
        raise HostError('invalid runtime/instances')
    keys = set()
    for instance in value['instances']:
        if type(instance) is not dict or set(instance) != BINDING | {'bundle_path', 'resources'}:
            raise HostError('invalid installed instance')
        _owner(dict(_binding(instance), generation='deployment-validation'))
        instance['resources'] = _resources(instance['resources'])
        path = instance['bundle_path']
        if type(path) is not str or not os.path.isabs(path) or '\x00' in path:
            raise HostError('installed bundle path must be absolute')
        key = instance_key(instance)
        if key in keys:
            raise HostError('duplicate installed binding')
        keys.add(key)
    return value


def validate_command(command, deployment):
    try:
        command = decode_command(bytes(_encode(command)))
        operation = command.get('operation')
        if operation not in ('prepare', 'start', 'status', 'stop'):
            raise HostError('unknown host operation')
        owner = dict(_binding(command), generation=command.get('expected_generation') or 'validation')
        _owner(owner)
        if operation == 'status':
            if set(command) != BINDING | {'operation', 'action_id', 'expected_generation', 'pending_action_ids'}:
                raise HostError('invalid status fields')
            surrogate = dict(command, operation='stop', mode='graceful')
            _command(owner, surrogate)
        else:
            _command(owner, command)
        generation = command['expected_generation']
        if generation is not None and (type(generation) is not str or not generation or '\x00' in generation):
            raise HostError('invalid expected generation')
        for instance in deployment['instances']:
            if _binding(instance) == _binding(command):
                return command, instance
        raise HostError('command is not an installed binding')
    except (KeyError, TypeError, JournalValidationError, ValueError) as error:
        if isinstance(error, HostError):
            raise
        raise HostError('invalid host command') from error


class HostAgent:
    def __init__(self, journal, supervisor, runtime, deployment):
        self.journal = journal
        self.supervisor = supervisor
        self.runtime = runtime
        self.deployment = validate_deployment(deployment)

    def handle(self, command):
        command, instance = validate_command(command, self.deployment)
        root = PrivateRoot(self.deployment['root'])
        try:
            with root.lock('agent.lock'):
                return self._handle(root, command, instance)
        finally:
            root.close()

    @staticmethod
    def _initial(command):
        return dict(owner=dict(_binding(command), generation=uuid.uuid4().hex),
                    state='NEW', cleanup='PENDING', evidence=dict.fromkeys(FACTS, False),
                    error=None, sequence=1, runtime_sequence=0, runtime_observation=None,
                    stop=False, prepare_id=None, start_id=None)

    def _report(self, command, snapshot, record, outcome):
        return dict(_binding(command), action_id=command['action_id'], outcome=outcome,
                    generation=snapshot['owner']['generation'] if snapshot else None,
                    sequence=snapshot['sequence'] if snapshot else 1,
                    state=snapshot['state'] if snapshot else 'NEW',
                    cleanup=snapshot['cleanup'] if snapshot else 'CONFIRMED',
                    evidence=copy.deepcopy(snapshot['evidence'] if snapshot else dict.fromkeys(FACTS, False)),
                    error=snapshot['error'] if snapshot else None,
                    action_outcomes={action_id: record['actions'][action_id]['outcome']
                        for action_id in command.get('pending_action_ids', [])
                        if record and action_id in record['actions']})

    def _accept(self, snapshot, observed):
        decode_command(bytes(_encode(observed)))
        required = OBSERVATION | {'sequence'}
        if not required <= set(observed) or set(observed) - required - {'outcome', 'action_outcomes', 'cleanup_evidence'}:
            raise HostError('invalid runtime observation fields')
        sequence = observed['sequence']
        if type(sequence) is not int or sequence <= 0:
            raise HostError('invalid runtime sequence')
        facts = {name: observed[name] for name in OBSERVATION}
        identity_facts = dict(facts)
        if 'cleanup_evidence' in observed:
            identity_facts['cleanup_evidence'] = observed['cleanup_evidence']
        if observed['state'] not in STATES or observed['cleanup'] not in ('PENDING', 'UNKNOWN', 'CONFIRMED'):
            raise HostError('invalid runtime state/cleanup')
        if type(observed['evidence']) is not dict or set(observed['evidence']) != FACTS or any(
                type(flag) is not bool for flag in observed['evidence'].values()):
            raise HostError('invalid completion evidence')
        if observed['error'] is not None and (type(observed['error']) is not str or not observed['error']):
            raise HostError('invalid runtime error')
        outcomes = observed.get('action_outcomes', {})
        if type(outcomes) is not dict or any(v not in OUTCOMES for v in outcomes.values()):
            raise HostError('invalid runtime action outcomes')
        if sequence < snapshot['runtime_sequence']:
            return False
        if sequence == snapshot['runtime_sequence']:
            if identity_facts != snapshot['runtime_observation']:
                raise HostError('contradictory same-sequence observation')
            return True  # Envelopes may resolve intents, but never renew freshness.
        state = observed['state']
        if snapshot['state'] in TERMINAL and state != snapshot['state']:
            raise HostError('terminal lifecycle regression')
        if snapshot['state'] not in TERMINAL and state not in TRANSITIONS[snapshot['state']]:
            raise HostError('lifecycle regression')
        if state in ('STARTING', 'RUNNING', 'COMPLETED') and snapshot['start_id'] is None:
            raise HostError('runtime advanced without authorized start')
        if snapshot['stop'] and state in ('PREPARING', 'READY', 'STARTING', 'RUNNING'):
            raise HostError('runtime ignored durable stop')
        if state == 'COMPLETED' and not all(observed['evidence'].values()):
            raise HostError('completion lacks data/output/EOD evidence')
        snapshot.update(facts, runtime_sequence=sequence,
                        runtime_observation=copy.deepcopy(identity_facts), sequence=snapshot['sequence'] + 1)
        # A runtime string alone is never release evidence.
        if snapshot['cleanup'] == 'CONFIRMED':
            snapshot['cleanup'] = 'UNKNOWN'
        return True

    def _handle(self, root, command, instance):
        key = instance_key(command)
        name = 'instance-' + key
        snapshot = root.read(name)
        validate_snapshot(snapshot, command)
        operation = command['operation']
        expected = command['expected_generation']
        if expected is not None and (snapshot is None or expected != snapshot['owner']['generation']):
            raise HostError('stale or unknown generation')
        if snapshot is None and operation == 'status':
            return self._report(command, None, None, 'APPLIED' if self.runtime else 'UNKNOWN')
        if snapshot is None:
            snapshot = self._initial(command)
            root.write(name, snapshot)  # Persist generation before any allocation/intent.
        owner = snapshot['owner']
        # Idempotent declarations only, never a runtime allocation.
        try:
            record = self.journal.load(owner)
        except OwnerNotFoundError:
            if operation == 'status':
                return self._report(command, snapshot, None, 'UNKNOWN')
            record = self.journal.claim(owner, instance['resources'])
        if record['resources'] != instance['resources']:
            raise HostError('deployment changed recorded resources')
        if operation == 'status':
            if self.runtime is None:
                return self._report(command, snapshot, record, 'UNKNOWN')
            observed = self.runtime.observe(owner)
            fresh = self._accept(snapshot, observed)
            if fresh:
                record = self._resolve(command, snapshot, record, observed)
                self._release(snapshot, record, observed)
            root.write(name, snapshot)
            return self._report(command, snapshot, self.journal.load(owner), 'APPLIED')

        begun = self.journal.begin_action(owner, command)
        if not begun['created']:
            return self._report(command, snapshot, record, begun['action']['outcome'])
        if operation == 'stop':
            snapshot['stop'] = True
        stop_latched = root.read('stop-' + key) is not None
        rejected = (self.runtime is None or
                    (operation == 'prepare' and (snapshot['prepare_id'] is not None or snapshot['stop'] or stop_latched)) or
                    (operation == 'start' and (snapshot['state'] != 'READY' or snapshot['start_id'] is not None
                     or snapshot['stop'] or stop_latched)))
        if operation in ('prepare', 'start') and not rejected:
            snapshot[operation + '_id'] = command['action_id']
            snapshot.update(state='PREPARING' if operation == 'prepare' else 'STARTING',
                            sequence=snapshot['sequence'] + 1)
        if self.runtime is None and snapshot['state'] == 'NEW':
            snapshot.update(state='FAILED', error='runtime_unavailable', sequence=snapshot['sequence'] + 1)
        root.write(name, snapshot)
        observed = None
        outcome = 'REJECTED' if rejected else 'UNKNOWN'
        if not rejected:
            try:
                if operation == 'prepare':
                    observed = self.runtime.prepare(owner, instance['bundle_path'])
                elif operation == 'start':
                    observed = self.runtime.start(owner, command['utc_start'], command['duration_seconds'])
                else:
                    observed = self.runtime.stop(owner, command['mode'], self.deployment['stop_timeout_seconds'])
                if observed.get('outcome') not in OUTCOMES:
                    raise HostError('effect missing authoritative outcome')
                if not self._accept(snapshot, observed):
                    raise HostError('effect returned an older observation')
                outcome = observed['outcome']
                if operation == 'start' and outcome in ('REJECTED', 'NOT_APPLIED') and snapshot['state'] in (
                        'STARTING', 'RUNNING', 'COMPLETED'):
                    raise HostError('start outcome contradicts active observation')
            except HostError:
                raise  # Durable UNKNOWN remains; caller must query, not replay.
            except Exception:
                snapshot.update(state=snapshot['state'] if snapshot['state'] in TERMINAL else 'FAILED',
                                cleanup='UNKNOWN', error='runtime_failure', sequence=snapshot['sequence'] + 1)
        record = self.journal.load(owner)
        report = self._report(command, snapshot, record, outcome)
        self.journal.finish_action(owner, command['action_id'], report)
        if observed is not None:
            record = self._resolve(command, snapshot, self.journal.load(owner), observed)
            self._release(snapshot, record, observed)
        root.write(name, snapshot)
        return self._report(command, snapshot, self.journal.load(owner), outcome)

    def _resolve(self, command, snapshot, record, observed):
        resolutions = observed.get('action_outcomes', {})
        for action_id in command.get('pending_action_ids', []):
            action = record['actions'].get(action_id)
            if action is None or action['outcome'] != 'UNKNOWN' or resolutions.get(action_id, 'UNKNOWN') == 'UNKNOWN':
                continue
            outcome = resolutions[action_id]
            if action['command']['operation'] == 'start' and snapshot['state'] in ('STARTING', 'RUNNING', 'COMPLETED') \
                    and outcome in ('REJECTED', 'NOT_APPLIED'):
                raise HostError('start outcome contradicts active observation')
            report = self._report(command, snapshot, record, 'APPLIED')
            report['action_outcomes'][action_id] = outcome
            self.journal.finish_action(snapshot['owner'], action_id, report)
            record = self.journal.load(snapshot['owner'])
        return record

    def _release(self, snapshot, record, observed):
        if observed.get('cleanup') != 'CONFIRMED' or 'cleanup_evidence' not in observed:
            return
        if snapshot['state'] not in TERMINAL:
            raise HostError('active observation cannot release resources')
        try:
            self.journal.release(snapshot['owner'], observed['cleanup_evidence'])
        except CleanupIncompleteError:
            return
        if snapshot['cleanup'] != 'CONFIRMED':
            snapshot['sequence'] += 1  # Newly verified journal release, not a status heartbeat.
        snapshot['cleanup'] = 'CONFIRMED'
