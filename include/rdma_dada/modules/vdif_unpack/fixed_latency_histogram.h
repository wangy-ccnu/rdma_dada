#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace rdma_dada {
namespace modules {
namespace vdif_unpack {

// Fixed-memory latency distribution. Quantiles are reported as bucket upper
// bounds; samples beyond the configured range use the observed maximum.
class FixedLatencyHistogram {
public:
    FixedLatencyHistogram(std::uint64_t quantization_ns,
                          std::size_t bucket_count);

    void Reset();
    void Record(std::uint64_t latency_ns);
    std::uint64_t QuantileNs(double quantile) const;

    std::uint64_t count() const;
    std::uint64_t total_ns() const;
    std::uint64_t maximum_ns() const;
    std::uint64_t quantization_ns() const;

private:
    std::uint64_t quantization_ns_;
    std::vector<std::uint64_t> buckets_;
    std::uint64_t overflow_count_;
    std::uint64_t count_;
    std::uint64_t total_ns_;
    std::uint64_t maximum_ns_;
};

}  // namespace vdif_unpack
}  // namespace modules
}  // namespace rdma_dada
