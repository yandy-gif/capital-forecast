# 开发指南

## 环境与构建

应用后端依赖 `requirements.txt` 中的 openpyxl；前端为原生 JavaScript、内嵌 CSS/SVG 和浏览器 XLSX 解析代码，没有 React/Vue 或前端打包器。Node.js 仅用于 jsdom 测试。

```powershell
python -m pip install -r requirements.txt
python 数仓/build_v10.py
python -m unittest discover -s tests -p "test_*.py"
```

前端测试使用 Node.js 24 和 pnpm 11；在 `tests` 目录执行：

```powershell
pnpm install --frozen-lockfile
pnpm test
```

不要只修改构建产物 `生态资金计划系统v10.html`。基础页面逻辑改在 v9 源码；生态模块改在 `数仓/eco_module.js`，随后重建 v10。仓库同时提交源码与构建产物，便于直接运行。

## 关键约定

- `db.DB_PATH` 默认指向代码目录内 `warehouse.db`；Python 测试重定向到临时文件，API 测试使用系统分配的空闲端口。
- `db.connect()` 启用 WAL、30 秒 busy timeout 和 autocommit。涉及多个写操作时必须显式设计事务，不能仅凭调用 `commit()` 推断具有整体原子性。
- 人工标签业务键为「日期|规范化金额|对方户名」；修改组成键的字段时必须同步迁移标签并保留修改日志。
- 保存结果以服务器确认成功为准。待保存增量和草稿不能被刷新得到的旧值覆盖，也不能在失败后清空。
- 新表写入 `数仓/schema.sql`，通过空库测试验证；不要依赖某个本地数据库已经存在的表。
- 初始化只补缺失的基础配置；用户参数、规则、主体、账期和人工确认不可在重启时重置。
- 分类规则与科目桥接是不同层次：前者生成流水标签，后者将标签映射到计划科目。

## 测试覆盖

| 测试 | 验证内容 |
| --- | --- |
| `tests/persistence.test.cjs` | 保存失败、刷新、连续编辑、规则和参数草稿、零天账期、余额预测 |
| `tests/release.test.cjs` | 空页面渲染、有效计划金额不被情景常量覆盖 |
| `tests/test_database.py` | 人工标签原子校验、流水改键迁移、金额规范化 |
| `tests/test_api.py` | HTTP 保存、模板回传、汇率、上传审批核销 |
| `tests/test_initialization.py` | 空库启动、无业务记录、初始化保留用户配置、附页落库 |

测试中少量虚构记录是代码内的断言输入，不是上传的业务/测试数据集。不要复制工作数据库来运行测试。

## 发布检查

```powershell
python 数仓/build_v10.py
git add README.md CONTRIBUTING.md AGENTS.md CHANGELOG.md requirements.txt .gitignore .gitattributes docs scripts tests 数仓 *.html .github
python scripts/check_release.py
git diff --cached --stat
```

检查暂存清单后提交。数据过滤既依赖 `.gitignore`，也依赖发布检查；不得通过 `git add -f` 加入数据库、Excel、账户清单、截图或本地缓存。变更功能时同步更新 `docs/features.md`、相关接口/工作流文档和 `CHANGELOG.md`。

`WH_PORT` 设置端口，`WH_HTML` 设置托管的 HTML 文件，`FINANCE_PYTHON` 为 Windows 启动器指定解释器。没有 API key 或云服务凭据需求。
