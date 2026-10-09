import hashlib
import os
import pathlib
import struct
import tempfile
import unittest
from unittest import mock

import scripts.multiserver.weights as weights
from scripts.multiserver.weights import slice_weights
from multiserver_fixture import write_weight_fixture


class WeightSliceTest(unittest.TestCase):
    def test_tail_three_channels_preserve_signed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "global.npy", root / "local.npy"
            payload = write_weight_fixture(source, (7, 1, 4, 2, 2), "|i1")
            evidence = slice_weights(source, target, 4, 3)
            self.assertEqual(evidence["shape"], [3, 1, 4, 2, 2])
            self.assertEqual(evidence["source_shape"], [7, 1, 4, 2, 2])
            self.assertEqual(evidence["dtype"], "|i1")
            bytes_per_f = 1 * 4 * 2 * 2
            expected = payload[4 * bytes_per_f:7 * bytes_per_f]
            self.assertEqual(target.read_bytes()[-len(expected):], expected)

    def test_both_versions_and_dtypes_recombine_in_f_order(self):
        shape = (7, 2, 3, 2, 2)
        for version in (1, 2):
            for dtype in ("|i1", "<i2"):
                with self.subTest(version=version, dtype=dtype):
                    with tempfile.TemporaryDirectory() as directory:
                        root = pathlib.Path(directory)
                        source = root / "global.npy"
                        first = root / "first.npy"
                        tail = root / "tail.npy"
                        payload = write_weight_fixture(
                            source, shape, dtype, version=version)
                        before = source.read_bytes()
                        first_evidence = slice_weights(source, first, 0, 4)
                        tail_evidence = slice_weights(source, tail, 4, 3)
                        bytes_per_f = shape[1] * shape[2] * shape[3] * shape[4]
                        item_bytes = 1 if dtype == "|i1" else 2
                        first_payload = first.read_bytes()[-4 * bytes_per_f * item_bytes:]
                        tail_payload = tail.read_bytes()[-3 * bytes_per_f * item_bytes:]
                        self.assertEqual(
                            first_payload + tail_payload, payload)
                        self.assertEqual(first_evidence["shape"], [4, 2, 3, 2, 2])
                        self.assertEqual(tail_evidence["shape"], [3, 2, 3, 2, 2])
                        self.assertEqual(source.read_bytes(), before)
                        self.assertEqual(
                            first_evidence["source_sha256"],
                            hashlib.sha256(before).hexdigest())
                        self.assertEqual(
                            first_evidence["local_sha256"],
                            hashlib.sha256(first.read_bytes()).hexdigest())

    def test_nonzero_middle_slice_has_expected_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "middle.npy"
            payload = write_weight_fixture(source, (7, 1, 1, 1, 2), "<i2")
            evidence = slice_weights(source, target, 2, 2)
            self.assertEqual(evidence["first_f"], 2)
            self.assertEqual(evidence["nchan"], 2)
            self.assertEqual(evidence["dtype"], "<i2")
            self.assertEqual(target.read_bytes()[-8:], payload[2 * 2 * 2:4 * 2 * 2])

    def test_same_input_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source_a, source_b = root / "a.npy", root / "b.npy"
            target_a, target_b = root / "a-local.npy", root / "b-local.npy"
            write_weight_fixture(source_a, (7, 1, 2, 2, 2), "|i1", version=2)
            write_weight_fixture(source_b, (7, 1, 2, 2, 2), "|i1", version=2)
            evidence_a = slice_weights(source_a, target_a, 3, 2)
            evidence_b = slice_weights(source_b, target_b, 3, 2)
            self.assertEqual(target_a.read_bytes(), target_b.read_bytes())
            self.assertEqual(evidence_a, evidence_b)

    def test_v2_accepts_historical_sixteen_byte_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "local.npy"
            payload = write_weight_fixture(
                source, (3, 1, 1, 1, 2), "|i1", version=2, alignment=16)
            evidence = slice_weights(source, target, 1, 1)
            self.assertEqual(target.read_bytes()[-2:], payload[2:4])
            self.assertEqual(target.read_bytes()[6:8], b"\x02\x00")
            self.assertEqual((12 + int.from_bytes(target.read_bytes()[8:12], "little")) % 64, 0)

    def test_rejects_duplicate_header_keys_before_literal_eval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source.npy"
            payload = write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            _write_custom_npy(source, payload, (
                "{'descr': '|i1', 'fortran_order': False, "
                "'shape': (3, 1, 1, 1, 2), "
                "'shape': (1, 3, 1, 1, 2), }"))
            with self.assertRaisesRegex(ValueError, "NPY_HEADER_DUPLICATE_KEY"):
                slice_weights(source, root / "local.npy", 0, 1)

    def test_rejects_boolean_zero_and_noninteger_dimensions(self):
        headers = (
            "(True, 1, 1, 1, 2)",
            "(0, 1, 1, 1, 2)",
            "(3.0, 1, 1, 1, 2)",
        )
        for shape_text in headers:
            with self.subTest(shape=shape_text):
                with tempfile.TemporaryDirectory() as directory:
                    root = pathlib.Path(directory)
                    source = root / "source.npy"
                    payload = write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
                    _write_custom_npy(source, payload, (
                        "{'descr': '|i1', 'fortran_order': False, 'shape': "
                        + shape_text + ", }"))
                    with self.assertRaisesRegex(ValueError, "NPY_SHAPE"):
                        slice_weights(source, root / "local.npy", 0, 1)

    def test_rejects_oversized_header_before_reading_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "oversized.npy"
            source.write_bytes(b"\x93NUMPY\x02\x00" + struct.pack("<I", 1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "NPY_HEADER_BOUNDS"):
                slice_weights(source, root / "local.npy", 0, 1)

    def test_payload_reads_are_bounded_and_byte_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "large.npy", root / "local.npy"
            shape = (3, 1, 1, 3000, 2)
            payload = write_weight_fixture(source, shape, "|i1")
            source_data = source.read_bytes()
            source_header_length = int.from_bytes(source_data[8:10], "little")
            source_payload_offset = 10 + source_header_length
            source_reads = []
            output_writes = []
            real_path_open = weights.Path.open
            real_fdopen = os.fdopen

            def guarded_path_open(path, mode="r", buffering=-1, encoding=None,
                                  errors=None, newline=None):
                wrapped = real_path_open(
                    path, mode, buffering=buffering, encoding=encoding,
                    errors=errors, newline=newline)
                return _GuardedReader(wrapped, source_payload_offset, 17, source_reads)

            def guarded_fdopen(descriptor, mode):
                wrapped = real_fdopen(descriptor, mode)
                return _GuardedWriter(wrapped, 17, output_writes)

            with mock.patch.object(weights, "_COPY_CHUNK_BYTES", 17):
                with mock.patch.object(weights.Path, "open", new=guarded_path_open):
                    with mock.patch.object(weights.os, "fdopen", side_effect=guarded_fdopen):
                        slice_weights(source, target, 1, 1)
            bytes_per_f = shape[1] * shape[2] * shape[3] * shape[4]
            self.assertEqual(target.read_bytes()[-bytes_per_f:], payload[bytes_per_f:2 * bytes_per_f])
            self.assertGreater(len(source_reads), 1)
            self.assertGreater(len(output_writes), 1)
            self.assertTrue(all(0 < size <= 17 for size in source_reads))
            self.assertTrue(all(0 < size <= 17 for size in output_writes))

    def test_rejects_invalid_slice_types_and_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source.npy"
            write_weight_fixture(source, (7, 1, 1, 1, 2), "|i1")
            cases = ((True, 1), (0, False), (-1, 1), (7, 1), (6, 2))
            for first_f, nchan in cases:
                with self.subTest(first_f=first_f, nchan=nchan):
                    with self.assertRaisesRegex(ValueError, "SLICE_"):
                        slice_weights(source, root / (str(first_f) + ".npy"), first_f, nchan)

    def test_rejects_malformed_and_unsupported_headers(self):
        mutations = {
            "literal": (b"False", b"false", "NPY_HEADER_MALFORMED"),
            "fortran": (b"False", b"True ", "NPY_FORTRAN_ORDER"),
            "dtype": (b"|i1", b">i1", "NPY_UNSUPPORTED_DTYPE"),
            "shape": (b"(3, 1, 1, 1, 2)", b"(3, 1, 1, 1, 1)", "NPY_SHAPE"),
        }
        for name, (old, new, error_code) in mutations.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    root = pathlib.Path(directory)
                    source = root / "source.npy"
                    write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
                    _replace_header_bytes(source, old, new)
                    with self.assertRaisesRegex(ValueError, error_code):
                        slice_weights(source, root / "local.npy", 0, 1)

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            data = bytearray(source.read_bytes())
            data[6] = 3
            source.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "NPY_UNSUPPORTED_VERSION"):
                slice_weights(source, root / "local.npy", 0, 1)

    def test_rejects_payload_truncation_and_trailing_bytes(self):
        for suffix, error_code in (("truncated", "NPY_PAYLOAD_TRUNCATED"),
                                   ("trailing", "NPY_PAYLOAD_TRAILING")):
            with self.subTest(suffix=suffix):
                with tempfile.TemporaryDirectory() as directory:
                    root = pathlib.Path(directory)
                    source = root / "source.npy"
                    write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
                    data = source.read_bytes()
                    source.write_bytes(data[:-1] if suffix == "truncated" else data + b"x")
                    with self.assertRaisesRegex(ValueError, error_code):
                        slice_weights(source, root / "local.npy", 0, 1)

    def test_refuses_existing_or_aliased_destinations_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "source.npy"
            target = root / "target.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            target.write_bytes(b"keep me")
            with self.assertRaisesRegex(ValueError, "DEST_EXISTS"):
                slice_weights(source, target, 0, 1)
            self.assertEqual(target.read_bytes(), b"keep me")
            with self.assertRaisesRegex(ValueError, "DEST_SOURCE_ALIAS"):
                slice_weights(source, source, 0, 1)
            symlink_target = root / "symlink.npy"
            symlink_target.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "DEST_SOURCE_ALIAS"):
                slice_weights(source, symlink_target, 0, 1)
            hardlink_target = root / "hardlink.npy"
            os.link(source, hardlink_target)
            with self.assertRaisesRegex(ValueError, "DEST_EXISTS"):
                slice_weights(source, hardlink_target, 0, 1)

            missing_parent_target = root / "missing" / "target.npy"
            with self.assertRaisesRegex(ValueError, "DEST_IO"):
                slice_weights(source, missing_parent_target, 0, 1)
            self.assertFalse(missing_parent_target.exists())

    def test_write_failure_removes_owned_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "local.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            with mock.patch.object(weights, "_write", side_effect=OSError("write")):
                with self.assertRaisesRegex(ValueError, "DEST_IO"):
                    slice_weights(source, target, 0, 1)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".weights-*.tmp")), [])

    def test_fsync_failure_removes_owned_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "local.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            with mock.patch.object(weights.os, "fsync", side_effect=OSError("fsync")):
                with self.assertRaisesRegex(ValueError, "DEST_IO"):
                    slice_weights(source, target, 0, 1)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".weights-*.tmp")), [])

    def test_source_close_failure_prevents_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "local.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            real_path_open = weights.Path.open

            def failing_source_open(path, mode="r", buffering=-1, encoding=None,
                                    errors=None, newline=None):
                wrapped = real_path_open(
                    path, mode, buffering=buffering, encoding=encoding,
                    errors=errors, newline=newline)
                return _FailingCloseReader(wrapped)

            with mock.patch.object(weights.Path, "open", new=failing_source_open):
                with self.assertRaisesRegex(ValueError, "NPY_SOURCE_IO"):
                    slice_weights(source, target, 0, 1)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".weights-*.tmp")), [])

    def test_flush_and_close_failures_remove_owned_temporary_file(self):
        class FailingOutput:
            def __init__(self, wrapped, failure):
                self.wrapped = wrapped
                self.failure = failure

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                if self.failure == "close":
                    self.wrapped.close()
                    raise OSError("close")
                self.wrapped.close()
                return False

            def write(self, data):
                return self.wrapped.write(data)

            def flush(self):
                if self.failure == "flush":
                    raise OSError("flush")
                return self.wrapped.flush()

            def fileno(self):
                return self.wrapped.fileno()

        for failure in ("flush", "close"):
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory() as directory:
                    root = pathlib.Path(directory)
                    source, target = root / "source.npy", root / "local.npy"
                    write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
                    real_fdopen = os.fdopen

                    def fdopen(descriptor, mode, selected=failure):
                        return FailingOutput(real_fdopen(descriptor, mode), selected)

                    with mock.patch.object(weights.os, "fdopen", side_effect=fdopen):
                        with self.assertRaisesRegex(ValueError, "DEST_IO"):
                            slice_weights(source, target, 0, 1)
                    self.assertFalse(target.exists())
                    self.assertEqual(list(root.glob(".weights-*.tmp")), [])

    def test_cleanup_failure_reports_owned_path_without_touching_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "local.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            with mock.patch.object(weights, "_write", side_effect=OSError("write")):
                with mock.patch.object(weights.os, "unlink", side_effect=OSError("cleanup")):
                    with self.assertRaisesRegex(ValueError, r"DEST_CLEANUP: .*\.weights-.*\.tmp"):
                        slice_weights(source, target, 0, 1)
            self.assertFalse(target.exists())

    def test_publish_race_preserves_nonowned_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "local.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")

            def link_race(temp_name, destination_name):
                pathlib.Path(destination_name).write_bytes(b"racing owner")
                raise FileExistsError(destination_name)

            with mock.patch.object(weights.os, "link", side_effect=link_race):
                with self.assertRaisesRegex(ValueError, "DEST_EXISTS"):
                    slice_weights(source, target, 0, 1)
            self.assertEqual(target.read_bytes(), b"racing owner")
            self.assertEqual(list(root.glob(".weights-*.tmp")), [])

    def test_post_publish_temp_cleanup_failure_rolls_back_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "local.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            real_unlink = os.unlink
            unlink_calls = [0]

            def fail_temp_then_unlink(path):
                unlink_calls[0] += 1
                if unlink_calls[0] == 1:
                    raise OSError("temp")
                return real_unlink(path)

            with mock.patch.object(weights.os, "unlink", side_effect=fail_temp_then_unlink):
                with self.assertRaisesRegex(ValueError, "DEST_CLEANUP.*rollback complete"):
                    slice_weights(source, target, 0, 1)
            self.assertFalse(target.exists())
            self.assertEqual(len(list(root.glob(".weights-*.tmp"))), 1)

    def test_post_publish_cleanup_and_rollback_failure_reports_published_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "source.npy", root / "local.npy"
            write_weight_fixture(source, (3, 1, 1, 1, 2), "|i1")
            with mock.patch.object(weights.os, "unlink", side_effect=OSError("unlink")):
                with self.assertRaisesRegex(ValueError, "DEST_CLEANUP.*final path published"):
                    slice_weights(source, target, 0, 1)
            self.assertTrue(target.exists())
            self.assertEqual(len(list(root.glob(".weights-*.tmp"))), 1)


