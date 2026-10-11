#include "rdma_dada/pipeline/worker_config.h"

#include "rdma_dada/config/resolved_plan_json.h"
#include "rdma_dada/pipeline/complex32.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <sstream>

namespace rdma_dada {
namespace pipeline {
namespace {

bool Fail(const std::string& message, std::string* error) {
    if (error) *error = message;
    return false;
}

bool CheckedMultiply(std::uint64_t left, std::uint64_t right,
                     const std::string& name, std::uint64_t* result,
                     std::string* error) {
    if (!result) return Fail(name + " output pointer is null", error);
    if (left != 0 && right > std::numeric_limits<std::uint64_t>::max() / left) {
        return Fail(name + " exceeds uint64 range", error);
    }
    *result = left * right;
    return true;
}

bool CheckedAdd(std::uint64_t left, std::uint64_t right,
                const std::string& name, std::uint64_t* result,
                std::string* error) {
    if (!result) return Fail(name + " output pointer is null", error);
    if (right > std::numeric_limits<std::uint64_t>::max() - left) {
        return Fail(name + " exceeds uint64 range", error);
    }
    *result = left + right;
    return true;
}

}  // namespace

bool BuildWorkerConfigFromResolvedPlan(
    const ResolvedObservationPlan& plan,
    WorkerConfig* config,
    WorkerBlockGeometry* geometry,
    std::string* error) {
    if (!config || !geometry) {
        return Fail("resolved worker output pointer is null", error);
    }
    ResolvedObservationPlan verified;
    if (!ResolveObservationPlan(plan.source, plan.wire, &verified, error)) {
        return false;
    }
    if (plan.source.modules.empty()) {
        return Fail("pipeline_worker requires a beamform module chain", error);
    }
    if (verified.nbeam != plan.nbeam ||
        verified.compute_block_bytes != plan.compute_block_bytes ||
        verified.converted_block_bytes != plan.converted_block_bytes ||
        verified.beamformed_block_bytes != plan.beamformed_block_bytes ||
        verified.product_block_bytes != plan.product_block_bytes ||
        verified.output_samples_per_block != plan.output_samples_per_block ||
        verified.output_block_bytes != plan.output_block_bytes ||
        verified.output_ring_bytes != plan.output_ring_bytes ||
        verified.output_data_stage != plan.output_data_stage ||
        verified.output_order != plan.output_order ||
        verified.output_sample_format != plan.output_sample_format) {
        return Fail("resolved plan contains stale worker geometry", error);
    }

    const ObservationModuleConfig& beamform = plan.source.modules[0];
    WorkerConfig result = WorkerConfig();
    result.input_key = plan.source.compute_key;
    result.output_key = plan.source.output_key;
    std::ostringstream input_key;
    std::ostringstream output_key;
    input_key << "0x" << std::hex << result.input_key;
    output_key << "0x" << std::hex << result.output_key;
    result.input_key_text = input_key.str();
    result.output_key_text = output_key.str();
    result.execution_backend = plan.source.backend;
    result.cuda_device = plan.source.cuda_device;
    result.run_once = plan.source.run_once;
    result.cuda_pipeline_mode = plan.source.cuda_pipeline_mode;
    result.cuda_inflight_blocks = plan.source.cuda_inflight_blocks;
    result.nchan = plan.source.nchan;
    result.nant = plan.nant;
    result.npol = plan.source.npol;
    result.udp_payload_bytes = plan.payload_bytes;
    result.samples_per_udp = plan.source.samples_per_packet;
    result.udp_packets_per_antenna_per_block =
        plan.source.groups_per_block;

    errno = 0;
    char* scale_end = NULL;
    result.conversion_scale =
        std::strtod(plan.source.conversion_scale.c_str(), &scale_end);
    if (errno == ERANGE || scale_end == plan.source.conversion_scale.c_str() ||
        *scale_end != '\0' || !std::isfinite(result.conversion_scale) ||
        result.conversion_scale <= 0.0) {
        return Fail("processing.conversion.scale must be finite and positive",
                    error);
    }
    result.weights_file = beamform.weights_file;
    result.weights_order = beamform.weights_order;
    result.weights_id = beamform.weights_id;
    errno = 0;
    char* weights_scale_end = NULL;
    result.weights_scale =
        std::strtod(beamform.weights_scale.c_str(), &weights_scale_end);
    if (errno == ERANGE || weights_scale_end == beamform.weights_scale.c_str() ||
        *weights_scale_end != '\0' || !std::isfinite(result.weights_scale) ||
        result.weights_scale <= 0.0) {
        return Fail("beamform weights_scale must be finite and positive",
                    error);
    }
    result.nbeam = plan.nbeam;
    result.compute_mode = beamform.compute_mode;
    result.product = WorkerProduct::kBeamformed;
    result.integration_enabled = false;
    result.integration_length = 1U;
    result.integration_operation = "MEAN";

    if (plan.source.modules.size() >= 2U) {
        const ObservationModuleKind kind = plan.source.modules[1].kind;
        if (kind == ObservationModuleKind::kPower) {
            result.product = WorkerProduct::kPower;
        } else if (kind == ObservationModuleKind::kStokes) {
            result.product = WorkerProduct::kStokes;
        } else {
            return Fail("resolved worker product must be power or stokes",
                        error);
        }
    }
    if (plan.source.modules.size() == 3U) {
        const ObservationModuleConfig& integration = plan.source.modules[2];
        if (integration.kind != ObservationModuleKind::kIntegrate) {
            return Fail("resolved worker third module must be integration",
                        error);
        }
        result.integration_enabled = true;
        result.integration_length = integration.integration_length;
        result.integration_operation = integration.integration_operation;
    }

    WorkerBlockGeometry computed;
    if (!ComputeWorkerBlockGeometry(result, &computed, error)) return false;
    if (computed.input_block_bytes != plan.compute_block_bytes ||
        computed.converted_block_bytes != plan.converted_block_bytes ||
        computed.beamformed_block_bytes != plan.beamformed_block_bytes ||
        computed.product_block_bytes != plan.product_block_bytes ||
        computed.output_ntime != plan.output_samples_per_block ||
        computed.output_block_bytes != plan.output_block_bytes) {
        return Fail("worker geometry conflicts with resolved observation plan",
                    error);
    }
    *config = result;
    *geometry = computed;
    return true;
}

bool LoadWorkerConfigFromResolvedPlan(
    const std::string& path,
    WorkerConfig* config,
    WorkerBlockGeometry* geometry,
    std::string* error) {
    ResolvedObservationPlan plan;
    if (!LoadResolvedObservationPlan(path, &plan, error)) return false;
    return BuildWorkerConfigFromResolvedPlan(plan, config, geometry, error);
}

bool ComputeWorkerBlockGeometry(const WorkerConfig& config,
                                WorkerBlockGeometry* geometry,
                                std::string* error) {
    if (!geometry) return Fail("worker block geometry output is null", error);
    if (config.nchan == 0 || config.nant == 0 || config.npol == 0) {
        return Fail("input_geometry F/A/P dimensions must be greater than zero",
                    error);
    }
    if (config.udp_payload_bytes == 0 || config.samples_per_udp == 0 ||
        config.udp_packets_per_antenna_per_block == 0) {
        return Fail("input_geometry UDP and block counts must be greater than "
                    "zero", error);
    }
    if (config.nbeam == 0) {
        return Fail("beamform.nbeam must be greater than zero", error);
    }
    if (config.product == WorkerProduct::kStokes && config.npol != 2) {
        return Fail("STOKES block geometry requires input_geometry.npol=2",
                    error);
    }
    if (config.integration_enabled) {
        if (config.product == WorkerProduct::kBeamformed) {
            return Fail(
                "time integration requires POWER or STOKES product", error);
        }
        if (config.integration_length == 0) {
            return Fail("integration length must be greater than zero", error);
        }
        if (config.integration_operation != "SUM" &&
            config.integration_operation != "MEAN") {
            return Fail("integration operation must be SUM or MEAN", error);
        }
    }

    WorkerBlockGeometry result = WorkerBlockGeometry();
    if (!CheckedMultiply(config.samples_per_udp,
                         config.udp_packets_per_antenna_per_block,
                         "block time samples", &result.ntime, error) ||
        !CheckedMultiply(config.udp_payload_bytes, config.nant,
                         "UDP all-antenna group bytes",
                         &result.udp_antenna_group_bytes, error)) {
        return false;
    }

    std::uint64_t elements = 0;
    if (!CheckedMultiply(config.nchan, config.npol,
                         "input frame F*P", &elements, error) ||
        !CheckedMultiply(elements, config.nant,
                         "input frame F*P*A", &elements, error) ||
        !CheckedMultiply(elements, UINT64_C(2),
                         "input CI8 frame bytes",
                         &result.input_frame_bytes, error) ||
        !CheckedMultiply(result.input_frame_bytes, result.ntime,
                         "input block bytes", &result.input_block_bytes,
                         error)) {
        return false;
    }
    if (result.input_block_bytes % result.udp_antenna_group_bytes != 0) {
        return Fail(
            "computed input block bytes must be an integer multiple of "
            "udp_payload_bytes * NANT", error);
    }
    result.udp_group_multiple =
        result.input_block_bytes / result.udp_antenna_group_bytes;

    if (!CheckedMultiply(config.nchan, config.npol,
                         "converted frame F*P", &elements, error) ||
        !CheckedMultiply(elements, config.nant,
                         "converted frame F*P*A", &elements, error) ||
        !CheckedMultiply(elements, sizeof(Complex32),
                         "converted CF32 frame bytes",
                         &result.converted_frame_bytes, error) ||
        !CheckedMultiply(result.converted_frame_bytes, result.ntime,
                         "converted block bytes",
                         &result.converted_block_bytes, error)) {
        return false;
    }

    if (!CheckedMultiply(config.nchan, config.npol,
                         "beamformed frame F*P", &elements, error) ||
        !CheckedMultiply(elements, config.nbeam,
                         "beamformed frame F*P*B", &elements, error) ||
        !CheckedMultiply(elements, sizeof(Complex32),
                         "beamformed CF32 frame bytes",
                         &result.beamformed_frame_bytes, error) ||
        !CheckedMultiply(result.beamformed_frame_bytes, result.ntime,
                         "beamformed block bytes",
                         &result.beamformed_block_bytes, error)) {
        return false;
    }

    switch (config.product) {
        case WorkerProduct::kBeamformed:
            result.output_frame_bytes = result.beamformed_frame_bytes;
            break;
        case WorkerProduct::kPower:
            if (!CheckedMultiply(config.nchan, config.npol,
                                 "power frame F*P", &elements, error) ||
                !CheckedMultiply(elements, config.nbeam,
                                 "power frame F*P*B", &elements, error) ||
                !CheckedMultiply(elements, sizeof(float),
                                 "power F32 frame bytes",
                                 &result.output_frame_bytes, error)) {
                return false;
            }
            break;
        case WorkerProduct::kStokes:
            if (!CheckedMultiply(config.nchan, config.nbeam,
                                 "Stokes frame F*B", &elements, error) ||
                !CheckedMultiply(elements, UINT64_C(4),
                                 "Stokes frame F*B*S", &elements, error) ||
                !CheckedMultiply(elements, sizeof(float),
                                 "Stokes F32 frame bytes",
                                 &result.output_frame_bytes, error)) {
                return false;
            }
            break;
        default:
            return Fail("unsupported worker output product", error);
    }
    if (!CheckedMultiply(result.output_frame_bytes, result.ntime,
                         "unintegrated product block bytes",
                         &result.product_block_bytes,
                         error)) {
        return false;
    }
    result.output_ntime = result.ntime;
    result.output_block_bytes = result.product_block_bytes;
    result.scratch_block_bytes =
        config.product == WorkerProduct::kBeamformed ?
            0 : result.beamformed_block_bytes;
    if (config.integration_enabled) {
        if (result.ntime % config.integration_length != 0) {
            return Fail(
                "block T must be divisible by integration length", error);
        }
        result.output_ntime = result.ntime / config.integration_length;
        result.output_block_bytes =
            result.product_block_bytes / config.integration_length;
        if (!CheckedAdd(result.beamformed_block_bytes,
                        result.product_block_bytes,
                        "integrated chain scratch block bytes",
                        &result.scratch_block_bytes, error)) {
            return false;
        }
    }
    *geometry = result;
    return true;
}

const char* WorkerProductName(WorkerProduct product) {
    switch (product) {
        case WorkerProduct::kBeamformed: return "BEAMFORMED";
        case WorkerProduct::kPower: return "POWER";
        case WorkerProduct::kStokes: return "STOKES";
    }
    return "UNKNOWN";
}

}  // namespace pipeline
}  // namespace rdma_dada
