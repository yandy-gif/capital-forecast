# 依赖与运行环境

| 层 | 依赖 | 用途 |
| --- | --- | --- |
| Python | Python 3.10+，验证版本 3.12 | HTTP、SQLite、JSON、文件、时间等标准库 |
| Python 第三方 | `openpyxl==3.1.5` | 计划 XLSX 解析、模板与数仓导出 |
| 浏览器 | 现代 Chromium 浏览器 | Fetch、localStorage、DOM、SVG、TextDecoder、DecompressionStream |
| JavaScript 测试 | Node.js 24、pnpm 11、jsdom 26.1.0 | 模拟页面、请求失败、刷新与并发编辑 |
| Windows 启动 | PowerShell、Batch | 本地服务发现与后台启动 |

运行页面没有外部 CDN 脚本依赖；流水 XLSX 由浏览器自带解压能力和项目解析代码读取。浏览器不支持 `DecompressionStream('deflate-raw')` 时应使用 CSV。openpyxl 不计算 Excel 公式，公式没有缓存结果时需要在 Excel 中重新计算保存，或填入明确数值；人民币折算由程序复算。

`requirements.txt` 管 Python 依赖；`tests/package.json` 与 `pnpm-lock.yaml` 管测试依赖。生成 v10 只需 Python 标准库。应用不需要 Node.js 运行时、云账号、API key 或外部数据库服务。
