# Tests

本目录包含 pipeline 配置、header、模块契约及 CUDA 算法的回归测试。通常应通过
CTest 调用；直接运行测试程序主要用于定位单项失败。

本分支多服务器接口和阶段验收见[配置指南](../docs/control/configuration.md)、
[生命周期](../docs/control/lifecycle.md)及[台账](../docs/control/task-ledger.md)。
下文 Task 8C 是单实例数据面测试工具，不是集中观测控制服务。
历史 suite 不作为本分支的重新验收；远端隔离按
[当前路线图](../docs/superpowers/plans/2026-09-12-multi-server-control-roadmap.md)执行。

## 构建测试

macOS 或不带 CUDA/PSRDADA 的便携测试构建：

```bash
cmake -S . -B build \
  -DBUILD_TESTING=ON \
  -DBUILD_RDMA_PIPELINE=OFF \
  -DUSE_CUDA=OFF
cmake --build build --parallel
ctest --test-dir build --output-on-failure
```

RTX 3090 CUDA 算法测试构建：

```bash
cmake -S . -B build-cuda \
  -DBUILD_TESTING=ON \
  -DBUILD_RDMA_PIPELINE=OFF \
  -DBUILD_SSD_BENCHMARK=OFF \
  -DUSE_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build-cuda --parallel
ctest --test-dir build-cuda -L cuda --output-on-failure
```

包含 PSRDADA/RDMA adapter 测试时使用：

```bash
cmake -S . -B build-linux \
  -DBUILD_TESTING=ON \
  -DBUILD_RDMA_PIPELINE=ON \
  -DUSE_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build-linux --parallel
ctest --test-dir build-linux --output-on-failure
```

最后一种构建要求 Linux、PSRDADA、libibverbs 和 CUDA Toolkit 均已安装。

## 测试总表

