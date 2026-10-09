"""Pure validation for the global multi-server observation input.

The global document deliberately contains only user-owned science, time,
frequency and assignment data.  Deployment resource claims and weight file
contents are validated by later phases.
"""

import copy
import datetime as _datetime
import re
from pathlib import PurePosixPath


_UINT16_MAX = 65535
_UINT32_MAX = (1 << 32) - 1
_UINT64_MAX = (1 << 64) - 1
_INT64_MAX = (1 << 63) - 1
_DURATION_RE = re.compile(
    r"^(?:[1-9][0-9]*(?:\.[0-9]{1,12})?|0\.(?=[0-9]{0,11}[1-9])[0-9]{1,12})$"
)
_DECIMAL_RE = re.compile(r"^(?:[1-9][0-9]*(?:\.[0-9]+)?|0\.(?=[0-9]*[1-9])[0-9]+)$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}:[0-9]{2}:[0-9]{2}$")


def _error(code, path, detail):
    raise ValueError("{}: {}: {}".format(code, path, detail))


def _object(value, path):
    if not isinstance(value, dict):
        _error("INVALID_TYPE", path, "expected object")
    return value


def _exact_keys(value, required, path):
    _object(value, path)
    actual = set(value)
    missing = sorted(set(required) - actual)
    if missing:
        _error("MISSING_FIELD", path + "." + missing[0], "required field")
    unknown = sorted((key for key in value if key not in required), key=lambda item: str(item))
    if unknown:
        _error("UNKNOWN_FIELD", path + "." + str(unknown[0]), "unknown field")


def _integer(value, path, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        _error("INVALID_INTEGER", path, "expected integer")
    if minimum is not None and value < minimum:
        _error("INTEGER_RANGE", path, "must be at least {}".format(minimum))
    if maximum is not None and value > maximum:
        _error("INTEGER_RANGE", path, "must be at most {}".format(maximum))
    return value


def _string(value, path, code="INVALID_TYPE"):
    if not isinstance(value, str) or not value:
        _error(code, path, "expected non-empty string")
    return value


def _identifier(value, path):
    return _string(value, path, "INVALID_IDENTIFIER")


def _positive_decimal(value, path, code):
    if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value):
        _error(code, path, "expected a finite positive decimal string")
    return value


def _duration_ps(value, path):
    if not isinstance(value, str) or not _DURATION_RE.fullmatch(value):
        _error("INVALID_DURATION", path, "expected positive decimal seconds with at most 12 fractional digits")
    if "." in value:
        whole, fraction = value.split(".")
    else:
        whole, fraction = value, ""
    fraction = (fraction + ("0" * 12))[:12]
    whole_ps = int(whole) * 1_000_000_000_000
    fraction_ps = int(fraction or "0")
    if whole_ps > _UINT64_MAX - fraction_ps:
        _error("DURATION_RANGE", path, "exceeds uint64 picoseconds")
    result = whole_ps + fraction_ps
    if result == 0:
        _error("INVALID_DURATION", path, "must be positive")
    return result


def _checked_time_product(sample_count, sample_interval_ps, path):
    if sample_interval_ps > _UINT64_MAX // sample_count:
        _error("TIME_GRID_RANGE", path, "sample count times sample interval overflows uint64")
    return sample_count * sample_interval_ps


def _validate_utc(value, path):
    _string(value, path)
    if not _UTC_RE.fullmatch(value):
        _error("INVALID_UTC_START", path, "expected YYYY-MM-DD-HH:MM:SS")
    try:
        _datetime.datetime.strptime(value, "%Y-%m-%d-%H:%M:%S")
    except ValueError:
        _error("INVALID_UTC_START", path, "invalid calendar date or time")


def _validate_weights(value):
    path = "processing.weights"
    _exact_keys(value, ["path", "order", "weights_id", "weights_scale", "compute_mode"], path)
    weight_path = _string(value["path"], path + ".path", "INVALID_WEIGHTS")
    if "\\" in weight_path or PurePosixPath(weight_path).is_absolute() or ".." in PurePosixPath(weight_path).parts:
        _error("INVALID_WEIGHTS", path + ".path", "must be a relative path without traversal")
    if value["order"] != "FPAB2":
        _error("INVALID_WEIGHTS", path + ".order", "must be FPAB2")
    if not isinstance(value["weights_id"], str) or not value["weights_id"]:
        _error("INVALID_WEIGHTS", path + ".weights_id", "must be a non-empty string")
    _positive_decimal(value["weights_scale"], path + ".weights_scale", "INVALID_WEIGHTS")
    if value["compute_mode"] not in ("FP32", "TF32"):
        _error("INVALID_WEIGHTS", path + ".compute_mode", "must be FP32 or TF32")


