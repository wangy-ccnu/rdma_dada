"""Bounded, byte-preserving slicing for beamform weight NPY files."""

import ast
import hashlib
import os
import struct
import tempfile
from pathlib import Path


_MAGIC = b"\x93NUMPY"
_MAX_HEADER_BYTES = 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024


def _invalid(code, message):
    return ValueError("{}: {}".format(code, message))


def _read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        chunk = stream.read(size - len(result))
        if not chunk:
            return None
        result.extend(chunk)
    return bytes(result)


def _parse_header(header_bytes):
    if not header_bytes or header_bytes[-1:] != b"\n":
        raise _invalid("NPY_HEADER_FORMAT", "header must end with a newline")
    try:
        header_text = header_bytes.decode("ascii")
    except UnicodeDecodeError as error:
        raise _invalid("NPY_HEADER_FORMAT", "header is not ASCII") from error
    try:
        expression = ast.parse(header_text, mode="eval")
    except (MemoryError, RecursionError, SyntaxError, ValueError, TypeError) as error:
        raise _invalid("NPY_HEADER_MALFORMED", "header is not a literal dictionary") from error
    if not isinstance(expression.body, ast.Dict):
        raise _invalid("NPY_HEADER_STRUCTURE", "header must be a dictionary")
    seen_keys = set()
    for key in expression.body.keys:
        if isinstance(key, ast.Constant):
            key_value = key.value
        elif isinstance(key, ast.Str):
            key_value = key.s
        else:
            key_value = None
        if type(key_value) is not str:
            raise _invalid("NPY_HEADER_STRUCTURE", "dictionary keys must be strings")
        if key_value in seen_keys:
            raise _invalid("NPY_HEADER_DUPLICATE_KEY", "header contains duplicate key {!r}".format(key_value))
        seen_keys.add(key_value)
    try:
        value = ast.literal_eval(expression)
    except (MemoryError, RecursionError, SyntaxError, ValueError, TypeError) as error:
        raise _invalid("NPY_HEADER_MALFORMED", "header is not a literal dictionary") from error
    if type(value) is not dict:
        raise _invalid("NPY_HEADER_STRUCTURE", "header must be a dictionary")
    if set(value) != {"descr", "fortran_order", "shape"}:
        raise _invalid("NPY_HEADER_STRUCTURE", "header fields are not exactly descr/fortran_order/shape")

    dtype = value["descr"]
    if type(dtype) is not str or dtype not in ("|i1", "<i2"):
        raise _invalid("NPY_UNSUPPORTED_DTYPE", "dtype must be |i1 or <i2")
    fortran_order = value["fortran_order"]
    if type(fortran_order) is not bool:
        raise _invalid("NPY_HEADER_STRUCTURE", "fortran_order must be a boolean")
    if fortran_order:
        raise _invalid("NPY_FORTRAN_ORDER", "weights must be C-contiguous")

    shape = value["shape"]
    if type(shape) is not tuple or len(shape) != 5:
        raise _invalid("NPY_SHAPE", "shape must be a five-dimensional tuple")
    for dimension in shape:
        if type(dimension) is not int or dimension <= 0:
            raise _invalid("NPY_SHAPE", "dimensions must be positive integers")
    if shape[-1] != 2:
        raise _invalid("NPY_SHAPE", "last dimension must be 2")

    component_bytes = 1 if dtype == "|i1" else 2
    elements = 1
    for dimension in shape:
        elements *= dimension
    payload_bytes = elements * component_bytes
    return {
        "dtype": dtype,
        "shape": shape,
        "component_bytes": component_bytes,
        "payload_bytes": payload_bytes,
    }


def _read_source_header(source):
    try:
        stream = source.open("rb")
    except OSError as error:
        raise _invalid("NPY_SOURCE_IO", "cannot open source") from error

    try:
        file_size = os.fstat(stream.fileno()).st_size
        prefix = _read_exact(stream, 8)
        if prefix is None or prefix[:6] != _MAGIC:
            raise _invalid("NPY_INVALID_MAGIC", "source is not an NPY file")
        major, minor = prefix[6], prefix[7]
        if major not in (1, 2):
            raise _invalid("NPY_UNSUPPORTED_VERSION", "expected NPY version 1 or 2")
        length_bytes = 2 if major == 1 else 4
        encoded_length = _read_exact(stream, length_bytes)
        if encoded_length is None:
            raise _invalid("NPY_HEADER_TRUNCATED", "header length is truncated")
        header_bytes = int.from_bytes(encoded_length, "little")
        if header_bytes == 0 or header_bytes > _MAX_HEADER_BYTES:
            raise _invalid("NPY_HEADER_BOUNDS", "header length exceeds the 1 MiB bound")
        header = _read_exact(stream, header_bytes)
        if header is None:
            raise _invalid("NPY_HEADER_TRUNCATED", "dictionary header is truncated")
        data_offset = stream.tell()
        if data_offset % 16 != 0:
            raise _invalid("NPY_HEADER_FORMAT", "header/data boundary is not aligned")
        metadata = _parse_header(header)
        expected_end = data_offset + metadata["payload_bytes"]
        if file_size < expected_end:
            raise _invalid("NPY_PAYLOAD_TRUNCATED", "payload is shorter than shape requires")
        if file_size > expected_end:
            raise _invalid("NPY_PAYLOAD_TRAILING", "payload has trailing bytes")
        metadata.update({
            "major": major,
            "minor": minor,
            "length_bytes": length_bytes,
            "header_bytes": header,
            "data_offset": data_offset,
            "file_size": file_size,
            "prefix": prefix,
        })
        return stream, metadata
    except ValueError:
        try:
            stream.close()
        except OSError:
            pass
        raise
    except OSError as error:
        try:
            stream.close()
        except OSError:
            pass
        raise _invalid("NPY_HEADER_IO", "cannot read source header") from error
    except Exception:
        try:
            stream.close()
        except OSError:
            pass
        raise


