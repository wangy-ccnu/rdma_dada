"""Transactional SQLite snapshots for control-local run records.

The caller owns lifecycle policy and stores it inside the snapshot. Store
metadata is returned separately as ``{run_id, revision, snapshot}``. Only a
new filesystem path is initialized; existing databases are never migrated or
reset. This is optimistic revision protection, not multi-controller HA.
"""

import json
import math
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


_SCHEMA_VERSION = 1
_CREATE_TABLE = """CREATE TABLE run_documents (
    run_id TEXT PRIMARY KEY NOT NULL CHECK (length(CAST(run_id AS BLOB)) > 0),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision > 0),
    snapshot TEXT NOT NULL CHECK (typeof(snapshot) = 'text')
)"""


class StoreError(Exception):
    """Base class for RunStore failures."""


class StoreValidationError(StoreError, ValueError):
    """Invalid run key, expected revision or strict finite JSON object."""


class RunExistsError(StoreError):
    def __init__(self, run_id):
        self.run_id = run_id
        super().__init__("run {!r} already exists".format(run_id))


class RunNotFoundError(StoreError):
    def __init__(self, run_id):
        self.run_id = run_id
        super().__init__("run {!r} does not exist".format(run_id))


class RevisionConflictError(StoreError):
    def __init__(self, run_id, expected_revision, actual_revision):
        self.run_id = run_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__("run {!r}: expected revision {}, actual revision {}".format(
            run_id, expected_revision, actual_revision))


class StoreDatabaseError(StoreError):
    """Database open, schema, transaction or I/O failure; no reset attempted."""


class UnsupportedStoreVersionError(StoreDatabaseError):
    def __init__(self, path, version):
        self.version = version
        super().__init__("{}: unsupported store schema version {} (expected {})".format(
            path, version, _SCHEMA_VERSION))


class StoreCorruptionError(StoreDatabaseError):
    """A stored document or database is invalid and must not be overwritten."""


class StoreClosedError(StoreError):
    """An operation was attempted after close()."""


def _validate_json(value, path, active):
    kind = type(value)
    if kind is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise StoreValidationError(
                "{}: expected Unicode scalar string (no surrogate code points)".format(path)
            ) from error
        return
    if value is None or kind in (bool, int):
        return
    if kind is float:
        if not math.isfinite(value):
            raise StoreValidationError("{}: expected finite JSON number".format(path))
        return
    if kind not in (dict, list):
        raise StoreValidationError("{}: unsupported JSON type {}".format(path, kind.__name__))
    identity = id(value)
    if identity in active:
        raise StoreValidationError("{}: cyclic JSON value".format(path))
    active.add(identity)
    try:
        if kind is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise StoreValidationError("{}: JSON object keys must be strings".format(path))
                _validate_json(key, "{} key {!r}".format(path, key), active)
                _validate_json(child, "{}[{!r}]".format(path, key), active)
        else:
            for index, child in enumerate(value):
                _validate_json(child, "{}[{}]".format(path, index), active)
    finally:
        active.remove(identity)


def _encode_snapshot(snapshot):
    if type(snapshot) is not dict:
        raise StoreValidationError("snapshot: expected JSON object")
    try:
        _validate_json(snapshot, "snapshot", set())
        return json.dumps(snapshot, allow_nan=False, ensure_ascii=True, separators=(",", ":"))
    except (RecursionError, ValueError) as error:
        if isinstance(error, StoreValidationError):
            raise
        raise StoreValidationError("snapshot: {}".format(error)) from error


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key {!r}".format(key))
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant {}".format(value))


