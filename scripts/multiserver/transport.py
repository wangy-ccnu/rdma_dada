"""One-shot bounded SSH JSON transport (Python 3.8, POSIX control hosts).

Endpoints are trusted deployment configuration. An optional deployment_path
selects the operator-installed host configuration, never a client command field.
This module does not implement
remote supervision, action deduplication, cancellation or lifecycle validation.
"""
import json
import math
import os
import re
import selectors
import shlex
import subprocess
import time


_LIMIT = 1024 * 1024
# A command request is bounded to one hour: representable by POSIX selector
# timeouts and OpenSSH's integer ConnectTimeout, without deadline slicing.
_MAX_TIMEOUT_SECONDS = 3600
_ENDPOINT_FIELDS = {'ssh_host', 'python_path', 'host_entrypoint', 'known_hosts_file'}
_HOST = re.compile(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')


def _scalar(value):
    return (type(value) is str and
            not any(0xD800 <= ord(character) <= 0xDFFF for character in value))


def _path(value):
    return (_scalar(value) and os.path.isabs(value) and
            not any(ord(character) < 32 or ord(character) == 127 for character in value))


def _json_value(value, ancestors):
    kind = type(value)
    if kind is str:
        if not _scalar(value) or len(value) > _LIMIT:
            raise ValueError('transport JSON string is invalid or oversized')
    elif value is None or kind in (bool, int):
        return
    elif kind is float:
        if not math.isfinite(value):
            raise ValueError('transport JSON number must be finite')
    elif kind in (list, dict):
        identity = id(value)
        if identity in ancestors:
            raise ValueError('transport JSON must be acyclic')
        ancestors.add(identity)
        try:
            if kind is dict:
                for key, item in value.items():
                    if type(key) is not str:
                        raise ValueError('transport JSON keys must be strings')
                    _json_value(key, ancestors)
                    _json_value(item, ancestors)
            else:
                for item in value:
                    _json_value(item, ancestors)
        finally:
            ancestors.remove(identity)
    else:
        raise ValueError('transport value is not strict JSON')


def _encode(command):
    try:
        _json_value(command, set())
        result = bytearray()
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False,
                                   sort_keys=True, separators=(',', ':'))
        for fragment in encoder.iterencode(command):
            encoded = fragment.encode('utf-8', errors='strict')
            if len(result) + len(encoded) + 1 > _LIMIT:
                raise ValueError('request limit')
            result.extend(encoded)
        result.extend(b'\n')
        return result
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError('transport request is invalid or exceeds 1 MiB') from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('nonfinite constant')


