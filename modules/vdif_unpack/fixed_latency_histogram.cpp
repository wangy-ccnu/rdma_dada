#include "rdma_dada/modules/vdif_unpack/fixed_latency_histogram.h"

#include <algorithm>
#include <cmath>

namespace rdma_dada {
namespace modules {
namespace vdif_unpack {

FixedLatencyHistogram::FixedLatencyHistogram(
    std::uint64_t quantization_ns, std::size_t bucket_count)
    : quantization_ns_(quantization_ns == 0U ? 1U : quantization_ns),
      buckets_(bucket_count == 0U ? 1U : bucket_count, 0U),
      overflow_count_(0U), count_(0U), total_ns_(0U), maximum_ns_(0U) {}

void FixedLatencyHistogram::Reset() {
    std::fill(buckets_.begin(), buckets_.end(), 0U);
    overflow_count_ = 0U;
    count_ = 0U;
    total_ns_ = 0U;
    maximum_ns_ = 0U;
}

void FixedLatencyHistogram::Record(std::uint64_t latency_ns) {
    const std::uint64_t bucket = latency_ns / quantization_ns_;
    if (bucket < buckets_.size()) {
        ++buckets_[static_cast<std::size_t>(bucket)];
    } else {
        ++overflow_count_;
    }
    ++count_;
    total_ns_ += latency_ns;
    maximum_ns_ = std::max(maximum_ns_, latency_ns);
}

std::uint64_t FixedLatencyHistogram::QuantileNs(double quantile) const {
    if (count_ == 0U || !(quantile > 0.0) || quantile > 1.0) return 0U;
    const std::uint64_t rank = static_cast<std::uint64_t>(
        std::ceil(quantile * static_cast<double>(count_)));
    std::uint64_t cumulative = 0U;
    for (std::size_t index = 0; index < buckets_.size(); ++index) {
        cumulative += buckets_[index];
        if (cumulative >= rank) {
            return static_cast<std::uint64_t>(index + 1U) * quantization_ns_;
        }
    }
    return overflow_count_ == 0U ? 0U : maximum_ns_;
}

std::uint64_t FixedLatencyHistogram::count() const { return count_; }
std::uint64_t FixedLatencyHistogram::total_ns() const { return total_ns_; }
std::uint64_t FixedLatencyHistogram::maximum_ns() const { return maximum_ns_; }
std::uint64_t FixedLatencyHistogram::quantization_ns() const {
    return quantization_ns_;
}

}  // namespace vdif_unpack
}  // namespace modules
}  // namespace rdma_dada
