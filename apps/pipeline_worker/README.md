# pipeline_worker

`pipeline_worker` 是独立处理进程：它只读取由
`observation_config_compile` 生成的 `resolved_observation.json`，通过其中的
`rings.compute_key` 和 `rings.output_key` 连接一个输入 PSRDADA HDU 和一个输出
PSRDADA HDU。它先校验 input header 与编译器生成的 `unpacked.header` 完全一致，校验
两个实际 ring 的 header capacity、data block capacity 和 block 数，再将运行时生成的
output header 与编译器生成的最终 header 完全比对，然后逐个 data block 处理并传播 EOD。

算法模块不连接 ring。worker 持有 ring lock、host/device buffer、CUDA stream 和
模块生命周期。

可选参数 `--metrics-json PATH` 在 transfer 结束时原子写入结构化指标，包括处理 block
数、输入/输出 bytes、每 block service time、output-ring wait，以及 H2D、算法链、D2H
累计时间。stream 拓扑由 `processing.cuda_pipeline` 决定，不由 metrics 开关决定。
计数用于核对 compute/output block 闭合，阶段累计时间可能重叠，不能直接求和作为墙钟时间。

固定前缀为 `H2D -> ComplexConvert`：输入 ring 的块级 `ATFP/CI8` 先在 GPU 上融合
完成物理转置、CI8→CF32 和 scale，得到独立的 `TFPA/CF32` buffer，再进入以下算法链。

## 当前支持的模块链

配置的 `output.product` 选择以下一条固定合法链：

| product | 模块链 | 输出布局 |
| --- | --- | --- |
| `BEAMFORMED` | ComplexConvert → Beamform | `TFPB/CF32` |
| `POWER` | ComplexConvert → Beamform → Power | `TFPB/F32` |
| `STOKES` | ComplexConvert → Beamform → Stokes | `TFBS/F32`，产物顺序为 `AA,BB,AB_REAL,AB_IMAG` |

Power 和 Stokes 是互斥的兄弟分支，不会串联。Stokes 要求 `NPOL=2`；Project
Observation v1 的偏振顺序固定为 `X,Y`，配置编译器自动写入 `POL_LABELS X,Y`。
`integration.enabled=true` 时，Power 或 Stokes 后追加
TimeIntegrate；`BEAMFORMED` 不允许直接积分。

## 参数来源

| 来源 | 参数 |
| --- | --- |
| input header block | `NCHAN`、`NPOL`、`NANT`、数据阶段/顺序/类型、内存位置、采样/字节率、`UTC_START` 等观测元数据 |
| resolved observation plan | compute/output ring key、CPU/CUDA backend、CUDA device、`F/A/P/T`、整数转换 scale、权重文件/scale/id、算法链、积分长度/运算及全部派生 block/ring 几何 |
| 权重 NPY | `[F,P,A,B,2]`；`B` 维自动确定 `NBEAM`，F/P/A 和 dtype 必须与观测输入一致 |
| ring 实例 | input/output header capacity、data block capacity 和 block 数 |

header 不允许进程各自扩展：Observation 编译器通过生产模块的 header transform 生成
`unpacked.header`、`converted.header`、`beamformed.header` 和 `output.header`，worker
按这些字段逐项核对。算法变换会更新 stage、order、sample format、resolution、byte
rate、transfer/file size、offset、权重标识和执行后端。

## Input header 契约

worker 从 unpack 输出的 host compute ring 开始。必填字段为：

```text
DATA_STAGE=UNPACKED
ORDER=ATFP
LAYOUT_SCOPE=BLOCK
SAMPLE_FORMAT=CI8
SAMPLE_ENCODING=TWOS_COMPLEMENT
COMPONENT_ORDER=IQ
COMPONENT_NBIT=8
SAMPLE_NBIT=16
MEMORY=HOST 或 PINNED_HOST
NCHAN=F
NPOL=P
NANT=A
BLOCK_NTIME=T
RESOLUTION=F*P*A*2
RECORD_BYTES=OUTPUT_BLOCK_BYTES=T*F*P*A*2
BYTES_PER_SECOND=<正整数且为 RESOLUTION 的整数倍>
```