| CTest 名称 | 来源 | 功能 | 构建条件 |
| --- | --- | --- | --- |
| `rdma_receive_policy_test` | `rdma_receive_policy_test.cpp` | 不依赖 NIC 地验证仅目的端 MAC/IP/UDP 精确匹配、所有源字段通配、错误长度可恢复分类、致命 CQ 分类、1 秒 drain/4096 次空轮询计时门控/期间 WR 重投递，以及 zero/one/batch/partial raw tail 的完整-record 发布判定 | `BUILD_TESTING=ON` |
| `project_vdif_v1_test` | `project_vdif_v1_test.cpp` | 对固定 32-byte Project VDIF v1 header 做 little-endian golden decode/encode，并校验 CI8/CI16 record 几何和保留字段错误路径 | `BUILD_TESTING=ON` |
| `vdif_unpack_config_test` | `vdif_unpack_config_test.cpp` | 校验 ring key、Station ID→A 映射、两 block payload-only 窗口几何、profile 冲突、内存上限与溢出错误路径 | `BUILD_TESTING=ON` |
| `vdif_unpack_header_test` | `vdif_unpack_header_test.cpp` | 校验 RAW→UNPACKED header 转换、观测 timeline 原样传播、按 `EXPECTED_GROUPS` 计算 `TRANSFER_SIZE`、未知字段保留、block-scoped ATFP/无包头几何及输入冲突不发布输出 | `BUILD_TESTING=ON` |
| `vdif_timeline_test` | `vdif_timeline_test.cpp` | 校验整数皮秒 group 时间轴解析、非整数 groups/s 跨秒、ordinal↔VDIF seconds/frame 严格逆映射、exclusive stop boundary、字段范围及算术溢出 | `BUILD_TESTING=ON` |
| `vdif_unpack_engine_test` | `vdif_unpack_engine_test.cpp` | 保留旧 TFP→TFPA scatter 作为正确性参考，校验任意 Station 顺序、跨 block、零填充及错误统计 | `BUILD_TESTING=ON` |
| `vdif_atfp_engine_test` | `vdif_atfp_engine_test.cpp` | 校验固定环形 slot、直接 Station lookup、每包单次 payload memcpy、ATFP block view、多 Station 最小 watermark、领先 Station 不淘汰落后数据、完全缺失/部分缺失补零、large gap、Station skew/raw-block 组成统计、固定内存 raw-block service/arrival 直方图、wrap/partial EOD 及与旧 TFPA reference 的等价性 | `BUILD_TESTING=ON` |
| `atfp_block_writer_test` | `atfp_block_writer_test.cpp` | 验证每个 compute block 只 Acquire/Commit 一次、每个 A 平面无回绕一次复制/回绕最多两段复制、partial block 紧凑布局及 sink 失败路径 | `BUILD_TESTING=ON` |
| `group_block_writer_test` | `group_block_writer_test.cpp` | 用内存 block sink 验证有序 group 直接填充满 block、EOD 精确提交部分 block、空传输不提交，以及 group/sink 容量错误不发布数据 | `BUILD_TESTING=ON` |
| `vdif_sender_sim_test` | `vdif_sender_sim_test.cpp` | 校验严格 schema v1/v2/v3 sender JSON、单/多 Station、显式 source、PACED/batch/payload mode、整数皮秒跨秒/frame 重置、CI8/CI16、MTU 和 fault | `BUILD_TESTING=ON` |
| `vdif_sender_rate_test` | `vdif_sender_rate_test.cpp` | 校验等速 Station 整数分配、固定点累计字节 deadline、零值和 uint64 overflow | `BUILD_TESTING=ON` |
| `vdif_sender_batch_test` | `vdif_sender_batch_test.cpp` | 校验固定 packet pool 地址、连续 header、Station 模板、按时间组轮换 Station、O(1) Station 索引、repeat payload 和 deterministic reference 一致性 | `BUILD_TESTING=ON` |
| `udp_vdif_sender_test` | `udp_vdif_sender_test.cpp` | 校验最终 sender JSON 统计可解析且 packet/byte/backend/source/payload prefix 字段一致 | `BUILD_TESTING=ON` |
| `fpga_sender_sim_loopback_test` | `fpga_sender_sim_loopback_test.py` | 在 127.0.0.1 验证 schema v1 fault、schema v2 单 Station PACED，以及 schema v3 单 socket/固定 source port 多 Station 轮换顺序与逐 Station 统计 | 找到 Python 3 |
| `fpga_sender_sim_linux_batch_test` | `fpga_sender_sim_linux_batch_test.py` | Linux loopback 验证 4 Station/64 packet、16-packet batch、固定 source port、轮换顺序、逐 Station 计数及 `SENDMMSG` backend | Linux 且找到 Python 3 |
| `gpu_pressure_fixture_test` | `gpu_pressure_fixture_test.py` | 生成并校验生产压力几何 Power `A=469,F=4,P=1,B=350` 与 coherency `A=469,F=2,P=2,B=350` 的确定性 FPAB2 int8 NPY 权重；同一产品可生成只改变 CUDA 执行模式的 `SYNCHRONOUS_DIRECT/1` 与 `STAGED_PIPELINE/N` Observation；编译器门禁核对五个 4096-byte stage headers、`NANT=469`、固定偏振顺序、各级 block bytes 及积分输出 block | 找到 Python 3；注册 CTest 还传入 `observation_config_compile`；不连接远端服务器 |
| `coherency_numerical_evidence_test` | `coherency_numerical_evidence_test.py` | 验证 Stokes CPU/CUDA 测试写出的机器可读 shape/bytes、`AA/BB/AB_REAL/AB_IMAG`、容差、max abs/rel error、NaN/Inf 与 reference-only I/Q/U/V 推导误差；不改变工程输出契约 | CPU：`BUILD_TESTING=ON`；CUDA：`USE_CUDA=ON` |
| `generate_source_manifest_test` | `generate_source_manifest_test.py` | 验证不依赖 Git 的确定性源码 manifest allowlist、排序与排除规则 | 找到 Python 3 |
| `pipeline_worker_metrics_test` | `pipeline_worker_metrics_test.cpp` | 验证 GPU worker block/byte/service/output-wait/CUDA timing，以及 staged submitted/completed/published、inflight、乱序、slot/writer wait、staging copy 和内存预算聚合与原子 JSON 落盘 | `BUILD_TESTING=ON` |
| `observation_config_test` | `observation_config_test.cpp` | 校验统一观测 JSON、精确皮秒时长、Station/A 轴顺序、metadata、ring/receiver 参数、相对路径、算法顺序及严格缺失/未知字段拒绝 | `BUILD_TESTING=ON` |
| `resolved_observation_plan_test` | `resolved_observation_plan_test.cpp` | 从统一观测配置、wire profile 和 `[F,P,A,B,2]` 权重精确派生 ingest 及 Beamform/Power/Stokes/Integration 的 block/output-ring 几何，并校验溢出、整除和权重 F/P/A 边界 | `BUILD_TESTING=ON` |
| `beamform_weight_metadata_test` | `beamform_weight_metadata_test.cpp` | 校验 NPY v1/v2、C-order、`|i1`/`<i2`、严格 `[F,P,A,B,2]`、B/NBEAM 推导及 payload/trailing byte 检查 | `BUILD_TESTING=ON` |
| `config_identity_test` | `config_identity_test.cpp` | 校验标准 SHA256、配置规范化、路径无关的 CONFIG_ID/GEOMETRY_ID、权重内容摘要、resolved plan 往返，以及派生几何、最终输出契约和 ID 篡改拒绝 | `BUILD_TESTING=ON` |
| `observation_artifacts_test` | `observation_artifacts_test.cpp` | 校验统一观测配置生成全部 stage header/manifest/ring/report，并精确验证 Beamform/Power/Stokes/Integration 的 20% GPU deadline、逐级及 H2D+D2H 合计吞吐、buffer/权重显存与溢出拒绝 | `BUILD_TESTING=ON` |
| `observation_config_compile_test` | `observation_config_compile_test.py` | 验证 compiler preflight、`--budget-payload-gbps` 双速率来源、无输出副作用、原子 artifact 目录、SHA256 manifest、无覆盖和无效配置拒绝 | 找到 Python 3 |
| `pipeline_config_test` | `pipeline_config_test.cpp` | 用内部 fixture 校验 record/block/ring/file/rate 几何、溢出和 DADA header 派生值 | `BUILD_TESTING=ON` |
| `packet_format_config_test` | `packet_format_config_test.cpp` | 加载 schema-v2、固定 32-byte/8-word Project VDIF wire profile，逐字段校验 bit layout、TFP axis、HEADER/DERIVED/LOOKUP 引用，并拒绝 schema-v1 `payload_bytes`、观测轴常量和 downstream `output_order` | `BUILD_TESTING=ON` |
| `packet_format_inspect_test` | `packet_format_inspect_test.py` | 检查 profile inspect 的 32-byte、TWOS_COMPLEMENT、IQ 和 axis 输出，并确认未知字段、旧 signed 字段和 64-byte header 被拒绝 | 找到 Python 3 |
| `pipeline_core_test` | `pipeline_core_test.cpp` | 验证 `AlgorithmModule` 的 header 传播、block view、sequence 和 host execution context | `BUILD_TESTING=ON` |
| `beamform_module_test` | `beamform_module_test.cpp` | 验证 CPU reference、NPY int8/int16 权重、scale、TFPA→TFPB、shape 和容量错误路径 | `BUILD_TESTING=ON` |
| `beamform_test_weights_generator_test` | `beamform_test_weights_generator_test.py` | 验证 CUDA Beamform 测试权重生成器输出的 NPY 版本、shape、dtype、order、scale 和已知系数 | 找到 Python 3 |
| `beamform_cuda_fp32_test` | `beamform_cuda_test.cpp` | 在 CUDA stream 上验证异步 FP32 batched complex GEMM，以及 T/F batch stride | `USE_CUDA=ON` |
| `beamform_cuda_tf32_test` | `beamform_cuda_test.cpp` | 使用相同已知结果验证 TF32/Tensor Core 配置路径 | `USE_CUDA=ON`，GPU CC ≥ 8.0 |
| `complex_convert_module_test` | `complex_convert_module_test.cpp` | 验证 CPU reference 的块级 ATFP CI8/CI16→物理 TFPA CF32 转置、partial T、scale、header/byte 几何、非法格式、容量及非重叠约束 | `BUILD_TESTING=ON` |
| `complex_convert_cuda_test` | `complex_convert_cuda_test.cpp` | 在 caller-owned non-default stream 上验证融合 ATFP→TFPA 转置、CI8/CI16→CF32、scale、partial/non-tile/大矩阵、CPU oracle 精确一致及错误 context | `USE_CUDA=ON` |
| `power_module_test` | `power_module_test.cpp` | 验证 CPU reference 的 `TFPB/CF32→TFPB/F32`、header/byte rate、两帧数值结果和错误路径 | `BUILD_TESTING=ON` |
| `power_cuda_test` | `power_cuda_test.cpp` | 在 worker 风格 non-blocking stream 上验证异步 Power kernel 与 CPU 已知结果一致 | `USE_CUDA=ON` |
| `stokes_module_test` | `stokes_module_test.cpp` | 验证 CPU reference 的 `TFPB/CF32→TFBS/F32`、四个相关产物、双偏振约束、header 和错误路径；`--result-json` 生成数值证据 | `BUILD_TESTING=ON` |
| `stokes_cuda_test` | `stokes_cuda_test.cpp` | 在 non-blocking stream 上验证异步 Stokes kernel 与 CPU 已知结果一致；`--result-json` 生成数值证据 | `USE_CUDA=ON` |
| `time_integrate_module_test` | `time_integrate_module_test.cpp` | 验证 TFPB/TFBS 的 SUM/MEAN、累计积分 header、block 缩短、容量/整除/重叠等错误路径 | `BUILD_TESTING=ON` |
| `time_integrate_cuda_test` | `time_integrate_cuda_test.cpp` | 在 worker 风格 non-blocking stream 上验证异步时间积分与已知结果一致 | `USE_CUDA=ON` |
| `transfer_cuda_roundtrip_test` | `transfer_cuda_roundtrip_test.cpp` | 将确定性字节块依次通过 H2D 和 D2H，检查 header、size、sequence 与最终逐字节结果 | `USE_CUDA=ON` |
| `pipeline_worker_cuda_chain_test` | `pipeline_worker_cuda_chain_test.cpp` | 在一条 non-blocking stream 上验证 H2D→ATFP 转置/转换→Beamform→Power→TimeIntegrate→D2H、独立 buffer、scratch/output 几何、header、sequence 和手算结果 | `USE_CUDA=ON` |
| `pipeline_worker_cuda_products_test` | `pipeline_worker_cuda_products_test.cpp` | 使用非对称 `T=2,F=2,P=2,A=2,B=2` CI8 ATFP 输入，逐项验证 H2D→转换→Beamform-only/Power/Stokes→D2H 的轴顺序、header、size、sequence 和手算数值 | `USE_CUDA=ON` |
| `gpu_block_pipeline_cuda_test` | `gpu_block_pipeline_cuda_test.cpp` | 对 staged slots=1/2/3/4 验证 pinned staging→多 non-blocking stream→Power+Integration→单 writer 严格按序发布、手算数值、生命周期/byte 计数闭合及 commit 失败传播 | `USE_CUDA=ON` |
| `ordered_slot_scheduler_test` | `ordered_slot_scheduler_test.cpp` | 不依赖 CUDA 地验证有界 slot 压力、乱序完成/有序发布、slot 复用、stale lease 和首错截断 | `BUILD_TESTING=ON` |
| `pipeline_worker_core_test` | `pipeline_worker_core_test.cpp` | 验证内部 runtime fixture、ASCII header、block/scratch 规划，以及 Power/Stokes 后积分的 header 和手算数值 | `BUILD_TESTING=ON` |
| `worker_resolved_plan_test` | `worker_resolved_plan_test.cpp` | 验证 worker 只从 Resolved Plan 获得 ring key、F/A/P/T、可配置 conversion scale、NPY B/NBEAM、Beamform-only/Power/Stokes/积分链和最终 block 几何，并拒绝 stale geometry 与变更后的权重 digest | `BUILD_TESTING=ON` |
| `pipeline_worker_resolved_integration_test` | `pipeline_worker_resolved_integration.sh` | 从 Observation 编译 plan，使用每次运行独立的受控 ring key，运行 compute ring→CUDA worker→output ring，验证完整编译 header、Power+Integration 数值、EOD、错误 input header、ring capacity 门禁和定向清理 | Linux、`BUILD_RDMA_PIPELINE=ON`、`USE_CUDA=ON`、PSRDADA CLI；缺少时 skip 77 |
| `dada_header_roundtrip_test` | `dada_header_roundtrip_test.cpp` | 从统一 observation 配置生成 RAW header，经 PSRDADA ASCII codec 验证 Project VDIF 几何、CONFIG_ID、GEOMETRY_ID 及版本拒绝 | `BUILD_RDMA_PIPELINE=ON` |
| `vdif_unpack_worker_integration_test` | `vdif_unpack_worker_integration.sh` | 从统一 observation 编译 plan/header/ring 几何，验证 full/partial/完全缺失 group、连续双 transfer、ATFP 字节及 EOD，并拒绝错误 CONFIG_ID 和 raw/compute ring block capacity | `BUILD_RDMA_PIPELINE=ON` 且具备 PSRDADA CLI；缺少时 skip 77 |

