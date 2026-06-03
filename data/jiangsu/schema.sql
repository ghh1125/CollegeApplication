-- 江苏省数据库 schema（3+1+2 院校专业组模式）
--
-- 设计原则：
--   1. 表名沿用通用名 historical_cutoff / admission_plan，使 src/common 的
--      attach_history / load_admission_plans 查询零改动复用。
--   2. 江苏特有的"院校专业组"信息以扩展列承载：
--        subject_category  物理类 / 历史类（首选科目科类，江苏分两个独立位次池）
--        special_group     专业组唯一ID（来自掌上高考 special_group）
--        sg_name           专业组号（如 "08"）
--        sg_info           组的选科要求（如 "首选物理，再选化学"）
--   3. 录取数据为"每专业一行"，位次粒度与浙江一致（min_rank = 掌上高考 min_section），
--      common 的 calculate_gap 直接复用；江苏 recommend 再按 special_group 聚合为志愿单位。
--   4. 全国通用表（school_master / discipline_evaluation / major_description /
--      major_subject_requirement）由 ingest 从 data/common/common.db 拷入，不在此定义。

-- 历史录取（每专业一行，含专业组列）
CREATE TABLE IF NOT EXISTS historical_cutoff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    province TEXT NOT NULL DEFAULT '江苏',
    subject_category TEXT NOT NULL,          -- 物理类 / 历史类
    batch TEXT NOT NULL DEFAULT '本科批',
    school_code TEXT NOT NULL,               -- 掌上高考 school_id
    school_name TEXT NOT NULL,
    special_group TEXT,                      -- 专业组唯一ID
    sg_name TEXT,                            -- 专业组号
    sg_info TEXT,                            -- 选科要求文本（首选/再选）
    major_code TEXT,                         -- 掌上高考 spcode
    major_name TEXT NOT NULL,
    min_score INTEGER,                       -- 录取最低分
    min_rank INTEGER,                        -- 录取最低位次（掌上高考 min_section）
    plan_count INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    CONSTRAINT historical_cutoff_unique UNIQUE (
        year, subject_category, school_code, special_group, major_name
    )
);

-- 招生计划（每专业一行，含专业组列）
CREATE TABLE IF NOT EXISTS admission_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    province TEXT NOT NULL DEFAULT '江苏',
    subject_category TEXT NOT NULL,          -- 物理类 / 历史类
    batch TEXT NOT NULL DEFAULT '本科批',
    recruit_type TEXT DEFAULT 'MAJOR',
    school_code TEXT NOT NULL,               -- 掌上高考 school_id
    school_name TEXT NOT NULL,
    special_group TEXT,                      -- 专业组唯一ID
    sg_name TEXT,                            -- 专业组号
    sg_info TEXT,                            -- 选科要求文本（首选/再选）
    major_code TEXT,                         -- 掌上高考 spcode
    major_name TEXT NOT NULL,
    plan_count INTEGER,
    subject_requirement TEXT,                -- 原始选科要求文本（= sg_info）
    subject_requirement_text TEXT,
    subject_requirement_json TEXT,           -- 结构化首选/再选（江苏 filter 解析用）
    subject_req_source TEXT,
    need_review INTEGER DEFAULT 0,
    school_location TEXT,
    tuition INTEGER,
    duration TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    CONSTRAINT admission_plan_unique UNIQUE (
        year, subject_category, school_code, special_group, major_name
    )
);

CREATE INDEX IF NOT EXISTS idx_js_cutoff_lookup
    ON historical_cutoff (subject_category, school_name, major_name, year);
CREATE INDEX IF NOT EXISTS idx_js_plan_group
    ON admission_plan (subject_category, school_code, special_group);
