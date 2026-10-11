# 多服务器观测配置与业务监测设计

本文维护已确认的需求与设计理由，不作为实现进度表。已实现接口见
[配置说明](../../control/configuration.md)和[生命周期](../../control/lifecycle.md)；
当前完成范围与证据见[项目状态](../../PROJECT_STATUS.md)及[验收台账](../../control/task-ledger.md)。

## 1. 已确认范围

- 用户根据部署硬件指定 GPU 与频段的对应关系。软件不自动分配、迁移频段或故障重启。
- 每张 GPU 同时只对应一条独立的接收、unpack、GPU 和输出流水线，以 `server_id + gpu_id` 作为用户侧分配、启停及监测标识；同一服务器可有多个实例。
- 正常各实例频道数相同；平均分配有余数时最多相差一个频道。由用户提交实际映射，不由程序重新分配。
- 各机采用共同采样时间基准、block 时间跨度、积分倍数和积分起点；各机 block 字节数按本机频道数计算。
- 不生成或下发真实 FPGA 配置。可为服务器上的发包模拟器生成测试配置；模拟器多目标能力须单独验证，不能假定现有 CLI 已支持。
- 重点监测接收/输出实际速率、数据自身的时间进度、数据质量和运行状态。
- 第一版是整体上位控制和监测系统：集中下发、预检查、启停及监测均在范围内；Web 页面提供主要操作，保留命令行入口。
- 预先指定观测开始时间和时长；完成目标数据区间及流水线排空后退出。允许人工优雅中断，排空有超时，强制终止记录部分完成。
- 权重在一次观测中不变；配置生成时沿 F 轴切分完整全局权重，再下发对应实例。
- 当前输出进度到 output ring 发布完成；将来 dada2rdma 单独记录本机发送完成。本项目不负责下游接收确认、消费或数据汇合。
- 第一版以功能闭环为主，不做复杂配置编辑器、自动恢复或高可用控制集群；功能优先不豁免身份校验、资源隔离及停止清理要求。
- 代码在独立分支 `codex/multi-server-observation` 开发；不得未经批准合并到论文分支。

## 2. 设计选择与当前基础

采用“全局分配编译层生成单机 Observation，再调用已有单机编译器”的方式。

其他两种方式不采用：人工维护每台完整 JSON 容易产生时间/权重/路由漂移；重写统一运行时会扩大对已验收数据链的影响。

当前可复用接口：

- `config/observation-v1.schema.json`、`include/rdma_dada/config/observation_config.h`：单机输入。
- `src/config/resolved_observation_plan.cpp`：几何、速率、权重形状、积分和预算相关计算。
- `include/rdma_dada/config/resolved_observation_plan.h`：解析后配置及 `config_id/geometry_id`。
- `include/rdma_dada/pipeline/worker_metrics.h`：已有字节、block 等计数基础；不据此假定已有实时快照接口。

现有频道选择为 `first_channel_id + nchan`，当前编译器约束 `1 <= nchan <= 255`。第一版保持每个单机实例一段连续频道，不支持把离散频道列表直接交给旧接口。

当前单机配置要求有限正观测时长，并要求时长为整数 packet group。不限时观测不是本期默认承诺。

## 3. 人工输入与派生值

### 3.1 用户输入

| 层次 | 输入 | 约束 |
|---|---|---|
| 全局观测 | observation_id、utc_start、duration_seconds | 所有必需参与者一致；实际数据时间仍须验证包时间和样本序号 |
| 阵列 | 有序 station_ids、npol | Station 唯一；极化沿用既有固定顺序，不新增标签 |
| 全局频道 | first_channel_id、nchan、首频道中心频率、频道间隔 | 频道编号与物理频率独立于采样时间；不从频道间隔猜采样率 |
| 采样/打包 | sample_interval_ps、wire profile、samples_per_packet | 与输入协议一致；第一版共用每包采样数 |
| 时间块 | samples_per_block，即 T | 唯一人工时间块尺寸；不再独立填写每机 groups_per_block 或 block bytes |
| 算法 | 权重资源、转换设置、产品模式、积分 K 与 SUM/MEAN | 公共参数固定；NBEAM 从权重形状解析 |
| 分配 | server_id、gpu_id、连续频道范围、部署 profile 引用 | 用户决定；所有配置实例均为完整观测的必需参与者 |
| 部署 profile | 接收地址/NIC、CPU/NUMA/GPU、worker 数、队列、ring 数/键、输出目标 | 人工维护，可跨观测复用；不得静默回退 scheduler 默认值 |
| 监测策略 | 上报周期、统计窗口、心跳超时、进度差阈值和持续观察窗口 | 与科学参数分离；阈值可配置，不自动控制运行 |

