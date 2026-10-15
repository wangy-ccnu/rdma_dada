# 多服务器分支测试与验收

本规则面向 `codex/multi-server-observation` 的配置、控制、同步接收与计算。
测试范围由变更影响决定，不自动重跑历史单实例性能实验。
分支、源码和远端目录隔离遵循[路线图](../superpowers/plans/2026-09-12-multi-server-control-roadmap.md)。

## 验收边界

- 本地测试是开发证据。新增模块或行为须交由既有“GPU服务器代码测试”任务在目标
  Linux 服务器验收；开发任务不自行部署或运行远端测试。
- 配置解析、bundle、离线控制、SSH transport、主机监管和真实数据流分别验收。
  mock、dry-run、编译预算或 SSH 往返通过，不等于多实例数据流通过。
- 不变的数据面基线可复用其身份和证据；变更触及算法、ring 生命周期或吞吐时才补
  对应回归与性能测试。
- 测试入口须版本化，接受显式配置，可从干净状态复现并输出机器可读结果；
  临时脚本和交互命令不能作为最终验收入口。
- 本分支功能状态及验收入口只维护在[任务台账](../control/task-ledger.md)，
  测试调用见[tests](../../tests/README.md)。

## Phase 0 与身份冻结

修改远程测试控制器前，由测试任务在每个目标执行只读 Phase 0，记录：

- 必需程序的绝对路径、版本、支持选项和动态库解析；
- 用户、设备、网络权限，NIC/link/MTU/NUMA、CPU、内存和磁盘容量；
- NTP/PTP 状态及影响 argv/JSON 的 shell 行为；
- 选定构建目录、源码、配置和可执行文件身份。

缺失设施报告 `ENV_BLOCKED`，附失败命令和输出；不得猜路径、静默安装或改变网卡配置。
环境经批准修改后复查。qths1 使用已验证的
`/home/user/wy/tools/cmake-3.31.12/bin/cmake` 和配套 `ctest`，不混用系统版本。

源码以本地功能 worktree 为准。阶段性变更收口或测试前更新
`config/testing/multiserver-source-manifest.sha256`；普通文档编辑不逐次刷新 SHA。
冻结验收 manifest 不重写。测试任务完整增量同步隔离的功能源码，保留 build、数据和
结果，逐项校验后再构建。显式构建目录不得回退到默认路径；远端不执行 Git。

## 重复与覆盖

| 类别 | 最低正式要求 |
| --- | --- |
| 配置、CPU、离线控制与 transport | 受影响版本化测试连续三次通过 |
| CUDA 数值 | 同一输入/seed/tolerance 连续三次通过，记录 shape/bytes、误差和 NaN/Inf |
| 进程/ring/网络集成 | 三次独立、干净的资源生命周期，计数与 EOD 闭合 |
| 性能点 | 一次 warm-up 加三次 measured；每次均满足预先确定的速率、时长与闭合门禁 |
| 故障注入 | 确定性输入和期望状态，覆盖失败、恢复查询与定向清理 |

只执行与本次改动相关的集合；同一用例的 direct 与 CTest 调用不算两种覆盖。
CTest 未注册、目标未构建、缺少参数或 skip 不能报 PASS。统计包含全部重复，不能只挑最好结果。

## 多实例必须验证的行为

配置与状态语义以[configuration](../control/configuration.md)、
[lifecycle](../control/lifecycle.md)为准，验收至少覆盖：

- 频道覆盖与重叠、server/GPU 唯一分配、同主机 CPU/endpoint/ring/目录冲突；
- 共同开始时间、每包采样数、block 时间跨度、积分整除；各实例按局部 F 派生字节几何；
- 权重频道切片保持 Station、偏振和波束顺序，实例加载相应 Observation/ResolvedPlan；
- 全部必需实例准备好之后才启动；启动失败清理本次准备的资源；
- 运行中一个实例失败，健康实例继续，整体标记部分失败；不能把缺失实例当完整观测；
- 人工停止作用于整个观测；停止、排空、超时升级及重复命令均按生命周期契约处理；
- 控制端重启先查询实际状态，不盲目重启或重复创建进程/ring；
- 接收与输出进度来自数据采样时间，不能用服务器当前时钟代替；
- 输出进度以按序发布到 output ring 为边界；当前关闭落盘，以 dbnull 验证 EOD。

