# Production unpack parser-worker scaling

Updated: 2026-09-05

This document records the interpretation of the controlled 1/2/4 parser-worker
comparison for the production `A=469,F=4,P=1` unpack boundary. The immutable
measurements remain in the local catalog; this file is an index, not a copy or
replacement of the suite JSON.

## Controlled protocol

All three experiments used the nominal 30 Gb/s campaign (`30.2505 Gb/s` runner
UDP-datagram target), 60 seconds, one-second preparation, one warm-up and three
measured repetitions. The two sender processes used the same 235/234 Station
partition and source ports 45871/55871. Receiver CPU 13, coordinator CPU 14,
writer CPU 19, sink CPU 20, NUMA node 1, poll batch 32, WR depth 1024 and the
raw/compute ring, window and reorder geometry were held fixed. Only the parser
CPU list changed.

Source manifest:
`ce66a44396f870c2df229ef9ebe7e8ba518ef8b0830fe37ccdef07b1b75a4c06`.
Baseline profile SHA256:
`f25d3a017d10544fc8b84303b9cf7778b50d2828237618894dd97b4a51db3a5f`.

## Measured result

Values below are medians over the three measured repetitions. Service time is
the raw-block unpack callback interval. CPU cost is whole-process CPU time per
accepted payload GB and is comparable because process lifecycle and all other
conditions were matched.

| Parser workers | CPUs | Result | Sender rate (Gb/s) | Packet/record deficit | Service P50/P95 (ms) | P95 capacity (Gb/s) | P95 headroom | CPU s/GB | Writer queue HWM |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 15 | `PERFORMANCE_FAIL` | 30.248563934 | 26,175,187 | 26.36 / 28.38 | 14.19 | -53.09% | 0.9332 | 11/33 |
| 2 | 15,16 | `PASS` | 30.248563939 | 0 | 11.07 / 11.24 | 35.83 | 18.43% | 0.6446 | 14/33 |
| 4 | 15,16,17,18 | `PASS` | 30.248563939 | 0 | 8.74 / 9.05 | 44.50 | 47.09% | 0.8501 | 16/33 |

The one-parser configuration completed all four repetitions but failed every
one: its P95 service time was more than twice the planned 13.312 ms raw-block
arrival interval. The three measured sender-to-receiver deficits ranged from
25,314,190 to 26,509,202 packets, and the median receiver-to-unpack gap was
915,957 records. No late, duplicate, bad-header, invalid-data, unknown-Station
or out-of-range records were reported.

Both two- and four-parser configurations closed sender, receiver, unpack,
compute-drain, EOD and cleanup counts in every repetition. Compared with two
parsers, four parsers reduced median P95 service time by 19.5% and increased
the P95-derived service capacity by 24.2%, at 31.9% higher process CPU seconds
per payload GB. Thus two parsers are sufficient for the nominal 30 Gb/s point,
while four parsers provide greater measured service headroom.

## Evidence locations

- 1 parser: `unpack-30.2505Gbps-60s-20260905T084505Z`, remote
  `/home/user/wy/task8c-multistation-unpack-workers-1-characterization-results/unpack-30.2505Gbps-60s-20260905T084505Z`, suite-manifest SHA256
  `fcd275e21f588d792d88483111048a978e8c50c0b119d12e2a9b964a3e026322`.
- 2 parsers: `unpack-30.2505Gbps-60s-20260905T075730Z`, remote
  `/home/user/wy/task8c-multistation-unpack-workers-2-results/unpack-30.2505Gbps-60s-20260905T075730Z`, suite-manifest SHA256
  `a9ebe2fdce11980992f08e44db2b9fbca602870d4240ad09b4434ca4afb3647a`.
- 4 parsers: `unpack-30.2505Gbps-60s-20260905T081600Z`, remote
  `/home/user/wy/task8c-multistation-unpack-workers-4-results/unpack-30.2505Gbps-60s-20260905T081600Z`, suite-manifest SHA256
  `9f2aea037f4c4c7d1ccaa46632d2943ffc56f6f348c8b054aca21d67fe9381d2`.

The suites are queryable under `.staging/catalog-results` by exact suite ID.
The complete comparison has been promoted to `accepted-results.json`: the
one-parser suite is retained as an accepted performance-boundary failure, and
the two- and four-parser suites are accepted PASS evidence. Promotion does not
change the immutable suite results or turn the one-parser failure into a PASS.