部署 profile 还记录经部署检查确认的 MTU/允许包长与资源预算。不能因配置声明了容量就认定主机实际具备容量。

同一主机并行实例的 CPU affinity 集合必须不重叠，不能只区分 GPU 而复用相同 receiver/unpack/worker CPU。预检查同时识别不同逻辑 CPU 是否为同一物理核的 SMT siblings，防止将逻辑编号不同误报为物理核隔离；默认按物理核隔离跨实例数据处理线程。CPU 分配由用户的部署 profile 提供，不自动挪动。

同一主机还必须校验 GPU、ring keys、接收 flow 匹配规则、运行目录和其他独占资源的冲突；NIC、内存容量等可共享资源按主机汇总预算。gpu_id 映射到启动时核对的 GPU UUID，不能在设备枚举变化后静默选择另一张卡。

### 3.2 自动计算

设每包采样数为 S，采样间隔为 d，积分倍数为 K，本机频道数为 F。

- A = Station 列表长度；本机 F = 分配范围长度；B 从匹配权重形状解析。
- groups_per_block = T / S；block 时间跨度 = T × d。
- 积分后采样数 = T / K；输出采样间隔 = K × d。积分窗口锚定共同观测起点。
- CI8 compute block bytes = T × F × P × A × 2。
- raw record bytes = 协议头字节数 + S × F × P × 2；raw block bytes 按已有编译器的 records_per_block 计算。
- Power 积分输出 F32 bytes = (T/K) × F × P × B × 4。
- coherency 积分输出 F32 bytes = (T/K) × F × B × 4 products × 4；要求 P=2，沿用 AA/BB/AB_REAL/AB_IMAG 输出契约。
- 各 ring 总字节数、GPU 中间区/并发内存、预期 packet rate 和各阶段 data rate 复用已有解析逻辑。

计算必须使用精确整数时间和溢出检查。T 必须同时被 S 与 K 整除；时长必须满足 packet group 及现有尾块输出规则，不能静默丢弃尾采样。具体有限时长下的 partial/EOD 行为继续由已有编译器/执行链验证。

频段分配必须覆盖本次选定频道集合且不重叠，并校验本次采用的均分/余数规则。每包采样数 S 保持一致；频道多一个时调整包字节数，不自动修改 S。内存不足、包长不合法、权重不匹配时拒绝生成，不自动减小某台 T 或调整频道归属。

## 4. 全局配置概念样例

以下是拟议接口样例，不是当前 schema 可执行文件。资源名称均为示例引用；生成工具尚未实现。四 Station 小几何仅便于检查算术，不替代 469 Station 生产验收。

```json
{
  "schema_version": 1,
  "observation": {
    "observation_id": "multi-server-layout-example",
    "utc_start": "2026-09-12-00:00:00",
    "duration_seconds": "1.048576",
    "station_ids": [1000, 1001, 1002, 1003],
    "npol": 1,
    "sample_interval_ps": 1000000
  },
  "channels": {
    "first_channel_id": 100,
    "nchan": 7,
    "first_center_frequency_hz": 1200000000,
    "channel_spacing_hz": 1000000
  },
  "wire": {
    "profile": "packet_formats/frontend.example-v1.json",
    "samples_per_packet": 512
  },
  "time_grid": {"samples_per_block": 16384},
  "processing": {
    "weights_resource": "example-global-weights-f7-p1-a4-b2",
    "conversion_scale": "0.0078125",
    "product": "POWER",
    "integration": {"length": 128, "operation": "MEAN"}
  },
  "assignments": [
    {"server_id": "node-a", "gpu_id": 0, "first_channel_id": 100, "nchan": 4, "deployment_profile": "example-node-a-gpu0"},
    {"server_id": "node-b", "gpu_id": 0, "first_channel_id": 104, "nchan": 3, "deployment_profile": "example-node-b-gpu0"}
  ],
  "monitoring": {
    "report_interval_ms": 1000,
    "rate_window_ms": 5000,
    "heartbeat_timeout_ms": 5000
  }
}
```

