#include "rdma_dada/modules/vdif_unpack/vdif_unpack_config.h"

#include <limits>
#include <set>

namespace rdma_dada {
namespace modules {
namespace vdif_unpack {
namespace {

bool Fail(const std::string& message, std::string* error) {
    if (error) *error = message;
    return false;
}

bool CheckedMultiply(std::uint64_t a, std::uint64_t b,
                     const std::string& name, std::uint64_t* result,
                     std::string* error) {
    if (a != 0 && b > std::numeric_limits<std::uint64_t>::max() / a) {
        return Fail(name + " exceeds uint64 range", error);
    }
    *result = a * b;
    return true;
}

}  // namespace

bool ComputeVdifUnpackLayout(const VdifUnpackConfig& config,
                             const PipelineConfig& pipeline,
                             const PacketFormatConfig& packet,
                             VdifUnpackLayout* layout,
                             std::string* error) {
    if (!layout) return Fail("layout output pointer is null", error);
    PipelineLayout base = {};
    if (!ComputePipelineLayout(pipeline, &base, error)) return false;
    if (config.antenna_map.size() != pipeline.nant)
        return Fail("antenna_map length must equal NANT", error);
    std::set<std::uint16_t> stations(config.antenna_map.begin(), config.antenna_map.end());
    if (stations.size() != config.antenna_map.size())
        return Fail("antenna_map contains duplicate Station IDs", error);
    if (config.window_blocks < 2) return Fail("window.blocks must be at least 2", error);
    if (static_cast<std::uint32_t>(config.first_channel_id) + pipeline.nchan > 65536U)
        return Fail("selected channel range exceeds uint16 range", error);
    if (packet.format_id != "project-vdif-v1" || packet.application_header_bytes != 32 ||
        packet.sample_format != "CI8" || packet.sample_encoding != "TWOS_COMPLEMENT" ||
        packet.component_order != "IQ" || packet.packed_order.size() != 3 ||
        packet.packed_order[0] != "T" || packet.packed_order[1] != "F" ||
        packet.packed_order[2] != "P")
        return Fail("packet profile does not match the Project VDIF wire contract", error);

    VdifUnpackLayout result = {};
    result.raw_record_bytes = base.raw_record_bytes;
    result.records_per_raw_block = pipeline.records_per_block;
    if (!CheckedMultiply(pipeline.packet_payload_bytes, pipeline.nant,
                         "group bytes", &result.group_bytes, error) ||
        !CheckedMultiply(config.window_blocks, base.packets_per_antenna_per_block,
                         "window group capacity", &result.window_capacity_groups, error) ||
        !CheckedMultiply(config.window_blocks, base.compute_block_bytes,
                         "window bytes", &result.window_bytes, error)) return false;
    result.compute_block_bytes = base.compute_block_bytes;
    if (result.window_bytes > config.max_window_bytes)
        return Fail("window allocation exceeds window.max_bytes", error);
    *layout = result;
    return true;
}

bool BuildVdifUnpackRuntimeFromResolvedPlan(
    const ResolvedObservationPlan& plan,
    VdifUnpackConfig* config,
    PipelineConfig* pipeline,
    PipelineLayout* pipeline_layout,
    VdifUnpackLayout* unpack_layout,
    std::string* error) {
    if (!config || !pipeline || !pipeline_layout || !unpack_layout) {
        return Fail("unpack runtime output pointer is null", error);
    }
    if (plan.config_id.size() != 64U || plan.geometry_id.size() != 64U) {
        return Fail("resolved plan identities are missing", error);
    }
    PipelineConfig pipeline_result;
    PipelineLayout pipeline_layout_result;
    if (!BuildPipelineRuntimeFromResolvedPlan(
            plan, &pipeline_result, &pipeline_layout_result, error)) {
        return false;
    }
    VdifUnpackConfig config_result = {};
    config_result.config_id = plan.config_id;
    config_result.geometry_id = plan.geometry_id;
    config_result.input_key = plan.source.raw_key;
    config_result.output_key = plan.source.compute_key;
    config_result.first_channel_id = plan.source.first_channel_id;
    config_result.antenna_map = plan.source.station_ids;
    config_result.window_blocks =
        static_cast<std::uint32_t>(plan.source.window_blocks);
    config_result.max_window_bytes = plan.window_payload_bytes;
    config_result.output_memory = "HOST";
    config_result.run_once = plan.source.run_once;
    VdifUnpackLayout unpack_result;
    if (!ComputeVdifUnpackLayout(config_result, pipeline_result, plan.wire,
                                 &unpack_result, error)) {
        return false;
    }
    if (unpack_result.raw_record_bytes != plan.raw_record_bytes ||
        unpack_result.records_per_raw_block != plan.records_per_block ||
        unpack_result.window_capacity_groups != plan.window_groups ||
        unpack_result.window_bytes != plan.window_payload_bytes ||
        unpack_result.compute_block_bytes != plan.compute_block_bytes) {
        return Fail("unpack runtime conflicts with resolved plan", error);
    }
    *config = config_result;
    *pipeline = pipeline_result;
    *pipeline_layout = pipeline_layout_result;
    *unpack_layout = unpack_result;
    return true;
}

}  // namespace vdif_unpack
}  // namespace modules
}  // namespace rdma_dada