header 中的 `NCHAN/NANT/NPOL` 必须与 Resolved Plan 的 F/A/P 完全一致，且
`CONFIG_ID/GEOMETRY_ID` 必须匹配。Resolved Plan 是 block 规划依据，header 是上游
实际数据声明，任一不一致时都在获取 data block 前拒绝运行。

Stokes 还要求 `NPOL=2`、`POL_LABELS=<label0>,<label1>`。可选的
`TRANSFER_SIZE`、`FILE_SIZE` 和 `OBS_OFFSET` 如果存在，必须对一个完整 TFPA 时间帧
对齐；输出 header 按输入/输出 frame byte 比例缩放这些字段。启用积分时还必须有正数
有限值 `TSAMP`；所有上述 byte count 在产品变换和除以积分长度 `K` 时都必须能精确表示。
`TRANSFER_SIZE` 用于严格几何和输出字节规划；PSRDADA reader 本身始终读取到 compute
ring 的 EOD。这样当有限观测恰好结束于完整 block 边界时，不会因 reader 先达到声明
字节数、producer 后发布 EOD 而误开一个带非零 `OBS_OFFSET` 的伪 continuation transfer。

## 单一配置入口

用户只维护 [`config/observation.example.json`](../../config/observation.example.json)。
其中处理部分示例如下：

```json
{
  "rings": {
    "raw_key": "0x00d2",
    "compute_key": "0x00d4",
    "output_key": "0x00d6"
  },
  "processing": {
    "backend": "CUDA",
    "cuda_device": 0,
    "run_once": true,
    "cuda_pipeline": {
      "mode": "STAGED_PIPELINE",
      "inflight_blocks": 3
    },
    "conversion": {"scale": "0.0078125"},
    "modules": [
      {"type": "beamform", "weights_file": "weights/beamform.npy",
       "weights_order": "FPAB2", "weights_id": "observation-v1",
       "weights_scale": "0.0078125", "compute_mode": "TF32"},
      {"type": "stokes"},
      {"type": "integrate", "length": 128, "operation": "MEAN"}
    ],
    "output": {"sample_format": "AUTO"}
  }
}
```

`processing.cuda_pipeline.mode` 可选：

- `SYNCHRONOUS_DIRECT`：单 stream 基线，`inflight_blocks` 必须为 1；输入
  ring 直接 H2D，D2H 直接写 output ring。
- `STAGED_PIPELINE`：1–4 个有界 slot，每个 slot 独占 non-blocking CUDA
  compute stream、pinned output staging 和 device 中间 buffer，并共用独立 H2D stream；worker 在 transfer 生命周期
  用 `dada_cuda_dbregister` 注册 compute ring，H2D 直接读取 ring block；reader 按 block
  序号提交，单 writer 严格按序写 output ring，因此 CUDA 完成乱序不会造成
  ring block 乱序。

省略 `cuda_pipeline` 时保持兼容基线 `SYNCHRONOUS_DIRECT/1`。staged 模式的
slot wait、writer wait、完成乱序、ring 注册、显存和 pinned output 预算均写入 worker
metrics JSON；兼容字段 `input_staging_bytes` 在直注册路径必须为 0。

`conversion.scale` 必须显式配置为正有限十进制字符串，不由 CI8 自动推导。
第一版 `output.sample_format` 只允许 `AUTO`：Beamform-only 自动为 `CF32`，Power、
Stokes 及其积分输出自动为 `F32`。output ring 的 block 数复用 compute ring block 数，
block bytes 由权重 B 维、所选产品和积分长度自动计算。

worker 仅消费由 Observation 编译生成的 Resolved Plan；模块级测试通过内部 fixture 初始化 runtime structs。

`udp_packets_per_antenna_per_block` 表示每个阵元贡献的 UDP 包数，不是所有阵元合计
包数；所有阵元合计包数为 `A×udp_packets_per_antenna_per_block`。

