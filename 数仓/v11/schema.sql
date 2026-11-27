-- v11 数仓表结构（SQLite）
-- 一条链路：申请(批次) → 解析落库 → 规则匹配 → 待确认池 → 人工确认 → 聚合 → 看板
-- 口径：看板实际数 = flow_raw.status='confirmed'（含规则匹配"已匹配"与人工确认）

PRAGMA journal_mode=WAL;

-- ---------- 主数据 ----------
CREATE TABLE IF NOT EXISTS dim_entity (
  short    TEXT PRIMARY KEY,
  name     TEXT NOT NULL,
  segment  TEXT,
  region   TEXT,
  ccy      TEXT DEFAULT 'CNY',
  core     TEXT,
  pending  INTEGER DEFAULT 0        -- 1 = 样表出现但无账户，待补登
);

CREATE TABLE IF NOT EXISTS dim_account (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entity     TEXT NOT NULL,
  bank       TEXT NOT NULL,
  account    TEXT,
  note       TEXT,
  restricted REAL DEFAULT 0,        -- 受限资金（人工维护）
  UNIQUE(entity, bank, account)
);

CREATE TABLE IF NOT EXISTS entity_rule (
  entity     TEXT PRIMARY KEY,
  safe_line  REAL DEFAULT 0,        -- 安全线（元），0=未设置，用建议值
  updated_at TEXT
);

-- 汇率：人工维护，不联网；历史期间取期间末生效汇率
CREATE TABLE IF NOT EXISTS fx_rate (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  pair           TEXT NOT NULL,
  rate           REAL NOT NULL,
  effective_date TEXT NOT NULL,     -- YYYY-MM-DD
  operator       TEXT,
  created_at     TEXT,
  UNIQUE(pair, effective_date)
);

-- 分类映射表：分类(22项) → 计划科目 → 现金流量表项目 → 活动类型
CREATE TABLE IF NOT EXISTS subject_map (
  code        TEXT PRIMARY KEY,     -- A01..G01
  l1          TEXT NOT NULL,        -- 经营活动/投资活动/筹资活动/内部往来
  l2          TEXT NOT NULL,        -- 22 项二级
  sub_detail  TEXT,                 -- 内部细分（写备注）
  direction   TEXT,                 -- 收/支/双向
  plan_subject TEXT,                -- 对应资金计划科目
  cf_section  TEXT,                 -- 现金流量表段落
  cf_item     TEXT,                 -- 现金流量表项目
  is_internal INTEGER DEFAULT 0,    -- 1 = 内部往来（现金流量表剔除）
  group_elim  INTEGER DEFAULT 0,    -- 1 = 集团汇总抵销
  sort        INTEGER DEFAULT 0
);

