# RDMA/PSRDADA 实时天线阵列处理系统

本项目面向连续天文观测数据，结合 RDMA 接收、PSRDADA 共享内存 ring 和
CUDA 计算，实现多阵元数据的并行解包、波束形成、功率或偏振相关乘积计算及积分。

系统以 Observation 为统一配置入口，以服务器/GPU 为处理单元按频段分工。
数据处理与上位控制分层：计算流水线负责本地实时处理，控制层负责全局配置、
集中启停和业务状态管理。各功能的实际支持范围见[项目状态](docs/PROJECT_STATUS.md)。

## 整体架构

```text
全局 Observation + 部署分配 + 硬件资源清单 + 完整权重
  → 按服务器/GPU 生成实例配置与权重
  → 编译为统一运行契约 ResolvedPlan

每个处理实例：
  数据发送端 → RDMA 接收 → raw ring
    → 并行解包 → compute ring
    → GPU 转换/转置 → 波束形成 → Power 或 Coherency → 可选积分
    → output ring → 输出消费者
```

- **按频段独立处理**：每张 GPU 对应一条流水线，处理用户分配的频道范围。
  各实例共享采样时间网格和 block 时间跨度，数据块字节数随频道数派生。
- **进程与算法解耦**：ring 连接接收、解包和 GPU worker 进程；
  算法模块不管理网络、ring 或进程生命周期。
- **统一数据契约**：各进程读取同一实例的 ResolvedPlan，避免分别配置
  Station 顺序、数据布局、block 几何与 header。
- **基于数据时间协调**：跨实例进度以数据携带的采样时间为依据，
  不使用服务器当前时间代替观测进度。

接收使用 NSGE=2 将 Project VDIF record 直接写入 raw ring；解包按 Station→A 轴
重排，通过多个 parser 和单 writer 保序发布。GPU worker 从 CUDA 注册的 compute
ring 执行 H2D，完成算法链后按 block sequence 写入 output ring。

`STOKES` 产品模式输出 `AA、BB、AB_REAL、AB_IMAG` coherency 基底，
可据此推导 I/Q/U/V，而非直接发布独立的 I/Q/U/V 数组。
数据布局和边界详见[数据面架构](doc/PIPELINE_ARCHITECTURE.md)与
[算法契约](doc/ALGORITHM_MODULE_CONTRACTS.md)。

## 配置方式

用户提供以下输入：

| 输入 | 内容 |
| --- | --- |
| 全局 Observation | 观测开始时间与时长、Station 顺序、频道、偏振、采样/block/积分参数、产品模式和完整权重 |
| Deployment | 服务器/GPU 频道分配，以及 CPU、NUMA、接收 flow、ring 等部署设置 |
| Inventory | 主机、CPU/GPU、网卡/端口/MAC/MTU 及资源容量 |

配置工具按 F 轴切分权重，生成每实例 `observation.json`；
Observation 编译器据此生成 `resolved_observation.json`、ring plan 和各级 DADA
header。派生字节数和 header 不需要手工维护，也不使用旧 standalone pipeline 配置。

分配由用户根据硬件决定，配置生成不进行自动硬件发现、动态负载均衡或 FPGA 下发，
也不会创建 ring 或启动数据流。
输入示例、命令和路径要求见[多服务器配置指南](docs/control/configuration.md)；
单实例字段见[Observation 配置说明](config/README.md)。

## 构建与配置检查

Linux 数据面依赖 libibverbs、PSRDADA、CUDA Toolkit/cuBLAS；构建使用 CMake：

```bash
BUILD_RDMA_PIPELINE=ON USE_CUDA=ON bash scripts/build.sh
```

macOS 仅构建可移植代码，不包含 RDMA、PSRDADA 或 CUDA 数据面：

```bash
bash scripts/build.sh
```

在构建后检查单实例配置，不启动数据流：

```bash
./build/observation_config_compile \
  --config config/observation.example.json --preflight-only
```

输入引用的权重及 wire profile 必须存在。生成工件时使用
`--output-dir <新的目录>`；配置检查和工件生成不等于运行时就绪。

## 进一步阅读

- [控制接口与生命周期](docs/control/lifecycle.md)
- [文档索引](INDEX.md)
- [构建与测试指南](tests/README.md)