def _write_custom_npy(path, payload, header_text, version=1, alignment=16):
    raw_header = header_text.encode("ascii")
    prefix_size = 8 + (2 if version == 1 else 4)
    raw_header += b" " * (-(prefix_size + len(raw_header) + 1) % alignment) + b"\n"
    prefix = b"\x93NUMPY" + bytes((version, 0))
    if version == 1:
        prefix += struct.pack("<H", len(raw_header))
    else:
        prefix += struct.pack("<I", len(raw_header))
    path.write_bytes(prefix + raw_header + payload)


class _GuardedReader:
    def __init__(self, wrapped, payload_offset, limit, calls):
        self.wrapped = wrapped
        self.payload_offset = payload_offset
        self.limit = limit
        self.calls = calls

    def read(self, size=-1):
        position = self.wrapped.tell()
        if position >= self.payload_offset:
            if size < 0 or size > self.limit:
                raise AssertionError("unbounded payload read")
            self.calls.append(size)
        return self.wrapped.read(size)

    def tell(self):
        return self.wrapped.tell()

    def fileno(self):
        return self.wrapped.fileno()

    def close(self):
        return self.wrapped.close()


class _GuardedWriter:
    def __init__(self, wrapped, limit, calls):
        self.wrapped = wrapped
        self.limit = limit
        self.calls = calls
        self.header_written = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.wrapped.__exit__(exc_type, exc_value, traceback)

    def write(self, data):
        if self.header_written and len(data) > self.limit:
            raise AssertionError("unbounded payload write")
        if self.header_written:
            self.calls.append(len(data))
        self.header_written = True
        return self.wrapped.write(data)

    def flush(self):
        return self.wrapped.flush()

    def fileno(self):
        return self.wrapped.fileno()


class _FailingCloseReader:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def read(self, size=-1):
        return self.wrapped.read(size)

    def tell(self):
        return self.wrapped.tell()

    def fileno(self):
        return self.wrapped.fileno()

    def close(self):
        self.wrapped.close()
        raise OSError("source close")


def _replace_header_bytes(path, old, new):
    data = bytearray(path.read_bytes())
    length_bytes = 2 if data[6] == 1 else 4
    header_length = int.from_bytes(data[8:8 + length_bytes], "little")
    header_start = 8 + length_bytes
    header = bytes(data[header_start:header_start + header_length])
    if old not in header:
        raise AssertionError("test mutation did not find expected header bytes")
    header = header.replace(old, new, 1)
    data[header_start:header_start + header_length] = header
    path.write_bytes(data)


if __name__ == "__main__":
    unittest.main()