多服务器配置、bundle 和编译器衔接测试见[配置指南](../docs/control/configuration.md)；
控制器、store、transport 测试见[生命周期指南](../docs/control/lifecycle.md)。
以下只列所复用数据面的回归调用，不代表已完成多实例集成验收。

## 单项调用

### RDMA direct raw 接收策略

可移植策略测试：

```bash
./build/rdma_receive_policy_test
ctest --test-dir build -R '^rdma_receive_policy_test$' --output-on-failure
```

真实 NIC/PSRDADA 三机测试统一由 HF 上的版本化
`scripts/task8c_rate_point.py` 驱动；qths1 不反向 SSH 到 sender。该控制器分别连接接收端和两个发送端，并覆盖有限传输
CQ tail、partial raw block、unpack 计数守恒和 compute 输出验证。

### `pipeline_config_test`

```bash
./build/pipeline_config_test
ctest --test-dir build -R '^pipeline_config_test$' --output-on-failure
```

### `observation_config_test`

```bash
./build/observation_config_test config/observation.example.json
ctest --test-dir build -R '^observation_config_test$' --output-on-failure
```

### `config_identity_test`

```bash
./build/config_identity_test \
  config/observation.example.json \
  config/packet_formats/frontend.example-v1.json
ctest --test-dir build -R '^config_identity_test$' --output-on-failure
```

