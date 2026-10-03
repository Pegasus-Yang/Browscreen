# browscreen 文档导航

English documentation: [Project overview](../README.en.md) · [Installation and deployment](deployment/安装与运行.en.md) · [User guide](user-guide/使用说明.en.md)

总览和快速开始见[项目入口](../README.md)。当前服务已实施，实际验证结果以[本机验收记录](project/本机验收记录.md)和[阶段核验清单](design/阶段核验清单.md)为准。

2026-10-03 代码审核后的修改与最新回归结果见[审核问题修复记录](project/审核问题修复记录-2026-10-03.md)；原验收与审核报告作为历史记录保留。

| 分类 | 文档 | 阅读用途 |
| --- | --- | --- |
| 安装运行 | [安装与运行](deployment/安装与运行.md) / [English](deployment/安装与运行.en.md) | Python 3.14、uv、启动与构建 |
| 使用指南 | [使用说明](user-guide/使用说明.md) / [English](user-guide/使用说明.en.md) | 文件、预览、图片接口与 webhook |
| 技术设计 | [设计方案](design/设计方案.md) | 适配器、状态、坐标和接口契约 |
| 实施计划 | [具体实施方案](design/具体实施方案.md) | 七阶段实施和本机验收步骤 |
| 阶段审核 | [阶段核验清单](design/阶段核验清单.md) | 逐项成功标准、证据与审核结果 |
| 代码审核 | [代码审核报告（2026-10-03）](design/代码审核报告-2026-10-03.md) | 当前缺陷、可选优化、复现证据与修复验收标准 |
| 实际验证 | [本机验收记录](project/本机验收记录.md) | 测试、真实网站、退出重启及证据索引 |
| 修复记录 | [审核问题修复记录（2026-10-03）](project/审核问题修复记录-2026-10-03.md) | 三项问题、两项优化的实施结果及回归证据 |

使用服务先阅读安装与使用说明；维护代码时阅读设计、实施与核验文档。初稿设计背景与实施后的几何校正已区分，实际运行证据保存在 `project/acceptance-evidence/`。