监测周期数值是讨论样例，不是已验收默认值。完整部署 profile 与权重资源需要由用户提供；引用的内容和 SHA 随生成产物记录，不凭示例名字推断硬件配置。

样例派生值：

| 参数 | node-a | node-b |
|---|---:|---:|
| 频道 | 100–103 | 104–106 |
| samples_per_block | 16,384 | 16,384 |
| groups_per_block | 32 | 32 |
| block 数据时间 | 16.384 ms | 16.384 ms |
| compute block bytes | 524,288 | 393,216 |
| raw record bytes（32 B 头） | 4,128 | 3,104 |
| raw block bytes | 528,384 | 397,312 |
| Power 积分输出 block bytes（B=2） | 4,096 | 3,072 |
| 输出时间采样数 | 128 | 128 |
| 预期接收 UDP datagram data rate | 0.258 Gb/s | 0.194 Gb/s |
| 预期输出 data rate | 0.002 Gb/s | 0.0015 Gb/s |

实际包长仍需通过部署 MTU/协议校验；此表不是对任何服务器网络的验收。

## 5. 单机下发与身份

每个 GPU 实例接收一个不可变的配置包；同机多个配置包按实例隔离，包含：

1. 本机生成的 Observation JSON：公共观测字段、本站频段、算法、部署 profile 展开的本机字段。
2. 现有编译器生成的 resolved plan、阶段 header、ring 计划与验证结果。
3. 启动前按全局 F 映射切分好的本地权重文件，保留 A/P/B 顺序及数值；记录全局来源 SHA、本地 SHA、shape 和切片范围。实例仅加载自己的部分，运行中不再切分或更新。不得手动改 header 来适配。
4. 单独的控制/监测配置：server_id、gpu_id、上报目的地/周期、全局身份、时间网格、资源所有权；不向旧 Observation schema 塞入未知字段。
5. manifest：全局配置摘要、本地 config_id/geometry_id、权重及部署 profile 身份、生成工具版本。

每实例本地 config_id/geometry_id 允许不同，不能要求不同 F 的实例共享本地几何身份。全局 observation_id 相同；启动时另生成 run_id 区分同一观测配置的不同运行。实例重启代次和状态序号也必须可区分，但不要求用户手工填写额外的 instance_id。

服务器只需要本机配置，不需要其他机器的 CPU/GPU 参数或权重。中央监测保留全局参与者和频段表。结果记录必须能够从全局身份追溯每机配置。

### 模拟发送端

仅对服务器发包模拟器生成独立配置，包含模拟 Station 分片、本地频道 packet geometry、接收目标、公共数据时间基准、pacing/batching 和记录的目标速率。

第一版可按目标服务器生成独立发送配置；一个进程是否能同时发往多目标须另查现有 sender 接口后设计，不在本设计稿中承诺。模拟器输入和接收配置从同一分配表派生；测试可指定压力倍率，正常观测预期速率由采样几何计算，不以测试计划速率代替实测值。

## 6. 监测字段与口径

以下为拟议状态接口，不声称现有 metrics 已支持周期上报。

| 字段 | 来源/定义 |
|---|---|
| observation_id / run_id / server_id / gpu_id / local_config_id | 启动时固定的身份 |
| instance_generation / report_sequence | 系统生成，用于区分重启、重复与乱序状态 |
| channel_range | 已确认的本机分配 |
| measurement_interval_ns | 本机单调时钟测得的计数快照间隔 |
| received_records / received_record_bytes | receiver 实际接受的 Project VDIF 记录累计值，不等同于 NIC 全部收到的流量 |
| published_output_blocks / published_output_bytes | 完成 output ring commit 的累计量，不使用提交 CUDA 的数量代替 |
| receive_data_rate / output_data_rate | 相同口径的累计字节增量 × 8 / 本机测量间隔；注明 byte boundary |
| expected_receive_data_rate / expected_output_data_rate | 配置编译得到；两个值可不同 |
| receive_rate_ratio / output_rate_ratio | 实测速率分别除以自己的预期值，不能把接收字节率与输出字节率直接相减 |
| received_data_frontier_sample | 已观测到的最大合法输入样本末端，仅作输入前沿，可能跨过缺包，不称连续进度 |
| published_data_end_sample | 已按顺序发布且前方区间已处理的数据末端，exclusive end，换算为原始采样轴 |
| missing / invalid / zero_filled | 各自单位、计数和作用区间清楚；时间推进不代表样本全部有效 |
| process_state / last_error | 程序运行、结束或异常状态 |

