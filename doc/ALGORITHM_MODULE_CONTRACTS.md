# Observation 算法模块契约

本文定义多服务器各处理实例复用的数据布局与模块组合规则。
配置入口为 [Observation](../config/README.md)；进程/ring 所有权见
[数据面架构](PIPELINE_ARCHITECTURE.md)。具体参数与错误路径以各模块指南和实现为准。

## 轴与输入

| 轴 | 含义 |
| --- | --- |
| T | 当前 block 的时间采样数 |
| F | 本实例的频道数 |
| P | 偏振数；NPOL=2 固定顺序 X,Y |
| A | Observation 中有序 Station 列表长度 |
| B | 数字波束数，由对应权重矩阵确定 |
| S | coherency 产物数，固定 4 |

前端 Project VDIF 使用固定 32-byte header 与 TFP payload；
当前 Observation 数据链使用 CI8（有符号 I/Q 各 8 bit）。
CI16 codec/转换模块的独立支持不代表当前 unpack/GPU worker 已开放 CI16 输入。
包格式详见 [Project VDIF](PROJECT_VDIF_PROFILE_V1.md)。

同一观测各实例使用相同 T、每包采样数和采样间隔；局部 F 决定字节规模，
不能为适配不同 GPU 单独改变 block 的数据时间跨度。

## 模块边界

| 模块 | 当前流水线输入 → 输出 | 参数与详细契约 |
| --- | --- | --- |
| unpack | RAW records → payload-only ATFP/CI8，host | [vdif_unpack](../modules/vdif_unpack/README.md) |
| H2D | ATFP/CI8 host → 相同数据 device | [host_to_device](../modules/host_to_device/README.md) |
| 转换/转置 | ATFP/CI8 → TFPA/CF32，device | [complex_convert](../modules/complex_convert/README.md) |
| Beamform | TFPA/CF32 + FPAB 权重 → TFPB/CF32 | [beamform](../modules/beamform/README.md) |
| Power | TFPB/CF32 → TFPB/F32 | [power](../modules/power/README.md) |
| coherency | TFPB/CF32，P=2 → TFBS/F32 | [stokes](../modules/stokes/README.md) |
| 积分 | TFPB/TFBS F32 → T/K 个时间采样，F32 | [time_integrate](../modules/time_integrate/README.md) |
| D2H | 最终 device tensor → 相同数据 host | [device_to_host](../modules/device_to_host/README.md) |

unpack 按 Station ID 映射到 A 轴，并按时间 ordinal 重排、补零和发布；包头不进入 compute ring。
compute block 物理顺序为 ATFP，不是 TFPA。转换模块融合转置和
`Y.real/imag = scale * X.real/imag`，scale 为显式正有限值，不自动归一化。

权重 NPY 形状为 `[F,P,A,B,2]`，最后一维存储复数分量。
多服务器配置生成按 F 轴切片，保留 P/A/B 顺序；不在运行中切换权重。
Beamform 输出 B 轴替代 A 轴，运算和 scale 细节见模块指南。

Power 与 coherency 为互斥兄弟分支。coherency 输出顺序固定：

```text
AA = |X|²
BB = |Y|²
AB_REAL = Re(X * conj(Y))
AB_IMAG = Im(X * conj(Y))
```

I=AA+BB、Q=AA−BB、U=2AB_REAL、V=−2AB_IMAG 是参考验证的派生量，
不是当前单独发布的输出数组。

## 合法组合

```text
H2D → Convert/Transpose → Beamform → D2H
H2D → Convert/Transpose → Beamform → Power → [TimeIntegrate] → D2H
H2D → Convert/Transpose → Beamform → Coherency → [TimeIntegrate] → D2H
```

Power 后不能接 coherency；Beamform 复电压不能直接接当前 F32 积分模块。
不支持任意算法间 ring 拆分或动态模块 registry。
配置生成和 worker 启动均须检查 stage、memory、type、order、shape、weight 与容量。

## 几何与时间

对完整 CI8 compute block：

```text
T = samples_per_udp × udp_packets_per_antenna_per_block
compute_bytes   = T × F × P × A × 2
converted_bytes = T × F × P × A × 8
beamformed_bytes = T × F × P × B × 8
power_bytes     = T × F × P × B × 4
coherency_bytes = T × F × B × 4 × 4
output_T        = integration_enabled ? T/K : T
```

积分支持 SUM/MEAN，K 必须整除实际处理的时间采样数，不能跨 block 丢弃尾样本。
partial block 只处理有效完整帧，且仍须满足所选积分契约。
输出时间间隔为输入间隔乘 K，观测时间原点保持不变。

`observation_config_compile` 从每实例配置统一派生 ring plan、stage headers 和
`gpu_pipeline_budget`；实际 ring 容量必须匹配。预算包括中间 device buffers、
权重和 staged pinned output，并另给建议空闲显存；runtime/cuBLAS/驱动开销需
服务器预检。预算通过不等于实时吞吐已验收，ring 大小也不能弥补持续服务率不足。

## Header、buffer 与生命周期

- 模块不连接 ring，不管理进程，不启动 detached thread。
- worker 读取编译器 ResolvedPlan，验证输入和最终输出 header，而不是读取旧独立 worker JSON。
- 未知上游 metadata 保留；模块只更新自身负责的 stage/order/type/shape/byte rate 等字段。
- header 每 transfer 发布一次，data block 不重复携带 ASCII header。
- 一个 GPU 输入 block 对应一个按序输出 block；输入输出 buffer 不重叠。
- 模块通过 worker 提供的 execution context 使用 CUDA stream，只提交工作；
  资源分配、跨 stream 依赖、事件等待与发布归 worker 所有。
- input ring lease 保留至 H2D 完成；output buffer 保留至对应 CUDA 完成及按序发布。
  不要求输入 lease 一直持有到 D2H。
- EOD 时先排空在途工作并提交最后有效输出，再发布 output EOD；
  Abort 也不能在 stream 仍引用资源时释放 buffer/event。

调度模式、H2D lookahead 和有序发布的唯一说明入口是
[pipeline_worker](../apps/pipeline_worker/README.md)；
多实例失败隔离遵循[控制生命周期](../docs/control/lifecycle.md)，不属于算法模块。

## 回归验证

数值 oracle、非法配置、轴顺序、shape/bytes、partial/EOD、内存容量、stream 生命周期和
输出保序均需保留回归。CPU reference 是 CUDA 数值对照，不是额外生产处理链。
测试调用见 [tests](../tests/README.md)，服务器验收见[测试规则](../docs/agents/testing.md)。