### `observation_artifacts_test`

```bash
./build/observation_artifacts_test config/observation.example.json
python3 tests/observation_config_compile_test.py \
  build/observation_config_compile config/observation.example.json \
  config/testing/atfp-throughput-observation.json
ctest --test-dir build \
  -R '^(observation_artifacts_test|observation_config_compile_test)$' \
  --output-on-failure
```

### Packet format profile 测试

```bash
./build/project_vdif_v1_test

./build/packet_format_config_test \
  config/packet_formats/frontend.example-v1.json

./build/packet_format_inspect \
  config/packet_formats/frontend.example-v1.json

ctest --test-dir build \
  -R '^packet_format_(config|inspect)_test$' --output-on-failure
```

该 profile 是 Project VDIF v1 的机器可读 wire contract；schema v2 不再保存
`payload_bytes`，实际观测几何由统一观测配置计算并与 packet header 逐包交叉校验。

### VDIF unpack 配置与 header 测试

```bash
./build/vdif_unpack_config_test \
  config/observation.example.json \
  config/packet_formats/frontend.example-v1.json
./build/vdif_unpack_header_test
./build/vdif_unpack_engine_test
./build/vdif_atfp_engine_test
./build/atfp_block_writer_test
ctest --test-dir build \
  -R '^(vdif_(unpack_(config|header|engine)|atfp_engine)|atfp_block_writer)_test$' \
  --output-on-failure
```

