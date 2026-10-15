#ifndef RDMA_DADA_CONTROL_LIFECYCLE_JSON_H
#define RDMA_DADA_CONTROL_LIFECYCLE_JSON_H

#include <cerrno>
#include <string>
#include <fcntl.h>
#include <unistd.h>

namespace rdma_dada {
namespace control {

// The caller supplies a fresh path in its private generation directory.
// link publishes atomically without replacing an earlier lifecycle record.
inline bool PublishLifecycleJson(const std::string& path,
                                 const std::string& json) {
    if (path.empty()) return true;
    const std::string temporary = path + ".tmp." + std::to_string(getpid());
    const int fd = open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL |
                        O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return false;
    std::size_t offset = 0;
    while (offset < json.size()) {
        const ssize_t count = write(fd, json.data() + offset, json.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        offset += static_cast<std::size_t>(count);
    }
    bool ok = offset == json.size() && fsync(fd) == 0;
    if (close(fd) != 0) ok = false;
    if (ok) ok = link(temporary.c_str(), path.c_str()) == 0;
    if (unlink(temporary.c_str()) != 0) ok = false;
    return ok;
}

}  // namespace control
}  // namespace rdma_dada

#endif
