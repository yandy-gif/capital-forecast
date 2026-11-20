# HTTP 接口

默认根地址 `http://127.0.0.1:8765`。JSON 请求使用 `Content-Type: application/json`，返回 UTF-8。多数写接口返回 `ok`、失败时 `error`；部分历史读取接口没有 `ok` 字段。客户端应同时检查 HTTP 状态与业务响应，不把 200 自动等同于业务成功。

## 读取

| 方法 / 路径 | 参数 | 结果 |
| --- | --- | --- |
| GET `/`、`/index.html` | — | v10 页面及服务引导脚本 |
| GET `/api/health` | — | `ok, db, html, stats, eco` |
| GET `/api/rows` | — | `flow, tagged, plan, manual, balances, items, stats, resetToken` |
| GET `/api/rules` | — | 规则 `payload`（JSON 字符串）和更新时间 |
| GET `/api/requests` | `kind` 可选 | 导入/修改申请 `rows` |
| GET `/api/flow-edits` | `id` 或 `flow_id` 可选 | 修改历史 `rows` |
| GET `/api/eco` | — | 模板、字典、主体、汇率、参数、账期、桥接、提报及状态定义 |
| GET `/api/template` | `id=TPL-05` 或 `TPL-06` | 空白 XLSX；下载计数增加 |
| GET `/api/uploaded` | `name`、`inline=1` 可选 | 已上传原件，仅允许 uploads 内的文件 |
| GET `/api/submissions` | — | 提报清单 |
| GET `/api/submission` | `id` | 单条提报、明细、附页（`appendix` / `appendix_sales` / `appendix_expense`）和审批日志 |
| GET `/api/locked` | `period`、`entity` 可选 | 已锁定/已核销计划明细 |
| GET `/api/appendix` | `period`、`entity` 可选 | 已锁定/已核销计划的附页明细（客户×商品 / 部门×费用项），管理视角、下钻、收支明细的计划口径用 |
| GET `/api/export` | — | 流水、旧计划和人工标签 Excel |
| GET `/db` | `t`、`limit` 可选 | 数仓表预览，具体可选表以页面为准 |

## 写入

首次重置后，所有 POST 请求需携带 `X-Reset-Token`，值取自 `GET /api/rows` 的 `resetToken`。页面自动携带本页已加载的版本；旧版本请求返回 409，需刷新后重试。重置、备份与普通 HTTP 读写串行执行，避免正在导入的数据在重置完成后重新落库。

| 路径（POST） | 主要请求字段 | 用途 |
| --- | --- | --- |
| `/api/import` | `kind: flow或plan, fileName, rows, actor` | 流水/旧计划导入 |
| `/api/tagged` | `rows` | 分类流水快照 |
| `/api/manual` | `manual, merge, remove` | 合并人工标签；默认 merge=true |
| `/api/flow-edit` | `id, patch, label, actor, reason` | 修改流水并迁移标签，保留日志 |
| `/api/rules` | `payload` | 完整规则数组序列化字符串 |
| `/api/requests` | `action:add或status` 与对应记录字段 | 创建申请或修改状态 |
| `/api/clear` | `kind:flow或plan或manual` | 清空指定类别；仅供明确需要清理的本地操作 |
| `/api/reset` | `confirm:"RESET_ALL", browserState` | 备份后清空全部测试内容并恢复初始配置，返回 `resetToken, backup` |
| `/api/upload-plan` | 二进制 XLSX，见下文 | 创建草稿，可自动提交 |
| `/api/submission-action` | `id, action, actor, role, reason, force` | 状态推进，actor 必填 |
| `/api/params` | `params, actor` | 对象更新已有值；数组可写完整参数定义 |
| `/api/terms` | `rows:[{customer,klass,term_days,note}], actor` | 客户账期保存；0 表示零天 |
| `/api/bridge` | `rows`，含 `id,direction,l1,l2,subject,cashflow,handle,priority,status` | 保存桥接规则 |
| `/api/match-link` | `links`，含 `sub_id,item_id,period,entity,subject,flow_key,amount,basis,confirmer` | 保存人工匹配关系 |

人工标签格式为 `{"manual":{"日期|金额|对方名称":{"l1":"一级分类","l2":"二级分类","cf":"现金流类别"}},"merge":true}`。金额需规范化，业务键应使用系统生成值，避免客户端自行拼出不同格式。

计划上传使用 `Content-Type: application/octet-stream`，请求体为文件原字节。头部：`X-File-Name` 文件名、`X-Period`（YYYY-MM）、`X-Actor` 操作人，`X-Submit: 1` 表示尝试直接提交。中文头值应 URL 编码。成功返回提报 ID、期间、主体、版本、明细数、汇总、校验报告和原件保存位置；源表转换可能返回多期间提报结果。

上传按文件内容 SHA256 查重：与已有提报单（非作废）内容完全相同时返回 409 与 `duplicate: true`（含原提报单 ID），不建新单；解析失败返回 4xx/5xx 并提示修正后重新上传。成功、失败、拦截三种结果都会写一条申请记录（`/api/requests?kind=plan`，状态分别为 已上传 / 失败 / 已拦截）。批准锁定时后端把有效明细写入 `dwd_plan`，`/api/rows` 的 plan 即随之更新。

审批 `action` 支持提交、批准、退回、发起变更、核销。退回必须说明原因；无效状态或闸门失败通常返回 409，缺少操作人返回 400，提报不存在返回 404。`force` 只绕过对应有效闸门并留下记录，不构成权限授权。

接口仅供本地可信客户端使用；未提供认证和完整的统一参数校验。`/api/clear` 与旧计划导入可绕开页面的正常审批流程，集成时应限制使用。