published_data_end_sample 基于实际成功发布 block 的样本覆盖区间，不能由“当前系统时间”、最大接收时间戳或输入字节数直接替代。积分后输出仍报告覆盖的原始采样区间；最后 partial block 按实际覆盖长度计算。

输入前沿不是“所有 Station 全部连续到齐”的证明。`received_data_frontier_sample - published_data_end_sample` 只作为数据时间距离，结合缺失计数及队列等待解释，不能直接当成精确未处理字节数。若尚未收到/发布数据，进度为 null，不伪造 0 为已完成进度。

系统单调时钟只用于速率统计与超时；不同主机的单调时钟值不能互相相减。监控接收端使用自身单调时钟判断上报新鲜度。速率窗口覆盖多个 block，避免 block 发布阶梯造成假告警。

## 7. 多机状态解释

仅在同一 run、相同时间网格且状态足够新鲜的参与者之间比较输出进度。每条比较必须同时显示状态年龄；异步采样的 max-min 是带采样不确定度的快照，不是精确同步测量。

- 共同已确认输出边界：所有必需参与者最后确认的连续 published_data_end_sample 的最小值。
- 某机状态过期/尚未上报：标记共同进度不可实时确认；不得移除该机后宣称其余机器的最小值就是完整观测的共同进度。
- 固定延迟：相对进度差在多个观察窗口内有界，推进速度接近。
- 短时抖动：差距扩大后回落；只有观察到恢复后才标记为已恢复抖动。
- 持续落后：新鲜状态下差距在多个窗口持续增加。
- 处理停滞：新鲜状态持续上报，但在输入持续到达时输出进度不推进；正常启动/结束空闲单独标识。
- 上报过期：监控未及时收到新状态，处理速度未知，不当作处理速度为零。

多机接收速率同时下降，优先提示共同发送端/网络检查，不自动判定网络故障。接收正常、输出偏低且输入输出时间距离增长，提示处理/下游积压。所有机器一起变慢也不能仅凭较小的跨机进度差判正常。

速率正常但 missing/zero_filled 增长时，显示数据质量异常。阈值按共同 block 时长和观察窗口配置；第一版报告事实和趋势，不自动改配置、重启或迁移。

## 8. 集中控制与第一版操作界面

### 启停和故障策略

- 统一入口生成和下发配置，验证内容身份及每机资源，所有必需实例就绪后才允许本次观测进入运行。接收进程可提前准备，但观测区间以数据时间为准；准备超时或错过规定开始时间不能静默改成另一开始时间。
- 启动准备失败：撤销本次启动，清理确属本 run 的已准备资源，不触碰其他观测、未知 ring 或进程。跨机撤销不是原子操作；未确认退出的实例必须标记未知/待清理，不虚报全部已撤销。
- 运行中局部故障：仅停止并清理故障实例，其他健康实例继续，整体标记降级运行；保留缺失频段及数据时间范围，不自动移除参与者、不自动重启。用户于 2026-10-09 确认：人工中止本次观测时全部实例停止，第一版不提供人工只停指定实例的入口。故障隔离的内部定向清理不等于人工中止。
- 控制或监测连接中断：不自动终止健康数据链；重新连接后核对 run/实例身份并读取实际状态，不能重复启动已在运行的实例。
- 正常结束：停止接收目标区间之外的新观测数据，按既有数据契约完成排空、尾块和 EOD，再释放本 run 资源；缺失/无效数据单独记录。
- 人工停止：面向本次观测的全部实例，默认停止纳入新数据，限时排空已接收数据；超时或用户明确强制停止时终止并记录未完成区间。失联实例保留未确认/待清理状态，不能声称已全部停止。各实例的实际终止数据时间可能不同，不宣称人工停止具有共同精确截止采样点。排空超时为显式运行策略，不无限等待。
- 控制端程序重启：读取持久化运行记录，逐实例查询并核对实际 run/config/代次；不因控制程序重新启动而重启服务器或流水线。不自动重发结果未知的启动操作，也不把旧记录当作当前在线证据。
- 控制命令按 run 和目标实例标识，重复启停请求必须可识别并返回当前操作结果，不重复创建资源；记录请求、实际动作、错误和清理结果。

