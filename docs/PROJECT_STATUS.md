# 多服务器接收与计算：项目状态

当前分支：`codex/multi-server-observation`。更新日期：2026-10-15。

## 已完成范围

| 范围 | 当前状态 | 验收入口 |
| --- | --- | --- |
| MS-01/02 全局观测与部署校验 | 配置覆盖、资源冲突、公共时间网格等已通过服务器验收 | [MS-01/02](control/ms01-ms02-acceptance.md) |
| MS-03–05 实例配置生成 | 资源清单、权重切片、可移植 bundle 与现有编译器衔接已验收 | [MS-03–05](control/ms03-ms05-acceptance.md) |
| MS-06 观测级控制 | SQLite 状态、集中启停决策、局部故障隔离、重启协调已通过离线控制验收 | [MS-06](control/ms06-acceptance.md) |
| MS-07a SSH transport | 命令与 JSON 传输、超时和失败路径已通过服务器验收 | [原始结果](control/evidence/ms07a-acceptance-20261011.json)、[协调审计](control/evidence/ms07a-acceptance-audit-20261011.json) |
| MS-07b/c、I1 构建及无数据流门禁 | 用户确认本阶段验收；生产程序构建及 13 项注册测试连续三次通过 | [本阶段结果](control/evidence/ms07bc-build-gates-20261015.json) |
| Observation 单一配置入口 | 旧独立 pipeline/unpack/worker JSON/CONF 入口已移除，保留运行时结构与算法；服务器 17 项测试各三次通过 | [入口清理验收](control/evidence/ms08-observation-cleanup-acceptance-20261011.json) |

以上不代表真实多实例同步接收/计算或实时业务监测已经验收。
现有数据面复用 RDMA → raw ring → parallel unpack → compute ring →
GPU → output ring；算法契约见[流水线架构](../doc/PIPELINE_ARCHITECTURE.md)。

## 下一步

1. 确认真实数据流测试所需权限，使用已实现的统一入口验证双 GPU 启停、隔离、
   数据闭合、故障与断连恢复查询；每项三次。临时 CAP_NET_RAW 尚未获授权。
2. 完成真实功能验收后，接入数据时间进度、实际速率等业务监测及用户控制入口。

当前检查点按用户要求提交到功能分支；真实数据流未启动，论文分支未修改。
后续无 preparation 数据、保留就绪检查和统一 UTC 开始时间，见[测试协议](agents/testing.md)。
任务身份、阶段边界和保留证据见[任务台账](control/task-ledger.md)。

本期输出边界为 output ring，测试用 `dada_dbnull` 消费；落盘、FPGA 管理和下游汇合
不在当前开发范围。具体顺序以[路线图](superpowers/plans/2026-09-12-multi-server-control-roadmap.md)为准。

## 使用与维护

- 用户输入和实例生成：[配置指南](control/configuration.md)。
- 控制状态与失败策略：[生命周期接口](control/lifecycle.md)。
- 任务身份与验收记录：[任务台账](control/task-ledger.md)。
- 文档只保留用户阅读或后续开发、测试需要的内容；不重复维护旧单机实验汇总。
- 本分支与 `codex/pipeline-architecture` 隔离；原始测试证据与冻结身份不随文档整理改写。