配置测试用内部 fixture 验证 Station/window 边界，并从 Observation→ResolvedPlan 验证
实际 runtime 的 ring、身份和 payload-only 窗口；header 测试验证 compute header 更新前会先完整检查 raw header
几何与两个 identity，失败时不会覆盖调用者已有 metadata。
engine 测试使用独立生成的 40-byte 小型 Project VDIF record。旧 engine 保留 TFPA
reference；新 engine 以手工 ATFP oracle 验证位置、完全缺失 group、缺失 Station 零区间，
并在测试中独立转置后与旧 TFPA 结果逐字节比较，不依赖 UDP/RDMA adapter。

Linux 上的真实双 ring 测试：

```bash
tests/vdif_unpack_worker_integration.sh \
  build-linux/vdif_unpack_worker \
  build-linux/observation_config_compile "$(pwd)"
ctest --test-dir build-linux \
  -R '^vdif_unpack_worker_integration_test$' --output-on-failure
```

该脚本的测试 sample 间隔固定为 `1 us`，分别验证末尾为完整 compute block、partial
compute block、完全缺失 group，以及 `processing.run_once=false` 时同一 worker 的两个
连续 transfer；同时验证错误 identity 和 ring block capacity 会在数据处理前失败。缺少
`dada_db`、`dada_diskdb`、`dada_dbdisk` 等工具时返回 CTest skip code 77。