def _build_header(dtype, shape, major, minor):
    text = "{'descr': %r, 'fortran_order': False, 'shape': %r, }" % (
        dtype, tuple(shape))
    raw = text.encode("ascii")
    prefix_size = 8 + (2 if major == 1 else 4)
    alignment = 16 if major == 1 else 64
    padding = (- (prefix_size + len(raw) + 1)) % alignment
    raw += b" " * padding + b"\n"
    if major == 1:
        if len(raw) > 0xFFFF:
            raise _invalid("NPY_HEADER_BOUNDS", "output v1 header is too large")
        length = struct.pack("<H", len(raw))
    else:
        length = struct.pack("<I", len(raw))
    return _MAGIC + bytes((major, minor)) + length + raw


def _write(output, data, digest):
    written = output.write(data)
    if written != len(data):
        raise OSError("short write")
    digest.update(data)


def _validate_slice(first_f, nchan):
    if type(first_f) is not int or first_f < 0:
        raise _invalid("SLICE_INDEX", "first_f must be a non-negative integer")
    if type(nchan) is not int or nchan <= 0:
        raise _invalid("SLICE_INDEX", "nchan must be a positive integer")


def _owned_temp_stat(temp_path, expected_stat):
    try:
        current_stat = os.stat(os.fspath(temp_path), follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _invalid(
            "DEST_CLEANUP",
            "cannot inspect owned temporary path {}".format(temp_path),
        ) from error
    if (current_stat.st_dev, current_stat.st_ino) != (
            expected_stat.st_dev, expected_stat.st_ino):
        raise _invalid(
            "DEST_CLEANUP",
            "owned temporary path was replaced: {}".format(temp_path),
        )
    return current_stat


def _remove_owned_temp(temp_path, expected_stat):
    if temp_path is None:
        return
    if expected_stat is None:
        raise _invalid(
            "DEST_CLEANUP",
            "cannot verify ownership of temporary path {}".format(temp_path),
        )
    if _owned_temp_stat(temp_path, expected_stat) is None:
        return
    try:
        os.unlink(os.fspath(temp_path))
    except FileNotFoundError:
        return
    except OSError as error:
        raise _invalid(
            "DEST_CLEANUP",
            "cannot remove owned temporary path {}".format(temp_path),
        ) from error


def _raise_with_cleanup(error, temp_path, expected_stat):
    if temp_path is not None:
        try:
            _remove_owned_temp(temp_path, expected_stat)
        except ValueError as cleanup_error:
            raise cleanup_error from error
    raise error


def _rollback_published_destination(destination, expected_stat):
    try:
        destination_stat = os.stat(os.fspath(destination), follow_symlinks=False)
    except FileNotFoundError:
        return True, "destination is already absent"
    except OSError as error:
        return False, "cannot inspect final path {}: {}".format(destination, error)
    if (destination_stat.st_dev, destination_stat.st_ino) != (
            expected_stat.st_dev, expected_stat.st_ino):
        return False, "final path was replaced: {}".format(destination)
    try:
        os.unlink(os.fspath(destination))
    except OSError as error:
        return False, "cannot rollback final path {}: {}".format(destination, error)
    return True, "destination rollback complete"


def slice_weights(source: Path, destination: Path, first_f: int, nchan: int) -> dict:
    """Copy a contiguous F-axis slice while preserving raw signed bytes."""
    source = Path(source)
    destination = Path(destination)
    _validate_slice(first_f, nchan)

    source_real = os.path.normcase(os.path.realpath(os.fspath(source)))
    destination_real = os.path.normcase(os.path.realpath(os.fspath(destination)))
    if source_real == destination_real:
        raise _invalid("DEST_SOURCE_ALIAS", "destination aliases source")
    if os.path.lexists(os.fspath(destination)):
        raise _invalid("DEST_EXISTS", "destination already exists")

    stream, metadata = _read_source_header(source)
    temp_path = None
    temp_stat = None
    source_closed = False
    cleanup_handled = False
    try:
        shape = metadata["shape"]
        if first_f >= shape[0] or nchan > shape[0] - first_f:
            raise _invalid("SLICE_RANGE", "F-axis slice is out of bounds")
        inner_elements = shape[1] * shape[2] * shape[3] * shape[4]
        bytes_per_f = inner_elements * metadata["component_bytes"]
        output_shape = (nchan,) + shape[1:]
        output_header = _build_header(
            metadata["dtype"], output_shape, metadata["major"], metadata["minor"])

        source_digest = hashlib.sha256()
        source_digest.update(metadata["prefix"])
        source_digest.update(struct.pack(
            "<H" if metadata["length_bytes"] == 2 else "<I",
            len(metadata["header_bytes"])))
        source_digest.update(metadata["header_bytes"])

        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=".weights-", suffix=".tmp", dir=os.fspath(destination.parent))
            temp_path = Path(temp_name)
        except OSError as error:
            raise _invalid("DEST_IO", "cannot create temporary destination") from error
        try:
            temp_stat = os.fstat(descriptor)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise _invalid("DEST_IO", "cannot inspect temporary destination") from error
        local_digest = hashlib.sha256()
        try:
            try:
                output = os.fdopen(descriptor, "wb")
            except OSError as error:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise _invalid("DEST_IO", "cannot open temporary destination") from error
            with output:
                _write(output, output_header, local_digest)
                for channel in range(shape[0]):
                    selected = first_f <= channel < first_f + nchan
                    remaining = bytes_per_f
                    while remaining:
                        try:
                            chunk = stream.read(min(remaining, _COPY_CHUNK_BYTES))
                        except OSError as error:
                            raise _invalid("NPY_PAYLOAD_IO", "cannot read source payload") from error
                        if not chunk:
                            raise _invalid("NPY_PAYLOAD_TRUNCATED", "payload ended during copy")
                        source_digest.update(chunk)
                        if selected:
                            _write(output, chunk, local_digest)
                        remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
        except ValueError:
            raise
        except OSError as error:
            raise _invalid("DEST_IO", "destination write failed") from error

        try:
            source_position = stream.tell()
            source_size = os.fstat(stream.fileno()).st_size
        except OSError as error:
            raise _invalid("NPY_PAYLOAD_IO", "cannot verify source payload") from error
        if source_position != metadata["file_size"]:
            raise _invalid("NPY_SOURCE_CHANGED", "source size changed during copy")
        if source_size != metadata["file_size"]:
            raise _invalid("NPY_SOURCE_CHANGED", "source size changed during copy")
        try:
            stream.close()
        except OSError as error:
            source_closed = True
            raise _invalid("NPY_SOURCE_IO", "cannot close source before publish") from error
        source_closed = True
        expected_local_size = len(output_header) + nchan * bytes_per_f
        if os.stat(os.fspath(temp_path), follow_symlinks=False).st_size != expected_local_size:
            raise _invalid("DEST_IO", "destination size is incomplete")

        try:
            os.link(os.fspath(temp_path), os.fspath(destination))
        except FileExistsError as error:
            raise _invalid("DEST_EXISTS", "destination appeared during publish") from error
        except OSError as error:
            raise _invalid("DEST_PUBLISH", "cannot publish destination") from error
        try:
            _remove_owned_temp(temp_path, temp_stat)
        except ValueError as cleanup_error:
            cleanup_handled = True
            rollback_succeeded, rollback_message = _rollback_published_destination(
                destination, temp_stat)
            if rollback_succeeded:
                raise _invalid(
                    "DEST_CLEANUP",
                    "{}; {}; owned temporary path {}".format(
                        cleanup_error, rollback_message, temp_path),
                ) from cleanup_error
            raise _invalid(
                "DEST_CLEANUP",
                "{}; {}; final path published at {}; owned temporary path {}".format(
                    cleanup_error, rollback_message, destination, temp_path),
            ) from cleanup_error
        temp_path = None
        return {
            "shape": list(output_shape),
            "source_shape": list(shape),
            "dtype": metadata["dtype"],
            "source_sha256": source_digest.hexdigest(),
            "local_sha256": local_digest.hexdigest(),
            "first_f": first_f,
            "nchan": nchan,
        }
    except ValueError as error:
        if cleanup_handled:
            raise
        _raise_with_cleanup(error, temp_path, temp_stat)
    except OSError as error:
        _raise_with_cleanup(_invalid("DEST_IO", "weight slicing failed"), temp_path, temp_stat)
    except OverflowError as error:
        _raise_with_cleanup(_invalid("WEIGHTS_IO", "weight slicing failed"), temp_path, temp_stat)
    finally:
        if not source_closed:
            try:
                stream.close()
            except OSError:
                pass
