# VDIF ingest and ATFP unpack status

Updated: 2026-09-05
Tested source manifest:
`ce66a44396f870c2df229ef9ebe7e8ba518ef8b0830fe37ccdef07b1b75a4c06`

This document records the current product boundary and performance evidence for
`rdma2dada -> raw ring -> vdif_unpack_worker -> compute ring`. It deliberately
does not treat sender rate or a one-off successful run as formal acceptance.

## Product architecture

### Direct raw-ring receiver

`rdma2dada` uses one destination-only flow, one QP/CQ and one poll thread. Each
receive WR has two SGEs:

- 42-byte Ethernet/IPv4/UDP header -> fixed registered scratch;
- Project VDIF record -> the assigned PSRDADA raw-ring record slot.

The receiver holds two raw blocks concurrently, tracks completion per slot,
publishes blocks in order, reposts WRs, and commits an exact partial tail at
EOD. The normal path has no packet-to-ring payload memcpy. Current test profile
defaults are WR depth 1024 and CQ poll batch 32; these are configurable rather
than protocol constants.

### Parallel ATFP unpack

One `vdif_unpack_worker` process keeps one raw-ring reader and one compute-ring
writer. Its threads are:

- coordinator: owns raw-block lifecycle, descriptor order, watermarks and
  window reservation;
- parser workers: decode contiguous record ranges and copy accepted payloads;
- writer: exclusively owns compute-ring Acquire/Commit and ordered publication.

The worker uses 16-byte pointer-free descriptors, preallocated bounded queues,
and ACK-controlled window leases. It outputs payload-only, block-scoped
`[A,T,F,P]` data and preserves missing/duplicate/late/invalid/EOD semantics.

The current qths1 NUMA1 test profile is receiver CPU 13, coordinator 14,
workers 15–18, writer 19 and sink 20. These values belong to the test profile,
not the product contract.

## Measured geometry

The production A469 campaign uses:

- Project VDIF record: 4,128 bytes = 32-byte header + 4,096-byte payload;
- raw block: 50,336,832 bytes = 12,194 records;
- compute block: 49,946,624 bytes;
- planned raw-block arrival interval at the nominal 30 Gb/s point: 13.312 ms.

Window blocks and reorder horizon are derived from requested rate,
`missing_wait_ms`, Station-skew reserve and block geometry. Ring/window capacity
is burst tolerance; it is not evidence of sufficient steady-state service rate.

## Current evidence

### Accepted repeatable evidence

- module/reference, partial/EOD, rollover, duplicate, missing and zero-fill
  fixtures pass;
- PSRDADA raw→compute integration and thread-affinity paths pass;
- receive-only 30 Gbps/60 s completed one warm-up plus three measured runs with
  exact sender/receiver closure, fixed source ports, clean dbnull/EOD and
  cleanup; result root:
  `/home/user/wy/task8c-drain-results/drain-receive-30Gbps-60s-20260826-r2`;
- receive+unpack 30 Gbps/60 s completed the same repetition contract with
  exact sender→receiver→unpack closure and zero missing/late/duplicate/header
  errors; result root:
  `/home/user/wy/task8c-unpack-results/unpack-30Gbps-60s-20260826-r3`;
- the accepted unpack profile fixes receiver CPU 13, coordinator/parser/writer
  CPUs 14--19, sink CPU 20, NUMA1, WR depth 1024, poll batch 32, source ports,
  ring/window geometry and one-second preparation.

### Parser-worker scaling

A matched production `A=469,F=4,P=1` comparison held the complete receiver,
ring, sender, duration and placement profile fixed and changed only the parser
worker count. One parser failed all warm-up/measured repetitions; its measured
median P95 raw-block service time was 28.38 ms versus the 13.312 ms planned
arrival interval, with a median 26,175,187-record deficit. Two and four parsers
both completed one warm-up plus three measured runs with exact closure.

The two-parser median P95 service time/capacity/headroom was
11.24 ms / 35.83 Gb/s / 18.43%. Four parsers improved these to
9.05 ms / 44.50 Gb/s / 47.09%, while increasing CPU cost from 0.6446 to
0.8501 CPU seconds per payload GB. Exact suite identities and the full table
are recorded in
[`results/unpack-worker-scaling-20260905.md`](results/unpack-worker-scaling-20260905.md).

### Remaining limits

- the 1-parser production configuration is a reproducible unpack/raw-ring
  backpressure boundary and is not an accepted operating profile;
- 35 Gbps/60 s historical evidence does not by itself identify unpack as the
  first saturated stage;
- all current rate results stop at `dada_dbnull` on the compute ring and exclude
  `pipeline_worker`, H2D, GPU algorithms, D2H and output ring.

The accepted wording is therefore:

> 30 Gbps is the accepted repeatable payload rate for the receive-only and
> receive+unpack boundaries on qths1. The full GPU-pipeline stable rate has not
> been established.

## Reuse rule

Later GPU/full tests inherit the accepted profile rather than silently changing
CPU/NUMA, source ports, preparation or ring/window geometry. They do not rerun
the receive/unpack acceptance unless the implementation, hardware boundary or
profile changes. Complete GPU-pipeline acceptance remains a separate gate.

The next executable work is defined in
[`superpowers/plans/2026-08-19-receiver-admission-and-full-pipeline-acceptance.md`](superpowers/plans/2026-08-19-receiver-admission-and-full-pipeline-acceptance.md).