### Compute group block writer 测试

```bash
./build/group_block_writer_test
ctest --test-dir build -R '^group_block_writer_test$' --output-on-failure
```

该测试使用真实内存作为非 owning sink 边界，验证五个 4-byte group 依序形成一个
12-byte 满 block 和一个 8-byte EOD block。未来 PSRDADA adapter 只需实现相同的
`Acquire/Commit` 接口，writer 本身不申请或持有 ring block 存储。

### FPGA sender simulator 测试

```bash
./build/vdif_sender_sim_test config/fpga_sender_sim.example.json
python3 tests/fpga_sender_sim_loopback_test.py \
  build/fpga_sender_sim config/fpga_sender_sim.example.json
ctest --test-dir build \
  -R '^(vdif_sender_sim|fpga_sender_sim_loopback)_test$' \
  --output-on-failure
```

loopback 测试使用操作系统分配的临时端口，不依赖固定端口或外部网络；若执行环境禁止
创建本地 socket，需要为该测试开放 `127.0.0.1` 回环权限。

### Observation 配置编译检查

pipeline/unpack/worker 的独立 JSON/CONF、inspect 和 converter 已移除。
外部入口统一为 Observation→ResolvedPlan；几何测试使用内部 C++ fixture。
生成的 artifacts/临时 NPY 放在 build 或测试临时目录，不能覆盖真实输入或权重。
冻结 manifest、accepted-results 和历史验收记录保留原 snapshot 引用。

```bash
./build/observation_config_compile \
  --config config/observation.example.json --preflight-only
ctest --test-dir build \
  -R '^(pipeline_config|vdif_unpack_config|pipeline_worker_core|observation_config|resolved_observation_plan|config_identity|observation_artifacts|observation_config_compile|worker_resolved_plan)_test$' \
  --output-on-failure
```

### `pipeline_core_test`

```bash
./build/pipeline_core_test
ctest --test-dir build -R '^pipeline_core_test$' --output-on-failure
```

### `beamform_module_test`

```bash
./build/beamform_module_test build/manual_beamform_weights.npy
ctest --test-dir build -R '^beamform_module_test$' --output-on-failure
```

参数是测试临时权重文件路径。测试会在该路径创建 `.npy` 并在结束时删除它，因此
不能传入真实权重文件。

### CUDA Beamform 测试

仓库提供一个持久化的已知权重文件：

```text
tests/data/beamform_cuda_weights_f2_p1_a2_b2_i8.npy
ORDER=FPAB2
shape=[F=2,P=1,A=2,B=2,real_imag=2]
dtype=int8
WEIGHTS_SCALE=0.5
```

需要重新生成时执行：

```bash
python3 scripts/generate_beamform_test_weights.py \
  tests/data/beamform_cuda_weights_f2_p1_a2_b2_i8.npy
```

```bash
./build-cuda/beamform_cuda_test \
  tests/data/beamform_cuda_weights_f2_p1_a2_b2_i8.npy FP32

./build-cuda/beamform_cuda_test \
  tests/data/beamform_cuda_weights_f2_p1_a2_b2_i8.npy TF32

ctest --test-dir build-cuda -L cuda --output-on-failure
```

也可以分别运行：

```bash
ctest --test-dir build-cuda \
  -R '^beamform_cuda_fp32_test$' --output-on-failure

ctest --test-dir build-cuda \
  -R '^beamform_cuda_tf32_test$' --output-on-failure
```

CUDA 测试创建一个 worker-owned non-blocking stream，将输入异步拷入显存，调用
Beamform，再在同一 stream 上异步拷回结果。测试覆盖 `T=2、F=2、P=1、A=2、B=2`
的已知复数结果。没有可用 CUDA device 时返回 77，CTest 将其标记为 skipped。

