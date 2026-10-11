# 多服务器实例的数据面架构

全局频道分配、实例配置和集中控制见[配置指南](../docs/control/configuration.md)与
[生命周期接口](../docs/control/lifecycle.md)。本文只定义每个 GPU 实例内部的数据面。

## 固定进程边界

```text
UDP/Project VDIF sender
  → rdma2dada → raw host ring
  → vdif_unpack_worker → compute host ring
  → pipeline_worker → output host ring → dada_dbnull
```

每个实例独占一张 GPU，同时拥有独立 endpoint/flow、CPU 分配、ring key 和运行目录。
同主机不同实例不能共享这些独占资源。每张网卡的多个 flow 可按部署使用同一物理端口，
但仍共享该链路容量。配置生成阶段检查资源冲突，主机监管负责运行期归属。

| 边界 | 生产者 | 消费者 | 数据 |
| --- | --- | --- | --- |
| raw ring | rdma2dada | unpack | 32-byte Project VDIF header + TFP payload records |
| compute ring | unpack | GPU worker | payload-only、block-scoped ATFP/CI8 |
| output ring | GPU worker | dbnull | Beamformed/Power/coherency，按配置可积分 |

当前只使用 host PSRDADA rings；GPU 中间结果留在 worker-owned device buffers，
不设置 CUDA ring 或算法间进程边界。本期不落盘、不实现输出网络发送或下游汇合。

## 接收、解包与计算

接收采用一个 destination-only flow、一个 QP/CQ/接收线程，NSGE=2 将网络头写入 scratch，
将 Project VDIF record 直接写入 raw-ring slot。

unpack 使用 coordinator、parser worker pool 和单 writer。Station ID 按本实例
ResolvedPlan 映射到固定 A 轴；时间排序、缺失判定和补零在有界 window 中完成。
writer 按数据 ordinal 发布 compute block，ACK 后才能复用 window range。

GPU worker 的固定合法链为：

```text
H2D → ATFP CI8 转置/转换为 TFPA CF32 → Beamform
    → [Power | coherency products] → [TimeIntegrate] → D2H
```

Beamform-only 也可直接输出复电压。Power 与 coherency 是互斥分支；
coherency 输出 AA、BB、AB_REAL、AB_IMAG，不单独发布 I/Q/U/V。
算法约束见[模块契约](ALGORITHM_MODULE_CONTRACTS.md)。

## 时间与几何

各实例共享观测起点、每包采样数、采样间隔、block 时间跨度与积分倍数。
局部频道数 F 不同可以使 packet/block bytes 不同，但不能自行改变时间网格。
权重按全局 F 轴切片，保持 P/A/B 顺序。所有 byte geometry、header 和预算由
Observation 编译器派生，运行进程只消费同一实例的 ResolvedPlan。

跨实例进度由数据本身的采样时间决定。发布到 output ring 才算本实例输出进度；
服务器时钟用于计划启动、超时和速率测量，不代替数据进度。

## Header 与 EOD

一个 HDU transfer 有 header block 和一系列 data blocks，不在每个 data block 前重复 ASCII header。

1. 读取并验证输入 header、配置身份、数据几何和实际 ring capacity。
2. 每个算法复制上游元数据，只更新自身负责的 stage/type/order/shape/rate。
3. 按编译器期望核对最终 header，在第一块输出前发布。
4. 按序处理完整或有效 partial block；数据帧和积分组不能被无声截断。
5. 排空在途任务，提交最后有效输出并传播 EOD，再释放资源。

Station 完整列表在 Observation/ResolvedPlan 中，ring header 保留 NANT、
CONFIG_ID/GEOMETRY_ID，而不重复 STATION_IDS。header 校验失败不得发布有效输出。

## CUDA 与 ring 生命周期

compute ring 启动时用 PSRDADA CUDA registration 注册，H2D 直接读取 acquired block，
没有额外 pinned input staging copy。

- `SYNCHRONOUS_DIRECT/1` 为单 stream 顺序路径。
- `STAGED_PIPELINE/N` 共用独立 H2D stream，每 slot 独占 compute stream、
  device buffers、pinned output、事件及 sequence。
- N≥2 使用 one-block H2D lookahead；先提交下一块 H2D，再提交前一块 compute/D2H。
  输入 block 到 H2D 完成才可释放，不必等其整个算法链结束。
- 完成可乱序，但一个 publisher 严格按 sequence 写 output ring；对应完成和发布之前
  不可复用 slot。Drain/Abort 都必须安全处理尚在运行的 stream。

ring 容量只缓冲抖动，不能解决持续服务率不足。配置 slot 数、stream 数不证明重叠，
也不证明多服务器吞吐已验收。

## 代码职责

- `config`：Observation/ResolvedPlan、严格身份与几何推导。
- `io/rdma`：NIC 接收和内存注册。
- `io/psrdada`：header codec、HDU、block lease、容量检查与 EOD。
- `modules`：算法及 CPU reference，不管理 ring、进程或 detached thread。
- `apps`：本地数据面进程入口，组合算法与 IO 生命周期。
- `scripts/multiserver`：跨实例配置、控制与传输；不复制算法实现。

多服务器启停、故障隔离与实际验收状态分别以生命周期接口和
[项目状态](../docs/PROJECT_STATUS.md)为准。
