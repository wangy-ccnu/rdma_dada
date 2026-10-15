#!/usr/bin/env python3
"""Bounded, versioned SSH-disconnect probe with exact-identity cleanup.

The probe starts only this file in ``--heartbeat-child`` mode.  It accepts no
arbitrary command or shell text, exits after a short fixed lifetime, and keeps
its scope marker/record separate from product and runtime resources.
"""

import argparse
import datetime
import json
import os
import stat
import sys
import time
from pathlib import Path


MARKER = "multiserver-disconnect-probe-v1"
SCRIPT = Path(__file__).resolve()
MAX_DURATION_SECONDS = 30
_CHILDREN = {}


class ProbeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _scope_path(value):
    path = Path(value)
    if not path.is_absolute() or path.name == "" or not path.name.startswith("ms07b-disconnect-probe-"):
        raise ProbeError("INVALID_PATH", "scope must be an absolute ms07b-disconnect-probe-* directory")
    if path.exists() and path.is_symlink():
        raise ProbeError("INVALID_PATH", "scope may not be a symlink")
    return path.resolve()


def _json_path(scope, name):
    return scope / name


def _proc_available():
    return Path("/proc").is_dir()


def _safe_artifact(scope, name):
    path = scope / name
    try:
        info = path.lstat()
    except OSError:
        return path
    if stat.S_ISLNK(info.st_mode) or path.resolve(strict=False).parent != scope:
        raise ProbeError("INVALID_PATH", "refusing symlink or escaped artifact: %s" % name)
    return path


def _write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(str(path), 0o600)


def _read_json(path, error_code="CORRUPT_RECORD"):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProbeError(error_code, "cannot read valid JSON: %s" % exc)
    if not isinstance(value, dict):
        raise ProbeError(error_code, "record must be a JSON object")
    return value


def _write_scope_marker(scope, source_thread_id, task_id, retention):
    _write_json(scope / "scope.json", {
        "scope_marker": MARKER,
        "task_id": task_id,
        "source_thread_id": source_thread_id,
        "retention": retention,
        "created_utc": _utc_now(),
    })


def _load_scope(scope):
    if not scope.exists() or not scope.is_dir() or scope.is_symlink():
        raise ProbeError("INVALID_PATH", "scope is missing, not a directory, or a symlink")
    marker = _read_json(_safe_artifact(scope, "scope.json"))
    if marker.get("scope_marker") != MARKER:
        raise ProbeError("CORRUPT_RECORD", "scope marker mismatch")
    record = _read_json(_safe_artifact(scope, "probe.json"))
    if record.get("scope_marker") != MARKER or record.get("scope_dir") != str(scope):
        raise ProbeError("CORRUPT_RECORD", "probe record scope mismatch")
    for field in ("pid", "pid_starttime_ticks", "boot_id", "uid", "task_id", "source_thread_id", "retention"):
        if field not in record:
            raise ProbeError("CORRUPT_RECORD", "missing record field %s" % field)
    if record.get("task_id") != marker.get("task_id") or record.get("source_thread_id") != marker.get("source_thread_id"):
        raise ProbeError("CORRUPT_RECORD", "record identity differs from scope marker")
    for field in ("pid", "pid_starttime_ticks", "uid"):
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProbeError("CORRUPT_RECORD", "%s must be an integer" % field)
    if record["pid"] <= 1 or record["pid_starttime_ticks"] < 0 or record["uid"] < 0:
        raise ProbeError("CORRUPT_RECORD", "invalid process identity values")
    if not isinstance(record["boot_id"], str) or not record["boot_id"]:
        raise ProbeError("CORRUPT_RECORD", "invalid boot_id")
    return marker, record


def _boot_id():
    if not _proc_available():
        raise ProbeError("PROC_UNKNOWN", "Linux /proc is required for boot identity")
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ProbeError("PROC_UNKNOWN", "cannot read Linux boot identity: %s" % exc)


def _proc_identity(pid):
    if not _proc_available():
        return None
    stat_path = Path("/proc") / str(pid) / "stat"
    status_path = Path("/proc") / str(pid) / "status"
    try:
        stat = stat_path.read_text(encoding="ascii")
        status = status_path.read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        raise ProbeError("PROC_UNKNOWN", "cannot read process identity: %s" % exc)
    except OSError as exc:
        raise ProbeError("PROC_UNKNOWN", "cannot read process identity: %s" % exc)
    closing = stat.rfind(")")
    if closing < 0:
        raise ProbeError("PROC_UNKNOWN", "malformed /proc/%s/stat" % pid)
    fields = stat[closing + 2:].split()
    if len(fields) < 20:
        raise ProbeError("PROC_UNKNOWN", "malformed /proc/%s/stat" % pid)
    uid = None
    for line in status.splitlines():
        if line.startswith("Uid:"):
            uid = int(line.split()[1])
            break
    if uid is None:
        raise ProbeError("PROC_UNKNOWN", "missing process UID")
    return {"pid": int(pid), "pid_starttime_ticks": int(fields[19]), "uid": uid, "boot_id": _boot_id(), "state": fields[0]}


def _record_template(scope, source_thread_id, task_id, retention):
    scope = Path(scope).resolve()
    return {
        "schema_version": 1,
        "scope_marker": MARKER,
        "scope_dir": str(scope),
        "task_id": task_id,
        "source_thread_id": source_thread_id,
        "retention": retention,
        "argv": [sys.executable, str(SCRIPT), "--heartbeat-child"],
        "uid": os.getuid(),
        "boot_id": _boot_id(),
        "launched_utc": _utc_now(),
        "duration_seconds": None,
    }