class RunStore:
    def __init__(self, path):
        self._connection = None
        try:
            self.path = Path(path).absolute()
            if "\x00" in str(self.path):
                raise ValueError("embedded null byte")
        except (TypeError, ValueError) as error:
            raise StoreValidationError("database path: {}".format(error)) from error
        try:
            # Claim a new file exclusively. Even an existing empty file is an
            # unknown database, not permission to initialize it. URI mode=rw
            # also prevents SQLite from recreating an existing file if removed.
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                new_database = False
            else:
                os.close(descriptor)
                new_database = True
            self._connection = sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
            if new_database:
                with self._transaction("initialize"):
                    self._connection.execute(_CREATE_TABLE)
                    self._connection.execute("PRAGMA user_version = 1")
            self._validate_database()
        except (OSError, sqlite3.Error) as error:
            self.close()
            raise StoreDatabaseError("{}: open database: {}".format(self.path, error)) from error
        except BaseException:
            self.close()
            raise

    def _validate_database(self):
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version != _SCHEMA_VERSION:
            raise UnsupportedStoreVersionError(self.path, version)
        objects = self._connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
        ).fetchall()
        if objects != [("table", "run_documents", _CREATE_TABLE)]:
            raise StoreDatabaseError("{}: unrecognized store schema".format(self.path))
        if self._connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise StoreCorruptionError("{}: database integrity check failed".format(self.path))

    def _ensure_open(self):
        if self._connection is None:
            raise StoreClosedError("{}: store is closed".format(self.path))

    @contextmanager
    def _transaction(self, operation):
        self._ensure_open()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            self._connection.commit()
        except BaseException as error:
            try:
                self._connection.rollback()
            except sqlite3.Error as rollback_error:
                self.close()
                raise StoreDatabaseError("{}: {}: {}; rollback failed: {}".format(
                    self.path, operation, error, rollback_error)) from error
            if isinstance(error, sqlite3.Error):
                raise StoreDatabaseError("{}: {}: {}".format(self.path, operation, error)) from error
            raise

    @staticmethod
    def _validate_run_id(run_id):
        if type(run_id) is not str or not run_id:
            raise StoreValidationError("run_id: expected nonempty string")
        try:
            run_id.encode("utf-8")
        except UnicodeEncodeError as error:
            raise StoreValidationError("run_id: expected UTF-8 encodable string") from error

    def _read(self, run_id):
        row = self._connection.execute(
            "SELECT revision, snapshot FROM run_documents WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        revision, payload = row
        try:
            if type(revision) is not int or revision < 1 or type(payload) is not str:
                raise ValueError("invalid stored revision or snapshot type")
            snapshot = json.loads(payload, object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant)
            _encode_snapshot(snapshot)
        except (ValueError, RecursionError) as error:
            raise StoreCorruptionError("{}: run {!r}: {}".format(self.path, run_id, error)) from error
        return {"run_id": run_id, "revision": revision, "snapshot": snapshot}

    def create(self, run_id: str, snapshot: dict) -> dict:
        self._ensure_open()
        self._validate_run_id(run_id)
        payload = _encode_snapshot(snapshot)
        with self._transaction("create run {!r}".format(run_id)):
            if self._connection.execute(
                "SELECT 1 FROM run_documents WHERE run_id = ?", (run_id,)
            ).fetchone() is not None:
                raise RunExistsError(run_id)
            self._connection.execute(
                "INSERT INTO run_documents (run_id, revision, snapshot) VALUES (?, 1, ?)",
                (run_id, payload))
            record = self._read(run_id)
        return record

    def load(self, run_id: str) -> dict:
        self._ensure_open()
        self._validate_run_id(run_id)
        try:
            return self._read(run_id)
        except sqlite3.Error as error:
            raise StoreDatabaseError("{}: load run {!r}: {}".format(
                self.path, run_id, error)) from error

    def save(self, run_id: str, expected_revision: int, snapshot: dict) -> dict:
        self._ensure_open()
        self._validate_run_id(run_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise StoreValidationError("expected_revision: expected positive integer (not bool)")
        payload = _encode_snapshot(snapshot)
        with self._transaction("save run {!r}".format(run_id)):
            previous = self._read(run_id)
            if previous["revision"] != expected_revision:
                raise RevisionConflictError(run_id, expected_revision, previous["revision"])
            # Compare under the write transaction before binding the revision:
            # even a Python integer beyond SQLite's int64 range gets a conflict.
            self._connection.execute(
                "UPDATE run_documents SET revision = revision + 1, snapshot = ? "
                "WHERE run_id = ? AND revision = ?", (payload, run_id, expected_revision))
            record = self._read(run_id)
        return record

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
