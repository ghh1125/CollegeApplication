CREATE TABLE IF NOT EXISTS school_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_code TEXT NOT NULL,
    school_name TEXT NOT NULL,
    province TEXT,
    city TEXT,
    school_level TEXT,
    school_type TEXT,
    ruanke_rank INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    CONSTRAINT school_master_unique UNIQUE (school_code)
);

CREATE TABLE IF NOT EXISTS major_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    major_code TEXT NOT NULL,
    major_name TEXT NOT NULL,
    discipline_category TEXT,
    major_category TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    CONSTRAINT major_master_unique UNIQUE (major_code)
);

CREATE TABLE IF NOT EXISTS admission_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    province TEXT NOT NULL,
    batch TEXT NOT NULL,
    recruit_type TEXT DEFAULT 'MAJOR' CHECK (recruit_type IN ('MAJOR', 'CATEGORY')),
    school_code TEXT NOT NULL,
    school_name TEXT NOT NULL,
    major_code TEXT NOT NULL,
    major_name TEXT NOT NULL,
    plan_count INTEGER,
    subject_requirement TEXT,
    subject_requirement_text TEXT,
    subject_requirement_json TEXT,
    school_location TEXT,
    tuition INTEGER,
    duration TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    subject_req_source TEXT,
    need_review INTEGER DEFAULT 0,
    CONSTRAINT admission_plan_unique UNIQUE (
        year,
        province,
        batch,
        school_code,
        major_code
    )
);

CREATE TABLE IF NOT EXISTS historical_cutoff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    province TEXT NOT NULL,
    batch TEXT NOT NULL,
    school_code TEXT NOT NULL,
    school_name TEXT NOT NULL,
    major_code TEXT NOT NULL,
    major_name TEXT NOT NULL,
    min_score INTEGER,
    min_rank INTEGER,
    plan_count INTEGER,
    avg_score REAL,
    enrollment_count INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    CONSTRAINT historical_cutoff_unique UNIQUE (
        year,
        province,
        batch,
        school_code,
        major_code
    )
);

CREATE TABLE IF NOT EXISTS program_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_school_code TEXT NOT NULL,
    source_major_code TEXT NOT NULL,
    school_code TEXT NOT NULL,
    major_code TEXT NOT NULL,
    normalized_program_name TEXT NOT NULL,
    mapping_direction TEXT DEFAULT 'BIDIRECTIONAL',
    valid_from_year INTEGER,
    valid_to_year INTEGER,
    need_human_review INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    CONSTRAINT program_mapping_unique UNIQUE (
        source_school_code,
        source_major_code,
        school_code,
        major_code
    )
);

CREATE TABLE IF NOT EXISTS admission_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    province TEXT NOT NULL,
    school_code TEXT NOT NULL,
    school_name TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    source_url TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    CONSTRAINT admission_rule_unique UNIQUE (
        year,
        province,
        school_code,
        rule_type
    )
);

CREATE TABLE IF NOT EXISTS major_admission_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    school_name TEXT NOT NULL,
    major_name TEXT NOT NULL,
    normalized_major_name TEXT,
    physical_exam_required INTEGER,
    physical_exam_detail TEXT,
    foreign_language_required TEXT,
    min_single_subject_scores TEXT,
    is_5year INTEGER DEFAULT 0,
    campus_location TEXT,
    special_requirements TEXT,
    source_url TEXT,
    parsed_by_llm INTEGER DEFAULT 0,
    human_verified INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    CONSTRAINT major_admission_rule_unique UNIQUE (year, school_name, major_name)
);

CREATE TABLE IF NOT EXISTS discipline_evaluation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discipline_code TEXT NOT NULL,
    discipline_name TEXT NOT NULL,
    school_name TEXT NOT NULL,
    grade TEXT NOT NULL,
    UNIQUE(discipline_code, school_name)
);

CREATE TABLE IF NOT EXISTS major_description (
    special_id   INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    national_code TEXT,
    level1       TEXT,
    level2       TEXT,
    level3       TEXT,
    is_what      TEXT,
    learn_what   TEXT,
    do_what      TEXT,
    keywords     TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS major_subject_requirement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_major_name TEXT NOT NULL,
    major_category TEXT,
    requirement_type TEXT NOT NULL,
    requirement_subjects TEXT,
    requirement_text TEXT,
    source TEXT DEFAULT 'STANDARD',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(normalized_major_name, source)
);