### Web 与命令行

第一版 Web 页面支持：选择/上传版本化配置、预检查、集中启动、停止、查看实例状态及速率/数据进度，并可查看配置身份和首个错误。底层命令行与 Web 使用同一控制接口，不各写一套启停逻辑。

不做复杂在线配置编辑器、自动优化、自动故障恢复或高可用控制集群。正式性能指标扩展和页面美化不阻塞功能版验收。功能优先不等于允许上传任意 shell 命令：启动参数必须来自校验后的配置和受控程序入口，控制访问必须受限制，凭据不写入观测 JSON 或日志。

## 9. 分阶段实现与验证范围

1. 全局配置编译：新增独立 schema/工具/测试，复用已有单机解析；先不修改数据链。验证 4/3 不等频道分配、T/S/K 整除、重叠/遗漏、权重切片顺序、包长和资源拒绝、身份可追溯。
2. 资源隔离与集中控制：验证同机双 GPU 下 CPU 逻辑编号/物理核、ring、flow、运行目录冲突拒绝；测试全部就绪、准备失败撤销、重复命令、断连重接、局部运行失败继续、正常/人工停止及限时清理。
3. 单机状态：只补必要计数快照和采样末端；明确采集开销，不逐包发布网络状态。检查实际发布、partial/EOD、补零、失败和重启时进度语义。
4. Web/命令行功能闭环及多机汇总：用确定性状态序列测试固定差距、抖动恢复、持续落后、共同输入下降、缺报和过期；首期不开发数据汇合。
5. 模拟发送验证另列子计划；涉及远程 controller 的设计/修改前，按仓库要求由服务器测试任务先执行 Phase 0。不同频道数可能改变包长，必须实证核对，不能直接复用包长不同的性能结论。

功能实现后交给 `GPU服务器代码测试` 验证；只读/本地测试不代替服务器验收。已有 Full、GPU-only、unpack suite 和配置保留，不因文档更新触发重测。

## 10. 设计确认与实施准备

用户已确认集中启停、Web 主界面/命令行、故障策略及第一版功能优先。grilling 范围决定已收敛；接下来细化文件级实施计划，不重新询问这些已定范围。

远程运行环境、支持的命令和权限通过 Phase 0 获取，不由用户猜测。控制传输、Web 技术栈及阈值初值应在实施计划中结合现有依赖和部署证据给出最小实现选择，不据此扩展业务范围。实施前检查分支及未提交工作，保留当前数值测试工作，使用隔离新分支，既有产品测试和结果不删除、不覆盖。

## 11. 控制系统参考与取舍

保留2026-10-09调研中仍影响后续开发的结论；链接指向上游活动文档，
不是固定版本或本项目验收证据，也不是待引入的依赖。

- [Simons Observatory OCS Registry](https://github.com/simonsobs/ocs/blob/main/docs/agents/registry.rst)：
  借鉴心跳新鲜度与操作状态分离；心跳不证明数据处理进度或观测完成。
- [MeerKAT katsdpcontroller](https://github.com/ska-sa/katsdpcontroller)及
  [katgpucbf 控制说明](https://katgpucbf.readthedocs.io/en/latest/control.html)：
  借鉴观测级汇总与逐实例详情分层，不引入整套调度基础设施。
- [Supervisor 配置](https://supervisord.org/configuration.html#program-x-section-settings)及
  [信号行为](https://supervisord.org/running.html#signal-handlers)：
  进程存活不等于业务 READY；控制端重启不能通过父子进程关系隐式重启观测。

运行中局部故障不停止健康频段、人工停止全体、重启先协调而不重发 start，
都是本项目用户确认的策略，不声称由上述上游实现证明。
上游精细故障/重启代码未完成源级核验；未来适配需依本项目契约及实测验收。