## Data block 几何

```text
T                       = samples_per_udp
                          * udp_packets_per_antenna_per_block
udp_antenna_group_bytes = udp_payload_bytes * A
input_frame_bytes       = F * P * A * sizeof(CI8 complex)
input_ring_block_bytes  = T * input_frame_bytes
converted_frame_bytes   = F * P * A * sizeof(CF32)
converted_block_bytes   = T * converted_frame_bytes
beamformed_frame_bytes  = F * P * B * sizeof(CF32)
power_frame_bytes       = F * P * B * sizeof(F32)
stokes_frame_bytes      = F * B * 4 * sizeof(F32)

product_block_bytes     = T * selected_output_frame_bytes
T_out                   = integration.enabled ? T/K : T
output_ring_block_bytes = T_out * selected_output_frame_bytes
input_ring_block_bytes % udp_antenna_group_bytes = 0
```

启用积分时还要求 `T % K = 0`。单进程中间 scratch 为 Beamform block；启用积分时
还需追加一个未积分 product block：

```text
scratch_block_bytes = beamformed_block_bytes + product_block_bytes
```

输入 `F×A×P×T` 的物理线性顺序为块级 `ATFP`；转换后的独立 buffer 才是 `TFPA`。
input/output ring 的 4096-byte header capacity、data block capacity 和 block 数必须与
Resolved Plan 完全相等，否则 worker 在取得 input data block 前退出，避免错误 ring 或
short block 被 PSRDADA 当作有效观测数据。

## 构建与运行

```bash
cmake -S . -B build-linux \
  -DBUILD_RDMA_PIPELINE=ON \
  -DUSE_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build-linux --parallel

./build-linux/observation_config_compile \
  --config config/observation.json \
  --output-dir run/observation
./build-linux/pipeline_worker \
  run/observation/resolved_observation.json
```

使用生成目录内的 `ring_plan.json` 创建 raw、compute 和 output ring；worker 只连接
compute 与 output 两个 ring。`run_once=true`
时完成一个 input transfer/EOD 后退出；为 `false` 时重新获取 input read lock，等待下一次
transfer。`SIGINT`/`SIGTERM` 会请求在当前 PSRDADA 操作返回后停止。

## CUDA 调度与所有权

- direct/1 使用单 stream，处理完成后再提交 output 并释放 input。
- staged/N 共用一条 non-blocking H2D stream，每 slot 有独立 compute stream；
  对应 compute 等待该 block 的 H2D event，D2H 跟随其 compute。
- N≥2 使用 `ONE_BLOCK_H2D_LOOKAHEAD`：先排入下一 block H2D，再提交前一 block
  compute/D2H；N=1 使用单 slot 顺序策略。输入 lease 只在本 block H2D 完成后释放。
- 每个 slot 独占 device buffers、pinned output 和事件。单 writer 按 sequence 等待
  完成并复制到 output ring；slot 不因 CUDA 乱序完成而提前复用。
- Drain 提交最后一个待计算 block，完成在途任务后发布 EOD；Abort 同样必须等待
  所有仍引用 buffer/event 的 stream 结束，才能销毁资源。

## GPU block deadline 与容量预检

实例 Observation 编译时生成 `validation_report.json.gpu_pipeline_budget`，
记录各阶段 block bytes、到达间隔、service deadline、device/pinned 内存及建议空闲显存。
正常预算速率从本实例采样几何派生；压力测试覆盖速率必须明确记录为测试输入。

host ring 容量由 `ring_plan.json` 给出，不计入显存；增加 ring 仅提供突发缓冲，
不能弥补长期服务能力不足。编译预算通过不代表真实吞吐验收通过。

direct/1 的 `blocks`、input/output bytes 和 CUDA totals 可用于闭合；
submitted/completed/published、max-inflight 和 active-window 属于 staged 调度指标，
direct 中为 0 不表示未处理数据。

macOS 只构建可移植 worker core；PSRDADA/CUDA 可执行文件需在 Linux 服务器构建。
