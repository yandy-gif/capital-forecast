PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 原始层：银行流水原样入库，不做任何加工（可回溯）
CREATE TABLE IF NOT EXISTS ods_flow (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  batch       TEXT NOT NULL,                 -- 批次号 yyyymmdd-HHMMSS
  file_name   TEXT NOT NULL,
  row_no      INTEGER,
  row_json    TEXT NOT NULL,                 -- 标准化后的字段 JSON
  ingested_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_ods_flow_batch ON ods_flow(batch);

-- 明细层：资金计划
CREATE TABLE IF NOT EXISTS dwd_plan (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  batch       TEXT NOT NULL,
  file_name   TEXT NOT NULL,
  month       TEXT,
  direction   TEXT,
  cashflow    TEXT,
  subject     TEXT,
  source      TEXT,
  amount      REAL,
  tax_flag    TEXT,
  note        TEXT,
  row_json    TEXT,
  ingested_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_dwd_plan_month ON dwd_plan(month);

-- 人工标签：业务键（日期|方向|金额|户名）→ 三个科目字段，跨批次生效
CREATE TABLE IF NOT EXISTS dwd_manual (
  uniq_key    TEXT PRIMARY KEY,
  trade_date  TEXT,
  amount      REAL,
  counterparty TEXT,
  l1          TEXT,
  l2          TEXT,
  cashflow    TEXT,
  confirmer   TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 规则版本（前端导出的规则集，保证数仓与看板同源）
CREATE TABLE IF NOT EXISTS dwd_rule (
  id          TEXT PRIMARY KEY,
  payload     TEXT NOT NULL,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 元数据：当前生效批次
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);

-- ============ 生态多主体 · 资金计划（模板 / 提报 / 审批 / 参数 / 桥接） ============

-- 模板注册表：系统里能下载哪些模板、哪一版、绑哪套规则
CREATE TABLE IF NOT EXISTS tpl_registry (
  id           TEXT PRIMARY KEY,             -- TPL-05
  name         TEXT NOT NULL,
  version      TEXT NOT NULL,
  scope        TEXT,                         -- 适用主体/板块
  fields       INTEGER,
  rule_ver     TEXT,                         -- 绑定规则版本
  effective    TEXT,
  file_path    TEXT,                         -- 相对项目根目录
  status       TEXT DEFAULT '启用',           -- 启用 / 停用
  downloads    INTEGER DEFAULT 0,
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 科目字典（模板「科目字典」页，全集团统一口径；C-09 靠它闭合）
CREATE TABLE IF NOT EXISTS subject_dict (
  direction  TEXT NOT NULL,                  -- 流入 / 流出
  cat        TEXT NOT NULL,                  -- 经营活动/投资活动/筹资活动/内部往来
  subject    TEXT NOT NULL,                  -- 科目明细
  cf_attr    TEXT,                           -- 现金流属性
  note       TEXT,
  tpl_ver    TEXT DEFAULT 'v1.0',
  PRIMARY KEY (direction, subject)
);

-- 主体台账（模板「主体台账」页）
CREATE TABLE IF NOT EXISTS entity_ledger (
  name         TEXT PRIMARY KEY,
  segment      TEXT,                         -- 业务板块
  region       TEXT,                         -- 境内 / 境外(xx)
  currency     TEXT,
  account      TEXT,
  consolidated TEXT,
  pooling      TEXT,                         -- 资金归集方式
  owner        TEXT,                         -- 财务责任人
  note         TEXT
);

-- 记账汇率（可调整业务参数的一部分，单列便于模板折算）
CREATE TABLE IF NOT EXISTS fx_rate (
  currency    TEXT PRIMARY KEY,
  name        TEXT,
  rate_cny    REAL NOT NULL,
  updated_at  TEXT,
  updated_by  TEXT
);

-- 提报主记录：主体 × 期间 × 版本，承载审批状态
CREATE TABLE IF NOT EXISTS plan_submission (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  entity       TEXT,                         -- 主体；多主体合并提报时为「多主体」
  period       TEXT NOT NULL,                -- YYYY-MM
  version      INTEGER NOT NULL DEFAULT 1,
  file_name    TEXT,
  file_sha     TEXT,                         -- 上传文件 SHA256，文件级去重（重复上传拦截）
  tpl_ver      TEXT,
  rule_ver     TEXT,
  status       TEXT NOT NULL DEFAULT '草稿',  -- 草稿/待审/待批/已锁定/已核销/退回/变更中
  item_count   INTEGER DEFAULT 0,
  inflow       REAL DEFAULT 0,
  outflow      REAL DEFAULT 0,
  net          REAL DEFAULT 0,
  gate_report  TEXT,                         -- JSON：G1/G2/G3 校验结果
  submitter    TEXT, submitted_at  TEXT,
  reviewer     TEXT, reviewed_at   TEXT, review_note TEXT,
  approver     TEXT, approved_at   TEXT,
  reject_by    TEXT, rejected_at   TEXT, reject_reason TEXT,
  locked_at    TEXT,
  replaced_by  INTEGER,                      -- 变更后指向新版本 id
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_plan_sub_period ON plan_submission(period, entity);
CREATE INDEX IF NOT EXISTS ix_plan_sub_status ON plan_submission(status);

-- 提报明细行（对应模板「预算明细」一行）
CREATE TABLE IF NOT EXISTS plan_item (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sub_id      INTEGER NOT NULL,
  seq         INTEGER,
  entity      TEXT,
  segment     TEXT,
  region      TEXT,
  currency    TEXT,
  direction   TEXT,                          -- 流入 / 流出
  cat         TEXT,                          -- 科目大类
  subject     TEXT,                          -- 科目明细
  timing      TEXT,                          -- 资金时点：上旬/中旬/下旬
  amt_orig    REAL,
  amt_cny     REAL,
  owner       TEXT,
  item_status TEXT,
  note        TEXT,
  src_row     INTEGER,                       -- 源表行号，可追溯
  flag        TEXT                           -- 校验标记：ok/warn/error + 说明
);
CREATE INDEX IF NOT EXISTS ix_plan_item_sub ON plan_item(sub_id);
CREATE INDEX IF NOT EXISTS ix_plan_item_key ON plan_item(entity, subject);

-- 审批留痕：谁在什么时候把状态从 A 改到 B、为什么
CREATE TABLE IF NOT EXISTS approval_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  sub_id        INTEGER NOT NULL,
  action        TEXT NOT NULL,               -- 创建/提交/复核通过/退回/批准/锁定/发起变更/核销
  from_status   TEXT,
  to_status     TEXT,
  actor         TEXT,
  role          TEXT,
  reason        TEXT,
  amount_before REAL,
  amount_after  REAL,
  gate          TEXT,                        -- 触发的闸门 JSON 摘要
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_appr_log_sub ON approval_log(sub_id);

-- 可调整业务参数（账期、回款比例、安全水位、阈值…）：用户给什么就改什么
CREATE TABLE IF NOT EXISTS biz_param (
  id          TEXT PRIMARY KEY,              -- 如 term.default / th.var_warn
  grp         TEXT NOT NULL,                 -- 分组：汇率/账期/回款/水位/阈值/口径
  label       TEXT NOT NULL,
  value       TEXT NOT NULL,
  unit        TEXT,
  scope       TEXT,                          -- 适用主体/客户，空=全局
  note        TEXT,
  locked      INTEGER DEFAULT 0,             -- 1=系统内置不建议改
  updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_by  TEXT
);
CREATE INDEX IF NOT EXISTS ix_biz_param_grp ON biz_param(grp);

-- 科目桥接：流水「方向+一级+二级」→ 计划「科目明细」（预实比对的主路径）
CREATE TABLE IF NOT EXISTS flow_bridge (
  id          TEXT PRIMARY KEY,              -- B-06
  direction   TEXT,                          -- 收 / 支 / 收/支 / 全
  l1          TEXT,                          -- 流水一级科目（多个用 / 分隔）
  l2          TEXT,                          -- 流水二级科目（多个用 / 分隔）
  subject     TEXT,                          -- → 计划科目明细；（剔除）/（投资活动）等
  cashflow    TEXT,                          -- 收支分类
  handle      TEXT,                          -- 归集/冲减（负数）/剔除/单列
  priority    INTEGER DEFAULT 5,
  status      TEXT DEFAULT '启用',
  note        TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 客户账期（预实比对的窗口口径）
CREATE TABLE IF NOT EXISTS cust_term (
  customer   TEXT PRIMARY KEY,
  klass      TEXT,                           -- KA / 其他经销商 / 中心化
  term_days  INTEGER NOT NULL DEFAULT 30,
  note       TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_by TEXT
);

-- 明细层匹配关系：一条计划行 ↔ 多笔流水（多对多，不在流水上写死科目）
CREATE TABLE IF NOT EXISTS match_link (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sub_id      INTEGER,
  item_id     INTEGER,
  period      TEXT,
  entity      TEXT,
  subject     TEXT,
  flow_key    TEXT NOT NULL,                 -- 日期|方向|金额|户名
  flow_date   TEXT,
  flow_amount REAL,
  amount      REAL,                          -- 本次匹配金额（可拆分）
  basis       TEXT,                          -- 客户+科目 / 科目 / 摘要关键词 / 人工
  confirmer   TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_match_link_item ON match_link(item_id);
CREATE INDEX IF NOT EXISTS ix_match_link_key ON match_link(flow_key);

-- 导入日志
CREATE TABLE IF NOT EXISTS import_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,                 -- flow / plan
  file_name   TEXT,
  rows        INTEGER,
  batch       TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 明细层：已打标的流水（处理好的结果，看板直接读这一层，不再每次重算）
CREATE TABLE IF NOT EXISTS dwd_flow_tagged (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  batch        TEXT NOT NULL,
  month        TEXT,                          -- 2026.09
  entity       TEXT,                          -- 主体
  account      TEXT,                          -- 工行/招行/稠州…
  trade_date   TEXT,
  trade_time   TEXT,
  direction    TEXT,                          -- 收/支
  amount       REAL,
  balance      REAL,
  counterparty TEXT,                          -- 对方户名
  memo         TEXT,                          -- 摘要
  l1           TEXT,                          -- 收支科目(一级)
  l2           TEXT,                          -- 明细科目(二级)
  cashflow     TEXT,                          -- 现金流量分类
  cat          TEXT,                          -- 收支分类（销售收款/采购付款/人力费…）
  activity     TEXT,                          -- 经营/投资/内部/筹资
  actual_flag  TEXT,                          -- 实际/预计
  party_type   TEXT,                          -- 客户/供应商/员工/银行/平台/关联方/其他
  related      TEXT,                          -- 是否指定关联方
  tag_src      TEXT,                          -- 规则/人工/未匹配
  src_file     TEXT,
  confirmer    TEXT,
  tagged_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_tagged_month ON dwd_flow_tagged(month);
CREATE INDEX IF NOT EXISTS ix_tagged_batch ON dwd_flow_tagged(batch);

-- 源表转换/计划附页的追溯明细。新安装也必须具备，不能依赖旧数据库。
CREATE TABLE IF NOT EXISTS plan_appendix (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sub_id INTEGER NOT NULL,
  period TEXT,
  entity TEXT,
  version INTEGER,
  kind TEXT,
  customer TEXT,
  klass TEXT,
  product TEXT,
  dept TEXT,
  item TEXT,
  qty REAL,
  price REAL,
  rate REAL,
  amount REAL,
  src_row INTEGER,
  note TEXT
);
CREATE INDEX IF NOT EXISTS ix_appendix_sub ON plan_appendix(sub_id);