如果传入路径已经存在，CUDA 测试只读取并保留该文件；如果路径不存在，则创建同样的
临时 fixture，并在测试结束时删除。

### Complex conversion 测试

CPU reference：

```bash
./build/complex_convert_module_test
ctest --test-dir build \
  -R '^complex_convert_module_test$' --output-on-failure
```

CUDA：

```bash
./build-cuda/complex_convert_cuda_test
ctest --test-dir build-cuda \
  -R '^complex_convert_cuda_test$' --output-on-failure
```

CUDA 融合转置的诊断 benchmark（H2D 与 kernel 分开计时）：

```bash
./build-cuda/complex_convert_transpose_cuda_benchmark \
  CI8 256 65536 200 1.0 0
```

两者使用独立写死的 CI8/CI16 输入与 CF32 expected 值，覆盖有符号边界、显式 scale、
frame 扩展和 sequence。CUDA 测试通过同一条 non-blocking stream 完成 H2D、转换和
D2H，仅在读取结果前同步；没有 CUDA device 时返回 77。

### Power 测试

CPU reference：

```bash
./build/power_module_test
ctest --test-dir build -R '^power_module_test$' --output-on-failure
```

CUDA：

```bash
./build-cuda/power_cuda_test
ctest --test-dir build-cuda -R '^power_cuda_test$' --output-on-failure
```

两个测试使用相同的 `T=2、F=2、P=2、B=2` 已知输入。CUDA 测试通过同一条
non-blocking stream 完成 H2D、Power kernel 和 D2H；没有可用 CUDA device 时返回
77，由 CTest 标记为 skipped。

### Stokes 测试

CPU reference：

```bash
./build/stokes_module_test
./build/stokes_module_test --result-json /tmp/stokes-cpu.json
ctest --test-dir build -R '^stokes_module_test$' --output-on-failure
ctest --test-dir build -R '^coherency_numerical_evidence_test$' --output-on-failure
```

CUDA：

```bash
./build-cuda/stokes_cuda_test
./build-cuda/stokes_cuda_test --result-json /tmp/stokes-cuda.json
ctest --test-dir build-cuda -R '^stokes_cuda_test$' --output-on-failure
ctest --test-dir build-cuda \
  -R '^coherency_cuda_numerical_evidence_test$' --output-on-failure
```

两个测试使用相同的 `T=2、F=2、P=2、B=2` 输入，逐项检查
`AA、BB、AB_REAL、AB_IMAG`。CUDA 测试使用 worker 风格 non-blocking stream；
数值证据另外记录 shape/bytes、abs/rel tolerance、max error、NaN/Inf，并只在
reference check 中推导 I/Q/U/V。没有可用 CUDA device 时返回 77，由 CTest 标记为
skipped。

### H2D/D2H round-trip 测试

```bash
./build-cuda/transfer_cuda_roundtrip_test
ctest --test-dir build-cuda \
  -R '^transfer_cuda_roundtrip_test$' --output-on-failure
```

测试建立一个 512-byte、8 个 `RESOLUTION` frame 的确定性输入，经同一条
non-blocking stream 依次调用 `HostToDeviceModule` 和 `DeviceToHostModule`，只在链末
同步一次。除逐字节比较外，还检查两级 header 的科学字段不变、`MEMORY` 正确变化，
以及 block `size/sequence` 保持一致。没有 CUDA device 时返回 77。

### CUDA worker 组合链测试

```bash
./build-cuda/pipeline_worker_cuda_chain_test \
  tests/data/beamform_cuda_weights_f2_p1_a2_b2_i8.npy

ctest --test-dir build-cuda \
  -R '^pipeline_worker_cuda_(chain|products)_test$' --output-on-failure

./build-cuda/pipeline_worker_cuda_products_test \
  build-cuda/manual_pipeline_worker_p2_weights.npy
```

该测试不连接 PSRDADA，直接按 worker 的真实调用顺序执行：

```text
Host -> H2D -> ComplexConvert -> Beamform -> Power
     -> TimeIntegrate(K=2,MEAN) -> D2H -> Host
```

