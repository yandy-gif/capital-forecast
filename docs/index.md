# 文档索引

本索引供使用者、开发者和 AI 助手定位资料。查功能先读 `features.md`，查操作读 `user-guide.md`，修复保存问题应同时读架构、数据模型和开发指南。下列文档描述当前实现；需求设想不能视为已完成能力。

| 文档 | 内容与适用问题 |
| --- | --- |
| [PRD（v11 当前）](../PRD.md) | 产品需求：版本定位、功能与口径、接口、验收、限制 |
| [v11 说明](v11.md) | v11 启动方式、数据链路、七个页面模块、接口与环境变量 |
| [PRD（v10 · 归档）](prd-v10.md) | 旧版本（8765）需求文档，仅存档参考 |
| [项目概况](codebase_info.md) | 技术栈、入口、发布组成 |
| [架构](architecture.md) | 前后端关系、数据流、保存策略 |
| [模块职责](components.md) | 从功能定位到源码文件 |
| [功能清单](features.md) | 已实现功能、参数用途、边界 |
| [接口](interfaces.md) | HTTP 路由、请求和响应约定 |
| [数据模型](data_models.md) | 表关系、人工标签业务键、存储位置 |
| [工作流](workflows.md) | 导入、审批、预测、比对 |
| [依赖](dependencies.md) | 运行、测试和构建需求 |
| [使用指南](user-guide.md) | 初次配置、日常操作、备份和排错 |
| [审阅记录](review_notes.md) | 已核对内容与已知实现缺口 |
| [发布规则](release-policy.md) | 不上传哪些数据以及如何检查 |
| [开发指南](../CONTRIBUTING.md) | 构建、测试、改动约定 |
| [变更记录](../CHANGELOG.md) | 本次保存修复与发布适配 |

阅读示例：「人工确认刷新后不见」→ architecture + data_models + tests；「计划为何不能批准」→ workflows + interfaces；「某参数是否参与预测」→ features。修改模块时同步更新对应文档，本索引作为后续 AI 助手的首个项目上下文。