def _validate_assignments(value, global_first, global_nchan):
    path = "assignments"
    if not isinstance(value, list) or not value:
        _error("INVALID_ASSIGNMENTS", path, "expected a non-empty array")
    intervals = []
    instance_keys = set()
    for index, assignment in enumerate(value):
        item_path = "{}.{}".format(path, index)
        _exact_keys(
            assignment,
            ["server_id", "gpu_id", "first_channel_id", "nchan", "deployment_profile"],
            item_path,
        )
        server_id = _identifier(assignment["server_id"], item_path + ".server_id")
        gpu_id = _integer(assignment["gpu_id"], item_path + ".gpu_id", 0, 255)
        first = _integer(assignment["first_channel_id"], item_path + ".first_channel_id", 0, _UINT16_MAX)
        raw_nchan = assignment["nchan"]
        if isinstance(raw_nchan, bool) or not isinstance(raw_nchan, int):
            _error("INVALID_INTEGER", item_path + ".nchan", "expected integer")
        if raw_nchan < 1 or raw_nchan > 255:
            _error("INVALID_NCHAN", item_path + ".nchan", "must be between 1 and 255")
        nchan = raw_nchan
        _string(assignment["deployment_profile"], item_path + ".deployment_profile", "INVALID_ASSIGNMENTS")
        key = (server_id, gpu_id)
        if key in instance_keys:
            _error("DUPLICATE_INSTANCE", item_path, "server_id/gpu_id is already assigned")
        instance_keys.add(key)
        end = first + nchan
        if end > _UINT16_MAX + 1:
            _error("CHANNEL_ID_RANGE", item_path + ".nchan", "assignment exceeds uint16 channel IDs")
        intervals.append((first, end, index, nchan))

    expected_end = global_first + global_nchan
    ordered = sorted(intervals)
    previous_end = global_first
    for first, end, index, _nchan in ordered:
        item_path = "assignments.{}.first_channel_id".format(index)
        if first < global_first or end > expected_end:
            _error("CHANNEL_COVERAGE", item_path, "assignment lies outside global channel selection")
        if first < previous_end:
            _error("CHANNEL_OVERLAP", item_path, "assignment overlaps another assignment")
        if first > previous_end:
            _error("CHANNEL_GAP", item_path, "assignment leaves a channel gap")
        previous_end = end
    if previous_end != expected_end:
        _error("CHANNEL_COVERAGE", path, "assignments do not cover the global channel selection")

    sizes = [item[3] for item in intervals]
    if max(sizes) - min(sizes) > 1:
        _error("ALLOCATION_IMBALANCE", path, "channel counts may differ by at most one")


def _validate_frequency(channels, assignments):
    first_center = channels["first_center_frequency_hz"]
    spacing = channels["channel_spacing_hz"]
    for index, assignment in enumerate(assignments):
        local_first = first_center + (assignment["first_channel_id"] - channels["first_channel_id"]) * spacing
        numerator = 2 * local_first + (assignment["nchan"] - 1) * spacing
        if numerator % 2:
            _error(
                "FREQUENCY_NON_INTEGRAL",
                "assignments.{}.nchan".format(index),
                "local center frequency is not representable in integer Hz",
            )


