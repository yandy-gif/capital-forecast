# Capital Forecast · 资金预测与计划管理

面向本地财务工作的资金管理应用：银行流水导入、规则打标、人工确认、资金计划提报与审批、参数假设、滚动预测、预实分析/预实比对及报表导出。界面按「一 数据 / 二 分析 / 三 测算（II期）」三模块组织。以 Python + SQLite 保存数据，以原生 HTML/JavaScript 提供操作界面。

本仓库包含完整运行源码、构建入口、数据库结构、基础分类配置、自动化测试和中文文档。**首次启动为空业务库**，不附带本地测试数据、真实流水、历史预算、客户账期、银行账户、上传原件或备份。测试在执行时自行构造最小数据并放入临时目录。

## 两个版本

| 版本 | 页面 | 服务 | 库 | 状态 |
| --- | --- | --- | --- | --- |
| **v11** | `生态资金计划系统v11.html` | `数仓/v11/server11.py` · 8770 | `数仓/v11/warehouse_v11.db` | **当前版本**：本地数仓 + 单页看板，四模块七页 |
| v10 | `生态资金计划系统v10.html` | `数仓/server.py` · 8765 | `数仓/warehouse.db` | 保留可运行，不再加功能 |

v11 的定位与完整需求见 [PRD](PRD.md)，启动细节与页面模块见 [v11 说明](docs/v11.md)。

### v11 快速启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe 数仓\build_v11.py            # 由 数仓/v11/ 源码合成单文件看板
.\.venv\Scripts\python.exe 数仓\v11\server11.py         # 默认 127.0.0.1:8770
```

或双击 `打开看板v11.bat`（启动服务并打开浏览器）。首次启动自动建表，并灌入 31 个科目编码、38 条匹配规则、26 条受限资金关键词与汇率基线——**没有 `seed.json` 也能从空库启动**（此时只有一个占位主体，可自行改名后导入自己的流水）。设置 `V11_PORT` 可换端口，设置 `V11_DB` 可指向临时库做测试。

## 快速启动

需要 Python 3.10 或以上版本（已验证 3.12）、现代 Chrome / Edge 浏览器。运行应用无需 Node.js，也无需外部数据库。

```powershell
git clone https://github.com/yandy-gif/capital-forecast.git
cd capital-forecast
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe 数仓/build_v10.py
.\.venv\Scripts\python.exe 数仓/server.py
```

打开 **http://127.0.0.1:8765**。服务首次启动自动建库、补齐基础科目/桥接/参数并注册两种空白模板。上述前台启动方式按 `Ctrl+C` 停止。

Windows 安装依赖后，也可双击 `数仓/启动数仓.bat`，它会寻找 `.venv` 中的 Python、后台启动服务并打开页面。日志与进程号位于 `数仓/server.*.log`、`数仓/server.pid`。已有其他副本占用端口时，请设置 `WH_PORT`，例如在 PowerShell 中执行 `$env:WH_PORT='8877'` 后再启动。

macOS / Linux 可在虚拟环境安装依赖后执行 `python 数仓/server.py`；Windows 快捷启动器仅适用于 Windows。

## 第一次使用

1. 在「一 数据 · 资金计划」下载空白计划模板，在「主体台账」填写自己的公司信息；科目字典和人民币汇率已提供。
2. 填写预算明细、确认期间、上传并查看校验报告；按「提交 → 批准」得到有效计划。
3. 在「数据接入」导入自己的银行流水，校验字段和金额，维护规则并完成必要的人工确认。
4. 在「三 测算 · 参数假设」设置账期、回款比例等（安全线也可直接在「管理视角」页内填），然后看「二 分析 · 预实分析」与「一 数据 · 预实比对」。

完整流程见 [使用指南](docs/user-guide.md)。请通过 HTTP 服务打开页面；直接双击 HTML 不能替代数据库服务。

## 功能与文档

| 内容 | 入口 |
| --- | --- |
| 所有功能、实现状态、参数实际用途 | [功能清单](docs/features.md) |
| 模块关系、数据流、保存机制 | [系统架构](docs/architecture.md) |
| HTTP 接口与请求格式 | [接口说明](docs/interfaces.md) |
| 数据表、业务键、主从关系 | [数据模型](docs/data_models.md) |
| 导入、审核、审批、预测流程 | [工作流](docs/workflows.md) |
| 环境、构建、测试与贡献 | [开发指南](CONTRIBUTING.md) |
| 发布范围与数据排除规则 | [发布与数据说明](docs/release-policy.md) |
| 全部文档导航 | [文档索引](docs/index.md) |
| 保存修复与发布整理 | [变更记录](CHANGELOG.md) |

主要源码在 `数仓/`；`珀研Q4资金预测看板v9.html` 是保留历史文件名的基础页面源码，`生态资金计划系统v10.html` 是构建产物及默认运行页面。两份 HTML 均不含内嵌业务记录。修改基础页面或 `数仓/eco_module.js` 后需重新构建 v10。

## 适用范围

当前为绑定 `127.0.0.1` 的本地应用，没有登录、权限校验或多人写入冲突解决机制；操作人字段用于留痕，不构成身份认证。不能直接作为公网财务系统部署。

生态计划与比对支持按期间取数；旧管理报表仍保留部分季度模型和历史页面命名。原有利润情景算法保留，但历史预算与个人化默认规则已移除；它们不会从空库自动得到真实利润。G3 及部分业务参数是提示/预留配置，具体边界见功能清单。税率、账期与分类规则是模型配置，须结合实际业务维护。
