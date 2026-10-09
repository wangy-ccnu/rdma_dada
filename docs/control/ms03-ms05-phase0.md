# MS-03–05 configuration Phase 0 refresh — 2026-10-08

Executor: `GPU服务器代码测试`, task `019fbb68-ae6f-77f0-bfdd-30bc30bd6c45`,
turn `01a11b81-b2bb-7b40-b847-8aa96fc4a21b`.
Coordinator: `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Read-only environment checks only; no synchronization/build/project tests or
data flow. Callback was rejected; coordinator retrieved command evidence via
read_thread. Notification failure is separate from the environment findings.

## Observed prerequisites

- HF hostname qtrelay0 and qths1 reachable as uid1000 user. Both use
  `/usr/bin/python3` 3.8.10. HF rsync3.1.3 and sha256sum/coreutils8.30.
- qths fixed `/home/user/wy/tools/cmake-3.31.12/bin/cmake` and sibling ctest
  report3.31.12 and executable mode755. `/usr/bin/gcc` and g++ resolve to9.4.0;
  GNU ld2.34 and glibc2.31.
- Python ldd resolves all listed dependencies, including libc/pthread/dl/util/
  m/expat/z. This does not yet verify a newly built compiler's linkage.
- libc binary contains the name renameat2 (final bounded grep output confirms
  the name). This is not an executed no-replace rename test; the Linux bundle
  tests must verify actual lookup/publication behavior before acceptance.
- Both hosts' `/home/user/wy/rdma_dada-multiserver` and
  `/home/user/wy/task8c-multiserver-results` exist and are user-owned. These are
  the existing feature namespaces; paper canonical roots remain outside scope.
- qths disk approximately741G free;48 CPUs;125Gi memory/122Gi available.
  NUMA0 CPUs0–11,24–35; NUMA1 CPUs12–23,36–47.
- ens2np0 is UP/MTU9000; enp4s0 UP/MTU1500. Exact link speed is not a gate for
  this portable configuration stage and is not claimed here.
- UTC date and clocksource tsc readable; clock synchronization accuracy is not
  established by these commands and is not needed for these offline tests.
- No matched task data-flow processes. IPC output contains two unrelated gdm
  shared-memory segments, not an empty system; semaphore listing is empty.

Evidence commands used SSH with fixed executable argv (hostname/id/version,
ls/df/free/numactl/ip/date, ldd and bounded grep); a printf argv probe returned
the expected token. Combined shell exit statuses do not individually establish
every command's result; the recorded concrete outputs above are the basis for
these findings. The pgrep group exit1 means no final process match, not a
project failure. An earlier unbounded binary grep produced noisy output and
was superseded by the bounded symbol-name query.

Required offline toolchain/readiness facts are sufficient to prepare the
combined source handoff. Synchronization identity, fresh compiler linkage and
actual Linux publication tests remain formal test gates. GPU/RDMA/PSRDADA
runtime readiness and NIC/clock performance are not acceptance claims here.
