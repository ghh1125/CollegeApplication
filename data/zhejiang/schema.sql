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

CREATE TABLE IF NOT EXISTS school_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_name TEXT NOT NULL,
    school_id TEXT,
    summary TEXT,
    content TEXT,
    tags TEXT,
    motto TEXT,
    founded_year TEXT,
    school_type TEXT,
    school_nature TEXT,
    education_level TEXT,
    master_count INTEGER,
    doctor_count INTEGER,
    academician_count INTEGER,
    ruanke_rank INTEGER,
    source_name TEXT DEFAULT '阳光高考',
    source_url TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(school_name)
);

CREATE TABLE IF NOT EXISTS school_info_section (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_name TEXT NOT NULL,
    school_id TEXT,
    section_key TEXT NOT NULL,
    section_title TEXT NOT NULL,
    summary TEXT,
    content TEXT,
    source_name TEXT DEFAULT '阳光高考',
    source_url TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(school_name, section_key)
);

CREATE TABLE IF NOT EXISTS admission_charter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    school_name TEXT NOT NULL,
    school_id TEXT,
    province_scope TEXT DEFAULT '浙江',
    title TEXT,
    content TEXT,
    content_html TEXT,
    image_urls TEXT,
    source_name TEXT DEFAULT '高校官网',
    source_url TEXT,
    source_type TEXT DEFAULT 'html',
    fetch_status TEXT DEFAULT 'ok',
    ocr_status TEXT DEFAULT 'not_needed',
    tuition_text TEXT,
    housing_fee_text TEXT,
    admission_rules_text TEXT,
    language_requirement_text TEXT,
    physical_requirement_text TEXT,
    contact_text TEXT,
    plan_policy_text TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(year, school_name)
);

CREATE TABLE IF NOT EXISTS major_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    major_name TEXT NOT NULL,
    special_id INTEGER,
    summary TEXT,
    learn_what TEXT,
    career_direction TEXT,
    keywords TEXT,
    fallback_from TEXT,
    source_name TEXT DEFAULT '阳光高考',
    source_url TEXT,
    qianwen_code TEXT,
    qianwen_discipline_category TEXT,
    qianwen_major_category TEXT,
    qianwen_length TEXT,
    qianwen_degree TEXT,
    qianwen_popular_value INTEGER,
    qianwen_subject_suggestion TEXT,
    qianwen_salary_summary TEXT,
    qianwen_gender_ratio_json TEXT,
    qianwen_salary_chart_json TEXT,
    qianwen_employment_area_json TEXT,
    qianwen_position_json TEXT,
    qianwen_industry_json TEXT,
    qianwen_related_majors_json TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(major_name)
);

CREATE TABLE IF NOT EXISTS qianwen_major_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    major_name TEXT NOT NULL,
    major_code TEXT,
    discipline_category TEXT,
    major_category TEXT,
    length TEXT,
    degree TEXT,
    popular_value INTEGER,
    summary TEXT,
    learn_what TEXT,
    career_direction TEXT,
    subject_suggestion TEXT,
    baike_text TEXT,
    baike_url TEXT,
    fresh_salary INTEGER,
    top_city TEXT,
    top_position TEXT,
    top_industry TEXT,
    gender_ratio_json TEXT,
    salary_chart_json TEXT,
    employment_area_json TEXT,
    position_distribution_json TEXT,
    industry_distribution_json TEXT,
    related_majors_json TEXT,
    opening_colleges_json TEXT,
    recommended_majors_json TEXT,
    raw_json TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(major_name)
);

CREATE TABLE IF NOT EXISTS city_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_name TEXT NOT NULL,
    province TEXT,
    city_tier INTEGER,
    tier_label TEXT,
    is_capital INTEGER DEFAULT 0,
    summary TEXT,
    gdp TEXT,
    population TEXT,
    industry_summary TEXT,
    employment_summary TEXT,
    source_name TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(city_name, province)
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