真实数据流验收还要按实例核对 sender/receiver/unpack/GPU/output 的包、block 和字节。
用户于 2026-10-15 确认：本轮及后续测试默认无 preparation 数据段，只发送正式观测
窗口的数据。程序预启动、全部实例就绪检查和共同 UTC 开始时间仍保留；它们不是预备数据。
历史工件保留当时的 preparation 配置和计数，不改写，也不因此重跑。
完整、partial tail、缺包补零、重复/迟到、错误 header 和正常结束均须有明确期望。
已配置 Station 的缺席不能静默变成更小阵列；实例故障不扩大为停止健康频段的默认策略。

## 运行安全

- 控制 host 按部署清单分别连接目标。现有测试拓扑由 HF 编排，不让 qths1 反向 SSH sender。
- 使用 argv 或版本化脚本，避免嵌套 SSH shell 展开；host-key 文件限定到本次任务。
- 资源创建前验证配置、二进制、网络 endpoint、能力和就绪条件；数据窗口遵循上述
  无 preparation 规则。多实例 CPU/NUMA/端口由部署输入决定，
  不把某台机器的固定核号当产品默认值。
- 每个进程保存真正子进程 PID/PGID、argv、环境、退出状态；wrapper 必须转发停止信号。
  后台子进程不能意外持有 SSH stdout/stderr 导致启动互等。
- 控制器 self-test 后检查无任务进程/ring/capability 残留，再开始集成。
- 清理仅操作本次记录的资源，不使用广泛 pkill 或未经归属确认的 ring key。
  `dada_db -p` 会创建资源，不能用作只读探针。
- 超时保留首错、状态时间线和进程证据；清理状态不得覆盖测试结果。
- 性能测试不引入落盘瓶颈。确需 materialized 数据核对时另行明确范围。
- 创建远端目录时记录任务、来源任务 ID、用途及保留/清理条件；结果删除需用户授权。

## 结果接口

每阶段只保留一份精简 JSON 结果及必要原始证据，放在 `docs/control/evidence/`
或该阶段记录的隔离结果目录。指南说明接口和复现方法，不逐轮复制测试日志。

结果至少包含：测试范围和命令、源码/配置/构建身份、输入和有效参数、每轮状态、
首个失败及原始错误、资源归属与清理结果、证据路径和 SHA。
数据流结果另保留实例身份、process ledger、状态时间线、计数/时间/速率及共同进度。
只收集实际拓扑产生的文件；早期失败中未产生的可选诊断文件不能误判为清理失败。

- `SYNC_FAIL`、`ENV_BLOCKED`、`HARNESS_FAIL` 是身份、环境或测试系统问题，
  不能作为产品吞吐失败。
- 正确性失败与持续速率不足分别报告；最终计数闭合不代表按要求时长完成。
- `TEST_RESULT`、`CLEANUP_RESULT`、`RESULT_NOTIFICATION` 独立记录。
- 原始结果保持不可变；更正附可追溯裁决，不能手改 summary 冒充重新测试。
- 测试任务完成或受阻均须向来源开发任务明确回传结果，确认发送成功。通过后由用户
  决定 commit/push，不自动执行。

## 性能与业务监测字段

预先确定各实例输入 data rate、packet rate、block bytes/时间跨度、时长和余量。
记录实测接收/输出速率及分母时间窗口，配置速率不可冒充实测，聚合时不能重复计算数据。
GPU-only 若用于诊断，速率明确为 compute-ring payload rate。

持续性判断同时使用数据时间进度差、stage service/arrival、ring/queue HWM 或
背压时间、loss/zero-fill 和资源利用率。配置了多个 stream 不等于产生重叠；
需要 overlap 指标或时间线支持。首个饱和阶段必须由对应边界的计数与等待证据支持，
不能只看 sender rate。扩大 ring 只能缓冲抖动，不能解决长期服务率不足。