输入为 `T=2,F=2,P=1,A=2`，输出为 `T=1,F=2,P=1,B=2`，手算结果固定为
`[17.5,66,175,9]`。测试检查 96-byte scratch、16-byte output、最终 header 和
sequence，并且只在完整链末尾同步一次 stream。没有可用 CUDA device 时返回 77。

`pipeline_worker_cuda_products_test` 使用 block-scoped `ATFP/CI8` 输入和
`FPAB2` 单位选通权重，在同一 worker-owned stream 上分别运行 Beamform-only、Power
和 Stokes 三种链。输入的每个 T/F/P/A 复数值均不对称，输出按手算的 TFPB/TFBS
字面量逐项比较，因此任一 T/F/P/A/B 轴错位都会失败。测试生成并清理自己的 P=2 NPY
fixture，不连接 PSRDADA ring。

### Resolved Plan + PSRDADA + CUDA worker 集成测试

该测试已注册为 `pipeline_worker_resolved_integration_test`。它必须使用配置时发现并记录的
PSRDADA 绝对安装目录，不能依赖非登录 shell 的 `PATH`：

```bash
PKG_CONFIG_PATH=/home/user/psrdada/bin \
/home/user/wy/tools/cmake-3.31.12/bin/cmake \
  -S . -B build-worker-acceptance \
  -DBUILD_TESTING=ON -DBUILD_RDMA_PIPELINE=ON -DUSE_CUDA=ON \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 \
  -DPSRDADA_RUNTIME_BIN_DIR=/home/user/psrdada/bin

PIPELINE_TEST_RESULT_ROOT=/home/user/wy/pipeline-worker-results \
/home/user/wy/tools/cmake-3.31.12/bin/ctest \
  --test-dir build-worker-acceptance \
  -R '^pipeline_worker_resolved_integration_test$' --output-on-failure
```

设置 `PIPELINE_TEST_RESULT_ROOT` 时，每次运行会保存独立目录，其中含配置、编译 artifact、
ring/worker/reader 日志、`run_manifest.json` 和 `result.json`；不设置时仅使用并清理本轮
`mktemp` 目录。脚本只销毁已确认由本轮 `dada_db` 进程创建的动态 key，不清理固定 key
或其他测试资源。最终验收连续运行三次，并逐次保留结果。

### Time integration 测试

CPU reference：

```bash
./build/time_integrate_module_test
ctest --test-dir build \
  -R '^time_integrate_module_test$' --output-on-failure
```

CUDA：

```bash
./build-cuda/time_integrate_cuda_test
ctest --test-dir build-cuda \
  -R '^time_integrate_cuda_test$' --output-on-failure
```

CPU 测试覆盖 `TFPB/MEAN`、`TFBS/SUM`、二次积分累计长度、header 字节率和时间更新，
以及缺字段、错误 frame、`T % K != 0`、输出过小、输入输出重叠和 backend 不匹配。
CUDA 测试在 worker-owned non-blocking stream 上执行 H2D、积分和 D2H，只在读取结果前
同步；没有可用 CUDA device 时返回 77。

### `dada_header_roundtrip_test`

```bash
./build-linux/dada_header_roundtrip_test config/observation.example.json
ctest --test-dir build-linux \
  -R '^dada_header_roundtrip_test$' --output-on-failure
```

该测试链接 PSRDADA header API，因此不会出现在 macOS/portable build 中。

### `pipeline_worker_core_test`

```bash
./build/pipeline_worker_core_test \
  build/manual_pipeline_worker_weights.npy

ctest --test-dir build \
  -R '^pipeline_worker_core_test$' --output-on-failure
```

唯一参数是测试临时 NPY 权重路径，测试结束时会删除，不能指向真实权重文件。
该测试不连接 PSRDADA，因此可在 macOS 检查内部 geometry/header/block/module-chain 逻辑。
当前覆盖 Beamform、Power、Stokes，以及 Power/MEAN 和 Stokes/SUM 两条积分组合链。

## 常用过滤方式

```bash
# 列出当前 build 注册的测试
ctest --test-dir build -N

# 运行所有 Beamform 测试
ctest --test-dir build-cuda -R 'beamform' --output-on-failure

# 只运行带 cuda label 的测试
ctest --test-dir build-cuda -L cuda --output-on-failure

# 显示更完整的测试命令和输出
ctest --test-dir build -V
```
