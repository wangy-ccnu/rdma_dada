"""Linux process supervision, internal B2 API (Python 3.8, no daemon/service).

ProcessSupervisor(root) uses one trusted, private host directory. Owner is exactly
the B1 binding: run_id, bundle_id, server_id, gpu_id, observation_sha256, generation.
The strings are data, never path components. A SHA256(owner, role) selects a
private directory with launch.json and exactly two owned log paths. One host root
must be shared by all callers for deduplication. flock serializes operations;
this is not distributed HA. The effective/real user must own the private files.

launch(owner, role, argv, cwd, env) -> report. argv is a nonempty list of scalar
strings with an absolute executable; cwd is an existing absolute directory;
env is an explicit string dictionary (no inherited environment). These are
TRUSTED host configuration, not client-selected commands. No shell is used.
stdin=DEVNULL, stdout/stderr=private files, close_fds and start_new_session detach
from request pipes. This covers normal SSH request exit on the observed Linux
host; no claim about reboot, all-session logout, logind policy or HA is made.
Identical owner/role/content returns the recorded launch; changed content raises
ProcessConflictError. A durable INTENT precedes Popen. INTENT after a crash stays
UNKNOWN and cannot replay, including a crash after spawn before identity save.

All four methods return detached dictionaries with exactly:
  identity: {owner, role, pid, start_ticks, boot_id, uid, pgid, sid}; process fields
            are all null for unresolved intent or a known failed spawn.
  state: RUNNING | EXITED | NOT_STARTED | UNKNOWN
  returncode: integer only for an authoritatively reaped child, otherwise null
  cleanup: CONFIRMED only for a known failed spawn; UNKNOWN for every spawned
           process. No generic /proc scan can prove all descendants are gone.
  reason: null or stable diagnostic string (no argv/environment/error text)
  signal_sent: boolean, True only for this call's successful os.kill
  logs: {stdout: absolute owned path, stderr: absolute owned path}
inspect(identity) checks the EXACT returned binding/fingerprint; it never adopts
a caller PID. A matching /proc sample means observed RUNNING, not control rights.
Missing, inaccessible or changed /proc identity means UNKNOWN, not clean exit.
wait(identity, timeout_seconds) accepts a finite [0, 3600] int/float, excluding
bool. It reaps only this supervisor's Popen children; timeout returns RUNNING.
Nonchildren cannot supply an authoritative exit status and return UNKNOWN.
An INTENT with a retained child can be waited/reaped: its code is saved, but its
state/identity/cleanup remain UNKNOWN/null/UNKNOWN without a captured fingerprint.
signal(identity, signum) accepts a positive supported OS signal. It signals only
an identity-matched, unreaped Popen child STILL held by this instance. The caller
must give this supervisor exclusive wait/reap ownership (no external waitpid or
SIGCHLD reaper). That retained child prevents PID reuse through check/kill.
Python3.8 has no standard pidfd_open: reopened/nonchild identities NEVER authorize
signals, even with matching /proc; check-then-kill would be unsafe. Never killpg.
The group/session fields are evidence about the leader, not all its descendants.
Cooperative parents may stop/reap their own children, or separately supervised
roles may be signalled individually; neither supplies generic cleanup proof.
Exit0 is not data completion, EOD, publication, or resource release evidence.

close() closes only descriptors and never signals/reaps. It refuses to discard unreaped
Popen handles (ProcessBusyError); wait/reap them first. Request process exit does
not kill detached children, but a later supervisor cannot signal those nonchildren.
No background reaper is installed. Healthy request exit/reconnect is inspection
only unless a separately verified runtime supplies a safe stop mechanism.

ProcessValidationError(ValueError) rejects inputs; ProcessConflictError rejects
content/identity rebinding; ProcessStorageError refuses inaccessible, symlinked,
hardlinked, unsafe-permission, inconsistent/corrupt state without resetting it;
ProcessClosedError rejects closed use; ProcessIdentityError marks uncertain /proc.
Storage failure after spawn propagates with durable uncertainty and the child
handle retained. Logs are private direct files, without rotation or size quota;
disk budgeting and authoritative descendant/resource cleanup belong to runtime.
No existing records/logs are deleted. POSIX local filesystem fsync/flock/atomic
replace semantics and a trusted same-UID host are prerequisites.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal as signals
import stat
import subprocess
import sys
import threading
import uuid

from .store import _validate_json, _unique_object, _reject_constant, StoreValidationError


class ProcessError(Exception):
    """Base for supervisor failures."""


class ProcessValidationError(ProcessError, ValueError):
    """Invalid caller input; no launch or signal is authorized."""


class ProcessConflictError(ProcessError):
    """Recorded owner/role/content/fingerprint does not match."""


class ProcessStorageError(ProcessError):
    """Untrusted or unavailable storage; never reset or assume empty."""


class ProcessClosedError(ProcessError):
    """Operation after close."""


class ProcessBusyError(ProcessError):
    """Live owned Popen handles must not be discarded by close."""


class ProcessIdentityError(ProcessError):
    """Process identity could not be sampled consistently."""


_OWNER = {'run_id', 'bundle_id', 'server_id', 'gpu_id', 'observation_sha256', 'generation'}
_PROC = {'pid', 'start_ticks', 'boot_id', 'uid', 'pgid', 'sid'}
_FIELDS = {'version', 'owner', 'role', 'argv', 'cwd', 'env', 'state', 'process',
           'returncode', 'error'}
_LIMIT = 1024 * 1024
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _json(value):
    try:
        _validate_json(value, 'process', set())
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                          allow_nan=False)
    except (StoreValidationError, ValueError, RecursionError) as error:
        raise ProcessValidationError('invalid strict JSON') from error


def _text(value):
    if type(value) is not str or not value or '\x00' in value:
        raise ProcessValidationError('expected nonempty scalar text without NUL')
    _json(value)


def _owner(value):
    _json(value)
    if type(value) is not dict or set(value) != _OWNER:
        raise ProcessValidationError('invalid owner fields')
    for name in _OWNER - {'gpu_id'}:
        _text(value[name])
    if type(value['gpu_id']) is not int or value['gpu_id'] < 0:
        raise ProcessValidationError('gpu_id must be a nonnegative integer')
    if not re.fullmatch('[0-9a-f]{64}', value['observation_sha256']):
        raise ProcessValidationError('invalid observation hash')
    return json.loads(_json(value))


def _path(value):
    try:
        value = os.fspath(value)
    except TypeError as error:
        raise ProcessValidationError('invalid path') from error
    _text(value)
    if not value.startswith('/') or any(part in ('.', '..') for part in value.split('/')):
        raise ProcessValidationError('path must be absolute without traversal')
    return str(Path(value))


def _command(argv, cwd, env):
    _json([argv, cwd, env])
    if type(argv) is not list or not argv:
        raise ProcessValidationError('argv must be a nonempty list')
    # Empty non-executable arguments and empty environment values are valid.
    for arg in argv:
        if type(arg) is not str or '\x00' in arg:
            raise ProcessValidationError('invalid argv string')
    _path(argv[0])
    cwd = _path(cwd)
    if type(env) is not dict:
        raise ProcessValidationError('env must be an explicit dictionary')
    for key, value in env.items():
        _text(key)
        if '=' in key or type(value) is not str or '\x00' in value:
            raise ProcessValidationError('invalid environment')
    if len(_json([argv, cwd, env]).encode('ascii')) > _LIMIT // 2:
        raise ProcessValidationError('launch input too large')
    return json.loads(_json(argv)), cwd, json.loads(_json(env))


def _process(value):
    if type(value) is not dict or set(value) != _PROC:
        raise ProcessValidationError('invalid process fields')
    for field in _PROC - {'boot_id'}:
        if type(value[field]) is not int or value[field] < (0 if field == 'uid' else 1):
            raise ProcessValidationError('invalid process integer')
    if type(value['boot_id']) is not str or not re.fullmatch(
            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value['boot_id']):
        raise ProcessValidationError('invalid boot ID')
    return value


def _key(owner, role):
    return hashlib.sha256(_json([owner, role]).encode('ascii')).hexdigest()


def _private(fd, directory=False):
    info = os.fstat(fd)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or (not directory and info.st_nlink != 1)):
        raise ProcessStorageError('storage must be private, owned and of the expected type')
    return info


def _directory(path, create=False):
    """Walk from / with no symlink following, optionally creating only final dir."""
    fd = os.open('/', _DIR_FLAGS)
    parts = Path(path).parts[1:]
    try:
        for index, part in enumerate(parts):
            if create and index == len(parts) - 1:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_process(pid, *, allow_exited=False):
    """Sample /proc twice without reaping; disappearance/change is uncertainty."""
    def read(path):
        with open(path, 'r', encoding='ascii') as stream:
            return stream.read(65537)

    def sample():
        base = '/proc/{}/'.format(pid)
        boot = read('/proc/sys/kernel/random/boot_id').strip()
        raw = read(base + 'stat')
        prefix, sep, suffix = raw.rpartition(')')
        fields = suffix.split()
        if not sep or int(prefix.split(' ', 1)[0]) != pid or len(fields) < 20:
            raise ProcessIdentityError('invalid proc stat')
        live_states = {'R', 'S', 'D', 'T', 't', 'I', 'P', 'W'}
        if fields[0] not in live_states and not (allow_exited and fields[0] in {'Z', 'X', 'x'}):
            raise ProcessIdentityError('process is exiting or has unknown state')
        uid_rows = [line.split()[1:] for line in read(base + 'status').splitlines()
                    if line.startswith('Uid:')]
        if len(uid_rows) != 1 or len(uid_rows[0]) != 4:
            raise ProcessIdentityError('invalid proc UID')
        uids = [int(value) for value in uid_rows[0]]
        # A setuid transition is outside this launcher's authority.
        if len(set(uids)) != 1:
            raise ProcessIdentityError('proc credentials changed')
        return _process(dict(pid=pid, start_ticks=int(fields[19]), boot_id=boot,
                             uid=uids[0], pgid=int(fields[2]), sid=int(fields[3])))
    try:
        first, second = sample(), sample()
        if first != second:
            raise ProcessIdentityError('proc identity changed during sample')
        return first
    except (ValueError, IndexError, UnicodeError, ProcessValidationError) as error:
        raise ProcessIdentityError('invalid proc identity') from error


class ProcessSupervisor:
    def __init__(self, root):
        self.root = _path(root)
        self._root_fd = self._lock_fd = None
        self._children = {}
        self._mutex = threading.RLock()
        try:
            self._root_fd = _directory(self.root, create=True)
            _private(self._root_fd, directory=True)
            self._lock_fd = os.open('.lock', os.O_RDWR | os.O_CREAT | _FILE_FLAGS,
                                    0o600, dir_fd=self._root_fd)
            _private(self._lock_fd)
            os.fsync(self._root_fd)
        except BaseException as error:
            for fd in (self._lock_fd, self._root_fd):
                if fd is not None:
                    os.close(fd)
            self._root_fd = self._lock_fd = None
            if isinstance(error, OSError):
                raise ProcessStorageError('cannot open trusted supervisor root') from error
            raise

    @contextmanager
    def _locked(self):
        with self._mutex:
            if self._root_fd is None:
                raise ProcessClosedError('supervisor closed')
            try:
                # Refuse pathname replacement; all subsequent IO stays fd-relative.
                current = _directory(self.root)
                try:
                    a, b = _private(current, True), _private(self._root_fd, True)
                    if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                        raise ProcessStorageError('supervisor root replaced')
                finally:
                    os.close(current)
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
                try:
                    lock_info = os.stat('.lock', dir_fd=self._root_fd, follow_symlinks=False)
                    held = _private(self._lock_fd)
                    if (lock_info.st_dev, lock_info.st_ino) != (held.st_dev, held.st_ino):
                        raise ProcessStorageError('supervisor lock replaced')
                    yield
                finally:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            except OSError as error:
                raise ProcessStorageError('supervisor storage operation failed') from error

    @contextmanager
    def _slot(self, key, create=False):
        new = False
        if create:
            try:
                os.mkdir(key, 0o700, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
                new = True
            except FileExistsError:
                pass
        fd = os.open(key, _DIR_FLAGS, dir_fd=self._root_fd)
        try:
            _private(fd, True)
            yield fd, new
        finally:
            os.close(fd)

    def _load(self, fd, key):
        record_fd = os.open('launch.json', os.O_RDONLY | _FILE_FLAGS, dir_fd=fd)
        try:
            _private(record_fd)
            with os.fdopen(record_fd, 'rb', closefd=False) as stream:
                raw = stream.read(_LIMIT + 1)
        finally:
            os.close(record_fd)
        try:
            if len(raw) > _LIMIT:
                raise ValueError('oversized record')
            record = json.loads(raw.decode('ascii'), object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant)
            _json(record)
            if type(record) is not dict or set(record) != _FIELDS or type(record['version']) is not int \
                    or record['version'] != 1:
                raise ValueError('unknown record schema')
            _owner(record['owner'])
            _text(record['role'])
            _command(record['argv'], record['cwd'], record['env'])
            if key != _key(record['owner'], record['role']):
                raise ValueError('record binding mismatch')
            state, proc, code, error = (record[k] for k in ('state', 'process', 'returncode', 'error'))
            if state in ('RUNNING', 'EXITED'):
                _process(proc)
                if proc['uid'] != os.getuid() or proc['pgid'] != proc['pid'] or proc['sid'] != proc['pid']:
                    raise ValueError('invalid session/UID ownership')
                if error is not None or (state == 'RUNNING' and code is not None) \
                        or (state == 'EXITED' and type(code) is not int):
                    raise ValueError('invalid process outcome')
            elif state in ('INTENT', 'NOT_STARTED'):
                if proc is not None or (code is not None and (
                        state == 'NOT_STARTED' or type(code) is not int)) or error != (
                        'spawn_failed' if state == 'NOT_STARTED' else None):
                    raise ValueError('invalid intent/outcome')
            else:
                raise ValueError('unknown record state')
        except (ValueError, TypeError, KeyError, RecursionError, ProcessValidationError) as error:
            raise ProcessStorageError('corrupt or inconsistent process record') from error
        for name in ('stdout.log', 'stderr.log'):
            try:
                log_fd = os.open(name, os.O_WRONLY | _FILE_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if state in ('RUNNING', 'EXITED'):
                    raise ProcessStorageError('missing process log')
            else:
                try:
                    _private(log_fd)
                finally:
                    os.close(log_fd)
        return record

    def _save(self, fd, record):
        raw = (_json(record) + '\n').encode('ascii')
        if len(raw) > _LIMIT:
            raise ProcessStorageError('oversized process record')
        name = '.write-' + uuid.uuid4().hex
        temp_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                          0o600, dir_fd=fd)
        try:
            with os.fdopen(temp_fd, 'wb', closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(temp_fd)
            os.replace(name, 'launch.json', src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(temp_fd)
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass

    def _identity(self, record):
        proc = record['process'] or dict.fromkeys(_PROC)
        return dict(owner=record['owner'], role=record['role'], **proc)

    def _report(self, record, state=None, reason=None, signal_sent=False):
        state = state or record['state']
        if state == 'INTENT':
            state, reason = 'UNKNOWN', 'launch_intent_unresolved'
        elif state == 'NOT_STARTED':
            reason = 'spawn_failed'
        base = Path(self.root) / _key(record['owner'], record['role'])
        return json.loads(_json(dict(identity=self._identity(record), state=state,
            returncode=record['returncode'], cleanup=(
                'CONFIRMED' if record['state'] == 'NOT_STARTED' else 'UNKNOWN'), reason=reason,
            signal_sent=signal_sent,
            logs=dict(stdout=str(base / 'stdout.log'), stderr=str(base / 'stderr.log')))))

    def _refresh(self, fd, key, record):
        if record['state'] != 'RUNNING':
            return self._report(record)
        child = self._children.get(key)
        if child is not None:
            code = child.poll()
            if code is not None:
                record.update(state='EXITED', returncode=code)
                self._save(fd, record)
                return self._report(record)
        try:
            observed = _read_process(record['process']['pid'])
        except (OSError, ProcessIdentityError):
            return self._report(record, 'UNKNOWN', 'process_identity_unavailable')
        if observed != record['process']:
            return self._report(record, 'UNKNOWN', 'process_identity_mismatch')
        return self._report(record)

    @contextmanager
    def _bound(self, identity):
        _json(identity)
        if type(identity) is not dict or set(identity) != _PROC | {'owner', 'role'}:
            raise ProcessValidationError('invalid identity fields')
        owner = _owner(identity['owner'])
        _text(identity['role'])
        proc = {name: identity[name] for name in _PROC}
        if any(value is not None for value in proc.values()):
            _process(proc)
        key = _key(owner, identity['role'])
        with self._locked(), self._slot(key) as (fd, _):
            record = self._load(fd, key)
            if self._identity(record) != identity:
                raise ProcessConflictError('identity does not exactly match recorded launch')
            yield fd, key, record

    def launch(self, owner, role, argv, cwd, env):
        owner = _owner(owner)
        _text(role)
        argv, cwd, env = _command(argv, cwd, env)
        if len(_json([owner, role]).encode('ascii')) > _LIMIT // 4:
            raise ProcessValidationError('owner/role too large')
        key = _key(owner, role)
        with self._locked(), self._slot(key, True) as (fd, new):
            if not new:
                record = self._load(fd, key)
                if (record['argv'], record['cwd'], record['env']) != (argv, cwd, env):
                    raise ProcessConflictError('owner/role already binds different launch content')
                return self._refresh(fd, key, record)
            if sys.platform != 'linux':
                raise ProcessValidationError('new process launches require Linux /proc')
            cwd_fd = _directory(cwd)
            os.close(cwd_fd)
            record = dict(version=1, owner=owner, role=role, argv=argv, cwd=cwd, env=env,
                          state='INTENT', process=None, returncode=None, error=None)
            self._save(fd, record)
            logs = []
            try:
                for name in ('stdout.log', 'stderr.log'):
                    logs.append(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                                        0o600, dir_fd=fd))
                os.fsync(fd)
                try:
                    child = subprocess.Popen(argv, cwd=cwd, env=env, shell=False,
                        stdin=subprocess.DEVNULL, stdout=logs[0], stderr=logs[1],
                        start_new_session=True, close_fds=True)
                except OSError:
                    record.update(state='NOT_STARTED', error='spawn_failed')
                    self._save(fd, record)
                    return self._report(record)
                # Retain immediately; failures below never drop/replay this child.
                self._children[key] = child
                try:
                    # Identity still exists for an unreaped, fast-exiting child.
                    # Only launch capture permits zombies; inspect cannot call
                    # a reopened zombie RUNNING.
                    proc = _read_process(child.pid, allow_exited=True)
                except (OSError, ProcessIdentityError):
                    return self._report(record)
                if proc['uid'] != os.getuid() or proc['pgid'] != child.pid or proc['sid'] != child.pid:
                    return self._report(record)
                record.update(state='RUNNING', process=proc)
                self._save(fd, record)
                return self._refresh(fd, key, record)
            finally:
                for log_fd in logs:
                    os.close(log_fd)

    def inspect(self, identity):
        with self._bound(identity) as (fd, key, record):
            return self._refresh(fd, key, record)

    def signal(self, identity, signum):
        if type(signum) is not int and not isinstance(signum, signals.Signals):
            raise ProcessValidationError('invalid signal')
        if isinstance(signum, bool) or signum <= 0 or signum not in signals.valid_signals():
            raise ProcessValidationError('invalid signal')
        with self._bound(identity) as (fd, key, record):
            result = self._refresh(fd, key, record)
            if result['state'] != 'RUNNING':
                return result
            child = self._children.get(key)
            if child is None or child.pid != record['process']['pid']:
                return self._report(record, 'UNKNOWN', 'no_retained_child_handle')
            # Do not poll/reap again between check and kill: an unreaped child PID
            # cannot be reused. Never apply this to reopened/nonchild processes.
            try:
                os.kill(child.pid, signum)
            except OSError:
                return self._report(record, 'UNKNOWN', 'signal_not_confirmed')
            return self._report(record, signal_sent=True)

    def wait(self, identity, timeout_seconds):
        if type(timeout_seconds) not in (int, float) or not 0 <= timeout_seconds <= 3600 \
                or not math.isfinite(timeout_seconds):
            raise ProcessValidationError('timeout must be finite and in [0, 3600]')
        with self._bound(identity) as (fd, key, record):
            child = self._children.get(key)
            if record['state'] == 'INTENT' and child is not None:
                try:
                    code = child.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    return self._report(record)
                if record['returncode'] != code:
                    record['returncode'] = code
                    self._save(fd, record)
                return self._report(record)
            result = self._refresh(fd, key, record)
            if result['state'] != 'RUNNING':
                return result
            child = self._children.get(key)
            if child is None:
                return self._report(record, 'UNKNOWN', 'no_retained_child_handle')
            try:
                code = child.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                return self._refresh(fd, key, record)
            record.update(state='EXITED', returncode=code)
            self._save(fd, record)
            return self._report(record)

    def close(self):
        with self._mutex:
            if self._root_fd is None:
                return
            if any(child.returncode is None for child in self._children.values()):
                raise ProcessBusyError('unreaped children require retained wait/reap ownership')
            os.close(self._lock_fd)
            os.close(self._root_fd)
            self._root_fd = self._lock_fd = None
