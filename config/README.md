# Observation 实例配置

## Unified observation configuration

For multi-server use, start with the [global/deployment/inventory guide](../docs/control/configuration.md).
It generates per-instance Observation inputs using the contract below.
Simulator examples below are test inputs, not deployment defaults.

`observation.example.json` is the sole schema-v1 pipeline/unpack/worker user input; `observation-v1.schema.json` documents the same strict
contract. The production C++ parser rejects missing and unknown fields.

User-entered fields cover observation timing, ordered Station IDs, channel and
polarization selection, telescope/frequency metadata, packet samples, block and
ring policy, receiver endpoint, storage policy and algorithm selection. Paths
are resolved relative to the observation JSON. Byte sizes and DADA headers are
not user inputs; `observation_config_compile` derives them from this file and
the fixed wire profile.

Preflight only:

```sh
build/observation_config_compile \
  --config config/observation.example.json \
  --preflight-only
```

Generate a new atomic artifact directory:

```sh
build/observation_config_compile \
  --config config/observation.example.json \
  --output-dir build/observation-artifacts
```

The destination must not already exist. It contains the verified resolved
configuration, ring plan, 4096-byte `raw.header`/`unpacked.header`, validation
report and `MANIFEST.sha256`. When `processing.modules` is non-empty, the
compiler also invokes the production module header transforms and writes
`converted.header`, `beamformed.header` and the final `output.header`; every
generated file is covered by the manifest.

`station_ids` order defines the A axis. `duration_seconds`, explicit
`processing.conversion.scale` and beamformer `weights_scale` are decimal
strings so they can be parsed without binary floating-point rounding. The
conversion scale must also be representable as a positive FP32 value.
`metadata.telescope`, `bandwidth_hz` and
`center_frequency_hz` are required and will propagate to generated DADA
headers.

## Runtime entry and internal geometry

`rdma2dada`, `vdif_unpack_worker` and `pipeline_worker` consume the same
compiler-generated `resolved_observation.json`. Independent pipeline, unpack
and worker JSON/CONF loaders, examples, inspect tools and the CONF converter
have been removed. There is no legacy fallback.

The fixed frontend byte/bit contract is documented in
[`packet_formats/`](packet_formats/README.md). Its schema-v2 profile defines
the 32-byte Project VDIF v1 header and wire TFP axes; Observation supplies the
payload size and axis lengths. Current unpack supports CI8 and rejects CI16.
Observation `station_ids` defines the ordered A axis, `first_channel_id`
selects the channel range, and `blocks.window_blocks` must be at least two.
The payload-only window size and allocation are derived from the resolved plan.
The window stores `[A,circular_group,T,F,P]`; compute output uses
`ORDER=ATFP`, `LAYOUT_SCOPE=BLOCK`.

`PipelineConfig`, `VdifUnpackConfig` and `WorkerConfig` are internal runtime
types. Their geometry helpers remain available to algorithms and tests;
they are not independent external configuration formats.

## Artifact and fixture ownership

- Observation/schema/wire files are maintained inputs. Multiserver global
  Observation/deployment inputs are compiled into per-node Observation and
  ResolvedPlan bundles; see [control configuration](../docs/control/configuration.md).
- Artifact directories contain generated resolved plans, ring plans, headers,
  reports and manifests. Use a directory under `build*/`, `tmp/` or
  `test-results/` for local generated output; these directories are ignored.
- Portable geometry tests use explicit internal C++ fixtures or resolve
  Observation inputs. They do not require a separate pipeline/unpack/worker JSON.
- `config/testing/` contains versioned test inputs and frozen source manifests;
  accepted results and historical manifests retain the identity of their
  original snapshot, including references to files removed from this branch.

## FPGA sender simulator 配置

`fpga_sender_sim.example.json` 描述单个 Station 的 UDP/Project VDIF v1 发送实例：

- `destination` 只限定接收端数值 IPv4、UDP 端口和 IPv4 path MTU；程序不限定源地址；
- `station.station_id` 必须与 Observation 的 `station_ids` 中某一项一致；
- `packet` 给出频段、TFP 几何、8/16-bit 分量和十进制字符串 `sample_interval_ps`；
- 示例测试间隔为 `1000000 ps = 1 us`，每 packet 包含 512 个时间 sample；
- `time.mode` 是 `BURST` 或 `REALTIME`；多服务器 REALTIME 测试必须使用相同的未来
  `start_utc`、reference epoch、start seconds 和 group count；
- `faults` 可确定性注入 drop、duplicate 和 invalid-header group。

`fpga_sender_sim.paced.example.json` 是严格 schema v2 高速 UDP 配置：

- `source.ip/port` 必须显式给出，且每个 Station process 使用不同 source port；
- `time.mode` 固定为 `PACED`，所有发送端使用共同未来 `start_utc`；
- `transmit.target_gbps` 表示完整 UDP datagram payload（VDIF header + data）的 Gbps；
- `batch_packets` 取值 `1..64`，Linux 使用 `sendmmsg()`；
- `payload_mode` 为 `REPEAT_TEMPLATE` 或 `DETERMINISTIC`；
- 所有 Station 等速，控制端不支持权重或单 Station override。

schema v3 支持一个进程发送多个 Station，模拟发送配置必须与目标实例的频道范围和时间网格一致：

- `station.station_ids` 是 Observation 中 Station ID 的有序、非空、无重复子集；
- 每个 sender 配置使用一个 source IP/port 和一个 socket；多目标配置分别生成；
- `time.group_count` 是时间组数，总 record 数为 `group_count × station_ids.size()`；
- 组内发送全部本机 Station，下一组轮换起始 Station；
- `transmit.target_gbps` 仍是完整 VDIF record（32-byte header + data）的速率，不是
  仅天文采样 data 的速率。

`NPOL=2` 使用项目固定极化顺序 `X,Y`。Observation 无需重复配置标签；配置编译器
自动将 `POL_LABELS X,Y` 写入 RAW header，后续 unpack 和 GPU stage header 原样传播。

完整 `station_ids` 只保存在 Observation/Resolved Plan 中：sender 用它分片，unpack
用它建立 Station ID→ATFP A 轴映射。映射完成后的 ring header 不重复保存
`STATION_IDS`，只保存 `NANT` 和配置/几何身份；因此固定 4096-byte header 不随阵元数
线性膨胀。

IPv4 UDP record 必须满足：

```text
payload_bytes = T * F * P * 2 * (component_bits / 8)
record_bytes = 32 + payload_bytes
record_bytes <= path_mtu - 20 - 8
record_bytes <= 65507
```

fault 列表必须严格递增、不重复、不越界且彼此不重叠，避免同一 group 同时定义互相冲突
的发送行为。
