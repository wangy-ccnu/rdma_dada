"""Generate deterministic, portable Observation v1 instance bundles locally."""

import copy
import ctypes
import errno
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile

from .deployment import validate_deployment
from .model import validate_global
from .weights import slice_weights


GENERATOR_VERSION = "multiserver-bundle-v1"
_ROOT = Path(__file__).resolve().parents[2]
_WIRE_CONTRACT = "config/packet_formats/frontend.example-v1.json"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _fail(code, field, detail):
    raise ValueError("{}: {}: {}".format(code, field, detail))


def _canonical(document):
    try:
        return (json.dumps(document, sort_keys=True, ensure_ascii=True,
                           allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    except (ValueError, TypeError) as error:
        raise ValueError("JSON_NONFINITE: document is not finite JSON") from error


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON_DUPLICATE_KEY", key, "duplicate JSON key")
        result[key] = value
    return result


def _parse_json(raw, field):
    def invalid_constant(value):
        _fail("JSON_NONFINITE", field, "nonfinite JSON constant " + value)
    try:
        document = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("JSON_INVALID: {}: invalid UTF-8 JSON".format(field)) from error
    _canonical(document)
    return document


def _safe_path(path):
    path = Path(path)
    if ".." in path.parts:
        _fail("PATH_TRAVERSAL", str(path), "traversal is not allowed")
    path = path.absolute()
    for candidate in list(reversed(path.parents)) + [path]:
        if candidate.is_symlink():
            _fail("SYMLINK", str(candidate), "symlink paths are not allowed")
    return path


def _read_json(path):
    path = _safe_path(path)
    try:
        return _parse_json(path.read_bytes(), str(path))
    except OSError as error:
        raise ValueError("JSON_IO: {}: cannot read input".format(path)) from error


def _resource(config_path, value):
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "\\" in value or not relative.parts:
        _fail("RESOURCE_PATH", value, "resource must be relative without traversal")
    path = _safe_path(config_path.parent / value)
    if not path.is_file():
        _fail("RESOURCE_IO", value, "resource is not a readable regular file")
    return path


def _sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _generator_identity():
    # Include the validators, slicer, CLI and contracts that affect generation.
    names = [
        "scripts/multiserver/__init__.py", "scripts/multiserver/model.py",
        "scripts/multiserver/deployment.py", "scripts/multiserver/weights.py",
        "scripts/multiserver/bundle.py", "scripts/multiserver_compile.py",
        "config/observation-v1.schema.json", _WIRE_CONTRACT,
    ]
    sources = {name: _sha_file(_ROOT / name) for name in names}
    return {"version": GENERATOR_VERSION, "sources": sources,
            "source_sha256": hashlib.sha256(_canonical(sources)).hexdigest()}


def _check_mapping(profile, field):
    if profile["ring_counts"]["output"] != profile["ring_counts"]["compute"]:
        _fail("OUTPUT_RING_COUNT_UNSUPPORTED", field + ".ring_counts.output",
              "Observation v1 uses compute_ring_blocks for output; counts must agree")
    match = profile["flow"]["match"]
    for source_field in ("source_ip", "source_port"):
        if match.get(source_field) not in (None, "*"):
            _fail("SOURCE_FILTER_UNSUPPORTED", field + ".flow.match." + source_field,
                  "current receiver supports destination-only matching")
    destination = match.get("destination_ip")
    port = match.get("destination_port")
    try:
        ipaddress.IPv4Address(destination)
    except (ValueError, TypeError, ipaddress.AddressValueError):
        _fail("DESTINATION_REQUIRED", field + ".flow.match.destination_ip", "concrete IPv4 required")
    if not isinstance(destination, str) or type(port) is not int or not 1 <= port <= 65535:
        _fail("DESTINATION_REQUIRED", field + ".flow.match", "concrete IPv4 and UDP port required")
    if match.get("protocol", "UDP") not in (None, "*", "UDP", "udp"):
        _fail("PROTOCOL_UNSUPPORTED", field + ".flow.match.protocol", "current receiver requires UDP")
    if profile["consumer"]["type"] != "DADA_DBNULL":
        _fail("CONSUMER_UNSUPPORTED", field + ".consumer.type", "disabled-storage bundle requires DADA_DBNULL")


def _observation(global_doc, assignment, profile):
    observation = copy.deepcopy(global_doc["observation"])
    observation.update({"first_channel_id": assignment["first_channel_id"], "nchan": assignment["nchan"]})
    channels = global_doc["channels"]
    first = channels["first_center_frequency_hz"] + (assignment["first_channel_id"] - channels["first_channel_id"]) * channels["channel_spacing_hz"]
    center = (2 * first + (assignment["nchan"] - 1) * channels["channel_spacing_hz"]) // 2
    weights = global_doc["processing"]["weights"]
    beamform = {
        "type": "beamform", "weights_file": "weights.npy",
        "weights_order": weights["order"], "weights_id": weights["weights_id"],
        "weights_scale": weights["weights_scale"], "compute_mode": weights["compute_mode"],
    }
    product = "power" if global_doc["processing"]["product"] == "POWER" else "stokes"
    integrate = dict(global_doc["processing"]["integration"], type="integrate")
    receiver = dict(profile["receiver"], destination_ip=profile["flow"]["match"]["destination_ip"],
                    destination_port=profile["flow"]["match"]["destination_port"])
    return {
        "schema_version": 1, "observation": observation,
        "metadata": {"telescope": global_doc["metadata"]["telescope"], "center_frequency_hz": center,
                     "bandwidth_hz": assignment["nchan"] * channels["channel_spacing_hz"]},
        "wire": {"profile": "wire-profile.json", "samples_per_packet": global_doc["wire"]["samples_per_packet"]},
        "blocks": {
            "groups_per_block": global_doc["time_grid"]["samples_per_block"] // global_doc["wire"]["samples_per_packet"],
            "raw_ring_blocks": profile["ring_counts"]["raw"],
            "compute_ring_blocks": profile["ring_counts"]["compute"],
            "window_blocks": profile["execution"]["unpack_window_blocks"],
        },
        "rings": copy.deepcopy(profile["rings"]), "storage": copy.deepcopy(profile["storage"]),
        "receiver": receiver,
        "processing": {
            "backend": "CUDA", "cuda_device": profile["gpu_id"], "run_once": True,
            "cuda_pipeline": {"mode": profile["execution"]["cuda_mode"], "inflight_blocks": profile["execution"]["cuda_slots"]},
            "conversion": {"scale": global_doc["processing"]["conversion_scale"]},
            "output": {"sample_format": "AUTO"}, "modules": [beamform, {"type": product}, integrate],
        },
    }


def _publish_exclusive(source, destination):
    """Atomic directory publication with no replacement on macOS/Linux."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = [os.fsencode(source), os.fsencode(destination), 4]  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        arguments = [-100, os.fsencode(source), -100, os.fsencode(destination), 1]  # RENAME_NOREPLACE
    else:
        _fail("PUBLISH_UNSUPPORTED", str(destination), "atomic no-replace rename unavailable")
    function.restype = ctypes.c_int
    if function(*arguments) != 0:
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            _fail("DEST_EXISTS", str(destination), "destination appeared during publication")
        _fail("PUBLISH_IO", str(destination), os.strerror(code))


def _output_check(output):
    output = _safe_path(output)
    if not output.parent.is_dir():
        _fail("DEST_PARENT", str(output.parent), "output parent must already exist")
    identity = None
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            _fail("DEST_EXISTS", str(output), "destination is not an empty directory")
        stat = output.stat()
        identity = (stat.st_dev, stat.st_ino)
    return output, identity


def build_bundle(global_path: Path, deployment_path: Path, output: Path, *, inventory_path: Path) -> dict:
    """Build all instances in staging, then publish one immutable snapshot.

    Returned static validation is development evidence, not compiler/runtime acceptance.
    """
    stage = None
    stage_identity = None
    try:
        global_path = _safe_path(global_path)
        global_doc = validate_global(_read_json(global_path))
        deployments = _read_json(deployment_path)
        inventory = _read_json(inventory_path)
        checked = validate_deployment(global_doc, deployments, inventory)
        output, empty_identity = _output_check(output)
        instances = []
        paths = set()
        for assignment, profile in zip(global_doc["assignments"], checked["instances"]):
            server = assignment["server_id"]
            if not _IDENTIFIER.fullmatch(server):
                _fail("UNSAFE_IDENTIFIER", server, "server_id must be a safe single path component")
            relative = "instances/{}/gpu-{}".format(server, assignment["gpu_id"])
            if relative in paths:
                _fail("DUPLICATE_INSTANCE_PATH", relative, "instance path already assigned")
            paths.add(relative)
            _check_mapping(profile, assignment["deployment_profile"])
            instances.append((assignment, profile, relative))
        weight_path = _resource(global_path, global_doc["processing"]["weights"]["path"])
        wire_path = _resource(global_path, global_doc["wire"]["profile"])
        wire_bytes = wire_path.read_bytes()
        wire_doc = _parse_json(wire_bytes, str(wire_path))
        if _canonical(wire_doc) != _canonical(_read_json(_ROOT / _WIRE_CONTRACT)):
            _fail("WIRE_UNSUPPORTED", str(wire_path), "requires the supported Project VDIF v1 contract")
        wire_hash = hashlib.sha256(wire_bytes).hexdigest()
        stage = Path(tempfile.mkdtemp(prefix=".multiserver-", dir=str(output.parent)))
        stat = stage.stat()
        stage_identity = (stat.st_dev, stat.st_ino)
        result_instances = []
        expected_prefix = [global_doc["channels"]["nchan"], global_doc["observation"]["npol"], len(global_doc["observation"]["station_ids"])]
        source_metadata = None
        for assignment, profile, relative in instances:
            directory = stage / relative
            directory.mkdir(parents=True)
            offset = assignment["first_channel_id"] - global_doc["channels"]["first_channel_id"]
            evidence = slice_weights(weight_path, directory / "weights.npy", offset, assignment["nchan"])
            shape = evidence["source_shape"]
            if shape[:3] != expected_prefix or shape[3] <= 0 or shape[4] != 2:
                _fail("WEIGHTS_GLOBAL_SHAPE", str(weight_path), "full F/P/A must match global selection and B must be positive")
            current_source = {"source_shape": shape, "source_sha256": evidence["source_sha256"], "dtype": evidence["dtype"]}
            if source_metadata is not None and current_source != source_metadata:
                _fail("RESOURCE_CHANGED", str(weight_path), "source changed between instance slices")
            source_metadata = current_source
            observation = _observation(global_doc, assignment, profile)
            (directory / "observation.json").write_bytes(_canonical(observation))
            (directory / "wire-profile.json").write_bytes(wire_bytes)
            result_instances.append({
                "server_id": assignment["server_id"], "gpu_id": assignment["gpu_id"],
                "deployment_profile": assignment["deployment_profile"], "path": relative,
                "assignment": copy.deepcopy(assignment), "weights": evidence,
                "deployment": copy.deepcopy(profile),
                "observation_sha256": _sha_file(directory / "observation.json"),
                "wire_sha256": wire_hash,
            })
        if _sha_file(weight_path) != source_metadata["source_sha256"] or _sha_file(wire_path) != wire_hash:
            _fail("RESOURCE_CHANGED", "resources", "resource changed during generation")
        inputs = {"global-observation.json": global_doc, "deployments.json": deployments, "inventory.json": inventory}
        for name, document in inputs.items():
            (stage / name).write_bytes(_canonical(document))
        channels = global_doc["channels"]
        center_numerator = 2 * channels["first_center_frequency_hz"] + (channels["nchan"] - 1) * channels["channel_spacing_hz"]
        result = {
            "schema_version": 1, "generator": _generator_identity(),
            "target_compiler_contract": {"observation_schema": "observation-v1", "wire_format": "project-vdif-v1", "resolved": False},
            "inputs": {name: hashlib.sha256(_canonical(document)).hexdigest() for name, document in inputs.items()},
            "observation_id": global_doc["observation"]["observation_id"],
            "time_grid": copy.deepcopy(global_doc["time_grid"]),
            "frequency": {"full_band_center_hz": center_numerator // 2 if center_numerator % 2 == 0 else "{}.5".format(center_numerator // 2)},
            "weights": source_metadata, "wire_sha256": wire_hash,
            "monitoring": copy.deepcopy(global_doc["monitoring"]),
            "instances": result_instances, "status": copy.deepcopy(checked["status"]),
        }
        result["bundle_id"] = hashlib.sha256(_canonical(result)).hexdigest()
        (stage / "bundle.json").write_bytes(_canonical(result))
        files = sorted(path for path in stage.rglob("*") if path.is_file())
        manifest = "".join("{}  {}\n".format(_sha_file(path), path.relative_to(stage).as_posix()) for path in files)
        (stage / "MANIFEST.sha256").write_text(manifest, encoding="ascii")
        for line in manifest.splitlines():
            digest, relative = line.split("  ", 1)
            if _sha_file(stage / relative) != digest:
                _fail("MANIFEST_MISMATCH", relative, "staged content changed")
        for instance in result_instances:
            directory = stage / instance["path"]
            if _sha_file(directory / "weights.npy") != instance["weights"]["local_sha256"]:
                _fail("SLICE_HASH_MISMATCH", instance["path"], "staged weights differ from slice evidence")
            if (_sha_file(directory / "observation.json") != instance["observation_sha256"]
                    or _sha_file(directory / "wire-profile.json") != wire_hash):
                _fail("MANIFEST_MISMATCH", instance["path"], "staged config/resource differs from generation evidence")
        if empty_identity is not None:
            _safe_path(output)
            stat = output.stat()
            if (stat.st_dev, stat.st_ino) != empty_identity:
                _fail("DEST_EXISTS", str(output), "empty output directory was replaced")
            try:
                output.rmdir()
            except OSError as error:
                raise ValueError("DEST_EXISTS: destination changed before publication") from error
        _publish_exclusive(stage, output)
        stage = None
        return result
    except OSError as error:
        raise ValueError("BUNDLE_IO: {}".format(error)) from error
    finally:
        if stage is not None:
            try:
                stat = stage.lstat()
                if stage.is_symlink() or (stat.st_dev, stat.st_ino) != stage_identity:
                    _fail("STAGING_OWNERSHIP", str(stage), "staging replaced; cleanup refused")
                shutil.rmtree(stage)
            except OSError as error:
                raise ValueError("BUNDLE_CLEANUP: {}: cannot remove owned staging".format(stage)) from error
