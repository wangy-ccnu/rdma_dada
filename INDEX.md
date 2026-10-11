# Project index

本分支只维护多服务器同步接收、计算及其配置、启停和监测。

## 使用与开发

- [项目说明](README.md)：系统整体和构建入口。
- [配置指南](docs/control/configuration.md)：全局观测、部署、资源清单、权重切片与实例 bundle。
- [控制接口](docs/control/lifecycle.md)：持久状态、启停、故障隔离和 SSH transport。
- [项目状态](docs/PROJECT_STATUS.md)：已验收范围与下一步。
- [任务台账](docs/control/task-ledger.md)：任务身份和验收证据。
- [系统设计](docs/superpowers/specs/2026-09-12-multi-server-observation-design.md)：多服务器需求与边界。
- [路线图](docs/superpowers/plans/2026-09-12-multi-server-control-roadmap.md)：实施顺序及分支、服务器隔离。

## 数据契约与验证

- [流水线架构](doc/PIPELINE_ARCHITECTURE.md)：实例内进程、ring 和资源所有权。
- [算法契约](doc/ALGORITHM_MODULE_CONTRACTS.md)：数据布局、模块输入输出和组合规则。
- [实例配置](config/README.md)：Observation → ResolvedPlan。
- [Project VDIF](doc/PROJECT_VDIF_PROFILE_V1.md) 与 [wire profile](config/packet_formats/README.md)：输入包格式。
- [应用](apps/README.md)与[算法模块](modules/README.md)：实现入口。
- [测试调用](tests/README.md)与[验收规则](docs/agents/testing.md)：回归和服务器验收。
