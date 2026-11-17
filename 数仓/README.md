# 数仓与本地服务

本目录保存 Python 服务、SQLite 结构、计划解析、基础配置、空白模板生成及生态前端模块。

- [项目安装与启动](../README.md)
- [系统架构](../docs/architecture.md)
- [HTTP 接口](../docs/interfaces.md)
- [数据模型](../docs/data_models.md)
- [构建和测试](../CONTRIBUTING.md)

直接运行 `python server.py` 自动初始化缺失的基础配置，并只监听回环地址。首次启动不会导入业务数据；`warehouse.db`、uploads、日志和 pid 属于本地运行产物，不提交 Git。