def _decode(raw):
    try:
        result = json.loads(raw.decode('utf-8', errors='strict'),
                            object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
        if type(result) is not dict:
            raise ValueError('response is not an object')
        _json_value(result, set())
        return result
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError('transport response is not a strict UTF-8 JSON object') from None


class SSHHostAdapter:
    """Trusted server endpoints -> existing MS-06 send(key, command) interface."""

    def __init__(self, endpoints, *, ssh_path, timeout_seconds):
        if not _path(ssh_path):
            raise ValueError('ssh_path must be an absolute scalar path without controls')
        if (type(timeout_seconds) not in (int, float) or
                not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS):
            raise ValueError('timeout_seconds must be in (0, 3600] seconds')
        timeout = float(timeout_seconds)
        if type(endpoints) is not dict or not endpoints:
            raise ValueError('endpoints must be a nonempty server mapping')
        validated = {}
        for server_id, endpoint in endpoints.items():
            if not _scalar(server_id) or not server_id:
                raise ValueError('endpoint server identity is invalid')
            if (type(endpoint) is not dict or
                    set(endpoint) not in (_ENDPOINT_FIELDS, _ENDPOINT_FIELDS | {'deployment_path'})):
                raise ValueError('endpoint must contain exactly the deployment fields')
            host = endpoint['ssh_host']
            if type(host) is not str or not _HOST.fullmatch(host):
                raise ValueError('ssh_host must be a hostname/alias with optional user')
            if not all(_path(endpoint[field]) for field in
                       ('python_path', 'host_entrypoint', 'known_hosts_file')):
                raise ValueError('endpoint paths must be absolute and free of controls')
            if 'deployment_path' in endpoint and not _path(endpoint['deployment_path']):
                raise ValueError('deployment_path must be an absolute scalar path without controls')
            if any(character in endpoint['known_hosts_file'] for character in ('"', '\\', '%', '$')):
                raise ValueError('known_hosts_file must be a literal path without SSH expansion')
            validated[server_id] = dict(endpoint)
        self._endpoints = validated
        self._ssh_path = ssh_path
        self._timeout = timeout

    def send(self, instance_key, command):
        deadline = time.monotonic() + self._timeout
        if (type(instance_key) is not tuple or len(instance_key) != 2 or
                not _scalar(instance_key[0]) or
                type(instance_key[1]) is not int or instance_key[1] < 0):
            raise ValueError('transport instance identity is invalid')
        server_id, gpu_id = instance_key
        if (server_id not in self._endpoints or type(command) is not dict or
                type(command.get('server_id')) is not str or
                command.get('server_id') != server_id or
                type(command.get('gpu_id')) is not int or command.get('gpu_id') != gpu_id):
            raise ValueError('transport command/target identity mismatch')
        request = _encode(command)
        endpoint = self._endpoints[server_id]
        remote_argv = [endpoint['python_path'], '-B', endpoint['host_entrypoint']]
        if 'deployment_path' in endpoint:
            remote_argv.extend(['--deployment', endpoint['deployment_path']])
        remote = ' '.join(shlex.quote(argument) for argument in remote_argv)
        argv = [self._ssh_path, '-T', '-o', 'BatchMode=yes', '-o',
                'StrictHostKeyChecking=yes', '-o',
                'UserKnownHostsFile="' + endpoint['known_hosts_file'] + '"',
                '-o', 'ConnectTimeout=' + str(max(1, math.ceil(self._timeout))),
                '-o', 'ConnectionAttempts=1', '--', endpoint['ssh_host'], remote]
        if time.monotonic() >= deadline:
            raise TimeoutError('SSH request deadline exceeded; remote outcome unknown')
        try:
            child = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     bufsize=0, shell=False)
        except OSError:
            raise OSError('SSH child launch failed; remote outcome unknown') from None
        try:
            response = self._exchange(child, request, deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('SSH request deadline exceeded; remote outcome unknown')
            try:
                code = child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise TimeoutError('SSH request deadline exceeded; remote outcome unknown') from None
            if code != 0:
                raise OSError('SSH child exited with code {}; remote outcome unknown'.format(code))
            result = _decode(response)
            if time.monotonic() >= deadline:
                raise TimeoutError('SSH request deadline exceeded; remote outcome unknown')
            return result
        finally:
            # Reap only our local SSH child. Remote actions may already have run.
            if child.poll() is None:
                child.kill()
            child.wait()
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

    @staticmethod
    def _exchange(child, request, deadline):
        response = bytearray()
        counts = {'stdout': 0, 'stderr': 0}
        offset = 0
        with selectors.DefaultSelector() as selector:
            for stream, name, event in (
                    (child.stdin, 'stdin', selectors.EVENT_WRITE),
                    (child.stdout, 'stdout', selectors.EVENT_READ),
                    (child.stderr, 'stderr', selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('SSH request deadline exceeded; remote outcome unknown')
                for key, _ in selector.select(remaining):
                    stream, name = key.fileobj, key.data
                    try:
                        if name == 'stdin':
                            try:
                                offset += os.write(stream.fileno(), request[offset:offset + 65536])
                            except BrokenPipeError:
                                raise OSError('SSH request stdin closed early; remote outcome unknown') from None
                            if offset == len(request):
                                selector.unregister(stream)
                                stream.close()
                        else:
                            data = os.read(stream.fileno(), min(65536, _LIMIT - counts[name] + 1))
                            if not data:
                                selector.unregister(stream)
                                stream.close()
                                continue
                            counts[name] += len(data)
                            if counts[name] > _LIMIT:
                                raise ValueError('transport {} exceeds 1 MiB; remote outcome unknown'.format(name))
                            if name == 'stdout':
                                response.extend(data)
                    except BlockingIOError:
                        continue
        return response