def validate_global(document):
    """Validate and deep-copy a global multi-server observation document.

    Raises:
        ValueError: with a stable error code and the offending field path.
    """
    _exact_keys(
        document,
        ["schema_version", "metadata", "observation", "channels", "wire", "time_grid", "processing", "assignments", "monitoring"],
        "root",
    )
    _integer(document["schema_version"], "schema_version", 1, 1)

    metadata = document["metadata"]
    _exact_keys(metadata, ["telescope"], "metadata")
    _string(metadata["telescope"], "metadata.telescope")

    observation = document["observation"]
    _exact_keys(
        observation,
        ["observation_id", "utc_start", "duration_seconds", "station_ids", "npol", "sample_interval_ps"],
        "observation",
    )
    _identifier(observation["observation_id"], "observation.observation_id")
    _validate_utc(observation["utc_start"], "observation.utc_start")
    duration_ps = _duration_ps(observation["duration_seconds"], "observation.duration_seconds")
    stations = observation["station_ids"]
    if not isinstance(stations, list) or not stations:
        _error("INVALID_STATIONS", "observation.station_ids", "expected a non-empty array")
    seen_stations = set()
    for index, station in enumerate(stations):
        station_path = "observation.station_ids.{}".format(index)
        _integer(station, station_path, 0, _UINT16_MAX)
        if station in seen_stations:
            _error("DUPLICATE_STATION", station_path, "station ID is repeated")
        seen_stations.add(station)
    npol = _integer(observation["npol"], "observation.npol", 1, 2)
    sample_interval_ps = _integer(observation["sample_interval_ps"], "observation.sample_interval_ps", 1, _UINT64_MAX)

    channels = document["channels"]
    _exact_keys(
        channels,
        ["first_channel_id", "nchan", "first_center_frequency_hz", "channel_spacing_hz"],
        "channels",
    )
    global_first = _integer(channels["first_channel_id"], "channels.first_channel_id", 0, _UINT16_MAX)
    global_nchan = _integer(channels["nchan"], "channels.nchan", 1, _UINT16_MAX + 1)
    if global_first + global_nchan > _UINT16_MAX + 1:
        _error("CHANNEL_ID_RANGE", "channels.nchan", "global selection exceeds uint16 channel IDs")
    first_center = _integer(channels["first_center_frequency_hz"], "channels.first_center_frequency_hz", 1, _INT64_MAX)
    spacing = _integer(channels["channel_spacing_hz"], "channels.channel_spacing_hz", 1, _INT64_MAX)
    if first_center + (global_nchan - 1) * spacing > _UINT64_MAX:
        _error("FREQUENCY_RANGE", "channels.channel_spacing_hz", "global frequency range overflows uint64")

    wire = document["wire"]
    _exact_keys(wire, ["profile", "samples_per_packet"], "wire")
    _string(wire["profile"], "wire.profile")
    samples_per_packet = _integer(wire["samples_per_packet"], "wire.samples_per_packet", 1, _UINT32_MAX)

    time_grid = document["time_grid"]
    _exact_keys(time_grid, ["samples_per_block"], "time_grid")
    samples_per_block = _integer(time_grid["samples_per_block"], "time_grid.samples_per_block", 1, _UINT64_MAX)

    processing = document["processing"]
    _exact_keys(processing, ["weights", "conversion_scale", "product", "integration"], "processing")
    _validate_weights(processing["weights"])
    _positive_decimal(processing["conversion_scale"], "processing.conversion_scale", "INVALID_CONVERSION_SCALE")
    product = processing["product"]
    if product not in ("POWER", "COHERENCY"):
        _error("INVALID_PRODUCT", "processing.product", "must be POWER or COHERENCY")
    integration = processing["integration"]
    _exact_keys(integration, ["length", "operation"], "processing.integration")
    integration_length = _integer(integration["length"], "processing.integration.length", 1, _UINT64_MAX)
    if integration["operation"] not in ("SUM", "MEAN"):
        _error("INVALID_INTEGRATION", "processing.integration.operation", "must be SUM or MEAN")

    if product == "POWER" and npol != 1:
        _error("PRODUCT_POLARIZATION", "observation.npol", "POWER requires npol=1")
    if product == "COHERENCY" and npol != 2:
        _error("PRODUCT_POLARIZATION", "observation.npol", "COHERENCY requires npol=2")
    if samples_per_block % samples_per_packet:
        _error("TIME_GRID_PACKET_DIVISIBILITY", "time_grid.samples_per_block", "must be divisible by samples_per_packet")
    if samples_per_block % integration_length:
        _error("TIME_GRID_INTEGRATION_DIVISIBILITY", "processing.integration.length", "must divide samples_per_block")
    if integration_length > samples_per_block:
        _error("TIME_GRID_INTEGRATION_RANGE", "processing.integration.length", "must not exceed samples_per_block")
    _checked_time_product(samples_per_block, sample_interval_ps, "time_grid.samples_per_block")
    _checked_time_product(integration_length, sample_interval_ps, "processing.integration.length")
    packet_period_ps = _checked_time_product(samples_per_packet, sample_interval_ps, "wire.samples_per_packet")
    if duration_ps % packet_period_ps:
        _error("DURATION_GROUP_DIVISIBILITY", "observation.duration_seconds", "must contain a whole packet group")

    _validate_assignments(document["assignments"], global_first, global_nchan)
    _validate_frequency(channels, document["assignments"])

    monitoring = document["monitoring"]
    _exact_keys(monitoring, ["report_interval_ms", "rate_window_ms", "heartbeat_timeout_ms"], "monitoring")
    for field in ("report_interval_ms", "rate_window_ms", "heartbeat_timeout_ms"):
        _integer(monitoring[field], "monitoring." + field, 1, _UINT32_MAX)

    return copy.deepcopy(document)
