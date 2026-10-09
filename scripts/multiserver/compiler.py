"""Resolve installed instances through the explicit existing C++ compiler."""

import os
from pathlib import Path
import re
import subprocess
import tempfile

from .bundle import (_canonical, _parse_json, _publish_exclusive, _read_json,
                     _resource, _safe_path, _sha_file)


class ResolutionError(ValueError):
    """Stable failure code plus retained compiler invocation evidence."""

    def __init__(self, code, message, details):
        super().__init__(code + ": " + message)
        self.details = details


_CHECKS = {"observation_schema", "wire_contract", "checked_geometry",
           "identity_verification", "header_transform"}
_ARTIFACTS = {"resolved_observation.json", "ring_plan.json", "validation_report.json",
              "raw.header", "unpacked.header", "converted.header", "beamformed.header",
              "output.header"}


def _report(raw):
    try:
        report = _parse_json(raw, "compiler report")
        if not isinstance(report, dict) or report.get("valid") is not True:
            raise ValueError("valid must be true")
        if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
            raise ValueError("unsupported report schema")
        for name in ("config_id", "geometry_id"):
            if not isinstance(report.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", report[name]):
                raise ValueError("missing/invalid " + name)
        checks = report.get("checks")
        if not isinstance(checks, list) or len(checks) != len(_CHECKS):
            raise ValueError("missing validation checks")
        if any(not isinstance(c, dict) or c.get("status") != "PASS" for c in checks):
            raise ValueError("validation check failed")
        if {c.get("name") for c in checks} != _CHECKS:
            raise ValueError("validation checks incomplete")
        return report
    except (ValueError, TypeError) as error:
        raise ValueError("COMPILER_REPORT: " + str(error)) from error


def _artifacts(directory, report):
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("COMPILER_ARTIFACT: compiler did not produce an artifact directory")
    names = {p.name for p in directory.iterdir()}
    if names != _ARTIFACTS | {"MANIFEST.sha256"}:
        raise ValueError("COMPILER_ARTIFACT: missing or unexpected artifacts")
    for name in names:
        if (directory / name).is_symlink() or not (directory / name).is_file():
            raise ValueError("COMPILER_ARTIFACT: artifact is not a regular file: " + name)
    hashes = {name: _sha_file(directory / name) for name in names}
    manifest = {}
    try:
        for line in (directory / "MANIFEST.sha256").read_text(encoding="ascii").splitlines():
            digest, name = line.split("  ", 1)
            if name not in _ARTIFACTS or name in manifest or digest != hashes[name]:
                raise ValueError("manifest entry invalid")
            manifest[name] = digest
        if set(manifest) != _ARTIFACTS:
            raise ValueError("manifest incomplete")
        if _report((directory / "validation_report.json").read_bytes()) != report:
            raise ValueError("stdout and artifact reports differ")
        for name in ("resolved_observation.json", "ring_plan.json"):
            document = _read_json(directory / name)
            if not isinstance(document, dict) or any(document.get(k) != report[k] for k in ("config_id", "geometry_id")):
                raise ValueError("artifact identity differs: " + name)
        for name in _ARTIFACTS:
            if name.endswith(".header") and (directory / name).stat().st_size != 4096:
                raise ValueError("invalid header size: " + name)
    except (ValueError, UnicodeError) as error:
        raise ValueError("COMPILER_ARTIFACT: " + str(error)) from error
    return hashes


def _owned(workspace, identity):
    try:
        _safe_path(workspace)
        stat = workspace.lstat()
        if not workspace.is_dir() or (stat.st_dev, stat.st_ino) != identity:
            raise ValueError("workspace identity changed")
    except (ValueError, OSError) as error:
        raise ValueError("STAGING_OWNERSHIP: diagnostic workspace replaced; writes refused: " + str(workspace)) from error


def resolve_instance(instance_directory: Path, compiler: Path, output: Path) -> dict:
    """Validate compiler artifacts and publish without changing portable inputs.

    Before-publication failures retain diagnostics and raise ValueError.
    Published artifacts survive cleanup errors, reported as persistent warnings
    or a structured error explicitly identifying publication success.
    Runtime resource readiness is outside compiler validation.
    """
    instance = _safe_path(instance_directory)
    output = _safe_path(output)
    compiler = Path(compiler).resolve()
    if not compiler.is_file() or not os.access(str(compiler), os.X_OK):
        raise ValueError("COMPILER_BINARY: explicit binary must be an executable regular file: " + str(compiler))
    if output.exists():
        raise ValueError("DEST_EXISTS: output must be a new directory: " + str(output))
    if not output.parent.is_dir():
        raise ValueError("DEST_PARENT: output parent must exist: " + str(output.parent))
    bundle = next((p for p in instance.parents if (p / "bundle.json").is_file()), instance)
    if output == instance or instance in output.parents or output in instance.parents or output == bundle or bundle in output.parents:
        raise ValueError("OUTPUT_OVERLAP: output must not overlap the installed instance")
    observation = instance / "observation.json"
    document = _read_json(observation)
    try:
        resources = [observation, _resource(observation, document["wire"]["profile"])]
        resources += [_resource(observation, m["weights_file"]) for m in document["processing"]["modules"] if m.get("type") == "beamform"]
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("INSTANCE_INPUT: missing instance resource references") from error
    try:
        inputs = {str(path): _sha_file(path) for path in resources}
        binary_hash = _sha_file(compiler)
        workspace = Path(tempfile.mkdtemp(prefix=".multiserver-resolve-", dir=str(output.parent)))
        stat = workspace.stat()
    except OSError as error:
        raise ValueError("RESOLVE_IO: " + str(error)) from error
    workspace_identity = (stat.st_dev, stat.st_ino)
    artifacts = workspace / "artifacts"
    argv = [str(compiler), "--config", str(observation), "--output-dir", str(artifacts)]
    evidence = {"ok": False, "published": False, "runtime_ready": False, "argv": argv,
                "returncode": None, "stdout": "", "stderr": "",
                "compiler": {"path": str(compiler), "sha256": binary_hash},
                "instance_directory": str(instance), "output": str(output),
                "inputs": inputs, "evidence_directory": str(workspace)}
    try:
        try:
            completed = subprocess.run(argv, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, check=False)
        except (OSError, UnicodeError) as error:
            evidence["stderr"] = str(error)
            raise ValueError("COMPILER_LAUNCH: " + str(error)) from error
        evidence.update(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
        if completed.returncode != 0:
            raise ValueError("COMPILER_EXIT: existing compiler returned " + str(completed.returncode))
        report = _report(completed.stdout)
        _owned(workspace, workspace_identity)
        hashes = _artifacts(artifacts, report)
        artifact_stat = artifacts.stat()
        artifact_identity = (artifact_stat.st_dev, artifact_stat.st_ino)
        if inputs != {str(path): _sha_file(_safe_path(path)) for path in resources} or _sha_file(compiler) != binary_hash:
            raise ValueError("COMPILER_INPUT_CHANGED: compiler or input/resource changed during resolution")
        if report.get("inputs") != {"observation": str(observation), "wire_profile": str(resources[1])} or report.get("observation_id") != document["observation"]["observation_id"]:
            raise ValueError("COMPILER_REPORT: report refers to a different installed instance")
        evidence.update(ok=True, published=True, report=report, artifacts=hashes, evidence_directory=str(output))
        # Preserve the original compiler manifest and bytes; adapter evidence is separate.
        with (artifacts / "resolution.json").open("xb") as stream:
            stream.write(_canonical(evidence))
        _safe_path(output)
        _publish_exclusive(artifacts, output)
    except (ValueError, OSError) as error:
        code = str(error).split(":", 1)[0] if isinstance(error, ValueError) else "RESOLVE_IO"
        evidence.update(ok=False, published=False, evidence_directory=str(workspace), error={"code": code, "message": str(error)})
        try:
            _owned(workspace, workspace_identity)
            with (workspace / "resolution.json").open("xb") as stream:
                stream.write(_canonical(evidence))
        except (ValueError, OSError) as diagnostic_error:
            evidence["diagnostic_error"] = str(diagnostic_error)
            code = "STAGING_OWNERSHIP" if isinstance(diagnostic_error, ValueError) else "RESOLVE_DIAGNOSTIC_IO"
        raise ResolutionError(code, str(error), evidence) from error
    # Publication is complete. Cleanup cannot undo it or become a compiler failure.
    try:
        _owned(workspace, workspace_identity)
        workspace.rmdir()
    except (ValueError, OSError) as error:
        evidence["cleanup_warning"] = {"code": "RESOLVE_CLEANUP", "message": str(error),
                                       "directory": str(workspace)}
        try:
            _owned(output, artifact_identity)
            _safe_path(output / "resolution.json")
            warning_record = output / ".resolution-cleanup.json"
            with warning_record.open("xb") as stream:
                stream.write(_canonical(evidence))
            os.replace(str(warning_record), str(output / "resolution.json"))
        except (ValueError, OSError) as diagnostic_error:
            evidence["diagnostic_error"] = str(diagnostic_error)
            raise ResolutionError("RESOLVE_CLEANUP_EVIDENCE",
                                  "artifacts published at {}; cannot persist cleanup warning: {}".format(output, diagnostic_error),
                                  evidence) from diagnostic_error
    return evidence