def launch(args):
    if not _proc_available():
        raise ProbeError("PROC_UNAVAILABLE", "Linux /proc is required for an authoritative probe")
    scope = _scope_path(args.scope_dir)
    if scope.exists():
        if not scope.is_dir() or any(scope.iterdir()):
            raise ProbeError("SCOPE_NOT_EMPTY", "scope must be new or empty")
    else:
        scope.mkdir(mode=0o700, parents=False)
    _write_scope_marker(scope, args.source_thread_id, args.task_id, args.retention)
    log_path = scope / "probe.log"
    log = log_path.open("w", encoding="utf-8")
    child_argv = [sys.executable, str(SCRIPT), "--heartbeat-child", "--duration-seconds", str(args.duration_seconds)]
    process = __import__("subprocess").Popen(
        child_argv,
        stdin=__import__("subprocess").DEVNULL,
        stdout=log,
        stderr=__import__("subprocess").STDOUT,
        start_new_session=True,
        close_fds=True,
        cwd=str(scope),
    )
    log.close()
    identity = _proc_identity(process.pid)
    if identity is None:
        raise ProbeError("LAUNCH_FAILED", "child exited before identity could be recorded")
    record = _record_template(scope, args.source_thread_id, args.task_id, args.retention)
    record.update(identity)
    record["duration_seconds"] = args.duration_seconds
    record["argv"] = child_argv
    record["log_path"] = str(log_path)
    _CHILDREN[process.pid] = process
    _write_json(scope / "probe.json", record)
    record["state"] = "RUNNING"
    print(json.dumps({"state": "RUNNING", "record": record, "scope": json.loads((scope / "scope.json").read_text(encoding="utf-8"))}, sort_keys=True))
    return 0


def inspect(args):
    scope = _scope_path(args.scope_dir)
    _, record = _load_scope(scope)
    if not _proc_available():
        raise ProbeError("PROC_UNAVAILABLE", "Linux /proc is required for an authoritative probe")
    current = _proc_identity(int(record["pid"]))
    if current is None:
        state = "EXITED"
    elif {key: current[key] for key in ("pid", "pid_starttime_ticks", "uid", "boot_id")} != {key: record[key] for key in ("pid", "pid_starttime_ticks", "uid", "boot_id")}:
        state = "IDENTITY_MISMATCH"
    elif current.get("state") == "Z":
        state = "EXITED"
    else:
        state = "RUNNING"
    print(json.dumps({"state": state, "record": record}, sort_keys=True))
    return 0 if state != "IDENTITY_MISMATCH" else 2


def cleanup(args):
    scope = _scope_path(args.scope_dir)
    marker, record = _load_scope(scope)
    if not _proc_available():
        raise ProbeError("PROC_UNAVAILABLE", "Linux /proc is required for an authoritative probe")
    expected = {key: record[key] for key in ("pid", "pid_starttime_ticks", "uid", "boot_id")}
    current = _proc_identity(int(record["pid"]))
    if current is not None and {key: current[key] for key in expected} != expected:
        raise ProbeError("IDENTITY_MISMATCH", "PID identity does not match recorded boot/start/uid")
    if current is not None and current.get("state") != "Z":
        raise ProbeError("STILL_RUNNING", "probe must exit naturally before cleanup")
    child = _CHILDREN.pop(int(record["pid"]), None)
    if child is not None:
        try:
            child.wait(timeout=1.0)
        except Exception:
            pass
    for name in ("probe.json", "probe.log"):
        path = _safe_artifact(scope, name)
        if path.exists():
            path.unlink()
    report = {"scope_marker": MARKER, "task_id": marker["task_id"], "source_thread_id": marker["source_thread_id"], "retention": marker["retention"], "pid": record["pid"], "pid_starttime_ticks": record["pid_starttime_ticks"], "boot_id": record["boot_id"], "cleanup_utc": _utc_now(), "cleanup": "PASS"}
    _write_json(_safe_artifact(scope, "cleanup.json"), report)
    print(json.dumps({"cleanup": "PASS", "record": report}, sort_keys=True))
    return 0


def _heartbeat_child(args):
    deadline = time.monotonic() + args.duration_seconds
    while time.monotonic() < deadline:
        print(json.dumps({"heartbeat_utc": _utc_now(), "pid": os.getpid()}, sort_keys=True), flush=True)
        time.sleep(0.25)
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--heartbeat-child":
        child_parser = argparse.ArgumentParser()
        child_parser.add_argument("--heartbeat-child", action="store_true")
        child_parser.add_argument("--duration-seconds", type=int, choices=range(1, MAX_DURATION_SECONDS + 1), required=True)
        return _heartbeat_child(child_parser.parse_args(argv))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch_parser = sub.add_parser("launch")
    launch_parser.add_argument("--scope-dir", required=True)
    launch_parser.add_argument("--source-thread-id", required=True)
    launch_parser.add_argument("--task-id", required=True)
    launch_parser.add_argument("--retention", required=True)
    launch_parser.add_argument("--duration-seconds", type=int, choices=range(1, MAX_DURATION_SECONDS + 1), required=True)
    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--scope-dir", required=True)
    cleanup_parser = sub.add_parser("cleanup")
    cleanup_parser.add_argument("--scope-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "launch":
            return launch(args)
        if args.command == "inspect":
            return inspect(args)
        if args.command == "cleanup":
            return cleanup(args)
        return _heartbeat_child(args)
    except ProbeError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