-- 匹配规则：技能规则库同步；人工确认优先沉淀回规则库，写失败时才临时反写（source=manual_back）
CREATE TABLE IF NOT EXISTS match_rule (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  pri        REAL NOT NULL,
  direction  TEXT DEFAULT '*',      -- 收/支/*
  scope      TEXT DEFAULT 'any',    -- cp / memo / any / person / both
  keyword    TEXT NOT NULL,         -- both 时为对方户名 pattern
  memo_pattern TEXT DEFAULT '',     -- both 时必须同时命中的摘要 pattern
  code       TEXT NOT NULL,
  enabled    INTEGER DEFAULT 1,
  source     TEXT DEFAULT 'skill',  -- skill / manual_back
  hit_count  INTEGER DEFAULT 0,
  last_hit   TEXT,
  entity     TEXT DEFAULT '*',      -- 主体简称；'*' 不限，逗号分隔
  account    TEXT DEFAULT '*',      -- 本方账号；'*' 不限，支持 * 通配
  rule_key   TEXT,                  -- 规则库 id（rules.json 的 id）
  created_at TEXT
);

-- 受限资金识别规则：摘要/户名关键词 → 受限类别（不改科目分类）
CREATE TABLE IF NOT EXISTS restricted_rule (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,           -- 保证金/未到期定期存款/境外受监管/质押存款/冻结/公管资产
  keyword    TEXT NOT NULL,           -- 命中即打标签（不分大小写）
  enabled    INTEGER DEFAULT 1,
  source     TEXT DEFAULT 'skill',    -- skill / manual
  hit_count  INTEGER DEFAULT 0,
  last_hit   TEXT,
  created_at TEXT,
  UNIQUE(kind, keyword)
);

-- ---------- 业务表 ----------
CREATE TABLE IF NOT EXISTS import_batch (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entity     TEXT NOT NULL,
  period     TEXT NOT NULL,          -- YYYY-MM
  kind       TEXT DEFAULT 'flow',
  file_name  TEXT,
  file_sha   TEXT,
  n_rows     INTEGER DEFAULT 0,
  n_confirmed INTEGER DEFAULT 0,
  n_pending  INTEGER DEFAULT 0,
  status     TEXT DEFAULT 'parsed',  -- draft / parsed / confirmed
  operator   TEXT,                   -- 创建人
  created_at TEXT,
  updated_at TEXT,                   -- 最后操作时间
  confirmed_at TEXT,
  confirmed_by TEXT,
  UNIQUE(entity, period, file_sha)
);

CREATE TABLE IF NOT EXISTS flow_raw (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id     INTEGER,
  entity       TEXT NOT NULL,
  bank         TEXT,
  account      TEXT,
  txn_date     TEXT NOT NULL,
  txn_time     TEXT,
  direction    TEXT NOT NULL,        -- 收/支
  amount       REAL NOT NULL,
  balance      REAL,
  balance_date TEXT,                 -- 余额时点；为空时使用交易日期
  counterparty TEXT,
  cp_account   TEXT,
  memo         TEXT,
  code         TEXT,                 -- A01.. 空=未命中
  l1           TEXT,                 -- 取自 subject_map
  l2           TEXT,
  sub_detail   TEXT,
  cf_section   TEXT,
  cf_item      TEXT,
  matched_by   TEXT,                 -- rule / manual / rule_back
  rule_id      INTEGER,
  confidence   TEXT,                 -- 高/中/低/未知
  status       TEXT DEFAULT 'pending',-- draft / pending / confirmed
  modified     INTEGER DEFAULT 0,    -- 1 = 确认后被改过，待重确认
  restricted_kind TEXT,              -- 受限资金类别（自动识别，空=不受限）
  offset_of      INTEGER,             -- 冲销指向的原笔 flow_raw.id
  src_file     TEXT,
  src_row      INTEGER,
  biz_key      TEXT,                 -- 去重键
  created_at   TEXT,
  updated_at   TEXT,
  FOREIGN KEY(batch_id) REFERENCES import_batch(id)
);
CREATE INDEX IF NOT EXISTS ix_flow_scope ON flow_raw(entity, txn_date);
CREATE INDEX IF NOT EXISTS ix_flow_status ON flow_raw(status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_flow_bizkey ON flow_raw(biz_key);

CREATE TABLE IF NOT EXISTS plan_batch (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  entity      TEXT NOT NULL,
  period      TEXT NOT NULL,
  file_name   TEXT,
  n_rows      INTEGER DEFAULT 0,
  inflow      REAL DEFAULT 0,
  outflow     REAL DEFAULT 0,
  version     INTEGER DEFAULT 1,      -- 变更一次 +1，保留版本历史
  parent_id   INTEGER,                -- 变更来源批次
  status      TEXT DEFAULT 'parsed',  -- parsed / submitted / approved / superseded / rejected
  operator    TEXT,                   -- 创建人
  created_at  TEXT,
  updated_at  TEXT,                   -- 最后操作时间
  submitted_at TEXT,
  submitted_by TEXT,
  approved_at TEXT,
  approved_by TEXT,
  reject_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_plan_batch_scope ON plan_batch(entity, period, status);

CREATE TABLE IF NOT EXISTS plan_item (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id  INTEGER NOT NULL,
  entity    TEXT NOT NULL,
  period    TEXT NOT NULL,
  direction TEXT NOT NULL,
  l1        TEXT,
  l2        TEXT,
  code      TEXT,
  amount    REAL DEFAULT 0,
  note      TEXT,
  status    TEXT DEFAULT 'draft',    -- 随批次：draft/pending/approved
  FOREIGN KEY(batch_id) REFERENCES plan_batch(id)
);
CREATE INDEX IF NOT EXISTS ix_plan_period ON plan_item(period, entity, status);

CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  target_type TEXT,
  target_id   INTEGER,
  action      TEXT,                  -- edit / confirm / rule_back / approve
  field       TEXT,
  before_val  TEXT,
  after_val   TEXT,
  operator    TEXT,
  reason      TEXT,
  created_at  TEXT
);

CREATE TABLE IF NOT EXISTS agg_meta (
  k TEXT PRIMARY KEY,
  v TEXT
);

-- ---------- 聚合视图（看板统一取数） ----------
CREATE VIEW IF NOT EXISTS v_flow_actual AS
  SELECT * FROM flow_raw WHERE status = 'confirmed';

CREATE VIEW IF NOT EXISTS v_plan_approved AS
  SELECT * FROM plan_item WHERE status = 'approved';

CREATE VIEW IF NOT EXISTS v_month_agg AS
  SELECT entity, substr(txn_date,1,7) AS period, direction,
         l1, l2, code, cf_section, cf_item,
         COUNT(*) AS n, SUM(amount) AS amt
  FROM v_flow_actual GROUP BY entity, period, direction, l1, l2, code, cf_section, cf_item;

CREATE VIEW IF NOT EXISTS v_pending AS
  SELECT * FROM flow_raw WHERE status IN ('pending','draft');
