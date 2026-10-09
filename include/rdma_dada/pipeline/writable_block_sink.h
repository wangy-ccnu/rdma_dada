#pragma once

#include <cstdint>
#include <string>

namespace rdma_dada {
namespace pipeline {

class WritableBlockSink {
public:
    virtual ~WritableBlockSink() {}
    virtual bool Acquire(std::uint8_t** data, std::uint64_t* capacity,
                         std::string* error) = 0;
    virtual bool Commit(std::uint64_t bytes, std::string* error) = 0;
};

}  // namespace pipeline
}  // namespace rdma_dada
