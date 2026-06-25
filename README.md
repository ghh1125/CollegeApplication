# 浙江高考志愿推荐系统

输入位次和偏好，自动生成冲稳保梯度志愿方案。

**在线体验**：[https://collegeapplication-imqdnkfg5pggujsmkofkbo.streamlit.app](https://collegeapplication-imqdnkfg5pggujsmkofkbo.streamlit.app)

> **注意事项**：本工具基于历史数据生成参考方案，不能保证录取结果。历史位次每年会有波动，部分学校/专业数据可能存在缺失或误差。**最终填报请以浙江省教育考试院官方公布的数据为准，建议同时参考学校招生章程和专业录取规则。**
>
> 本工具输出的志愿方案仅供参考，不构成填报建议。是否填报某所学校或专业，取决于考生本人的意愿、家庭情况和综合判断，请结合实际情况自行决定。

---

## 支持范围

浙江 3+3（7选3），志愿单位是「学校+专业」，最多 80 个，2023–2026 年历史/招生数据。

**三步生成流程：**

1. **初步筛选**：选考科目 / 学科门类 / 地域偏好 / 体检限制 / 经济预算 / 单科成绩（必填）。结果表格按「省份（浙江最前）→ 软科院校排名」排序，**院校名称可直接点击跳转招生官网**
2. **二轮意向过滤**：三选一过滤模式（不过滤 / 剔除不想要的专业 / 只保留偏好专业），通过级联选择器（学科门类 → 专业类 → 具体专业）选取目标专业；可开启预警专业过滤（2020–2024 撤销布点 Top30）
3. **三轮分档 → 推荐 80 志愿**：先按位次将二轮候选池分入冲/稳/保三档（无数量限制），再按比例从三档中选出 80 个推荐志愿；同时单独列出「2026 新专业/新招生方向」（招生计划里有但无 2025 历史位次的专业，不参与冲稳保分档，需人工核实）。支持导出 Excel（5 个 sheet）/ CSV

**冲稳保分档规则（以考生位次 R 为基准）：**

| 位次段 | 冲间距 | 稳范围 | 保范围 | 冲/稳/保数量 |
|--------|--------|--------|--------|------------|
| ≤ 5000 | 每 50 名 | +1000 以内 | +5000 以内 | 20/40/20 |
| 5001–10000 | R ÷ 70 | +2000 以内 | +10000 以内 | 20/40/20 |
| 10001–50000 | R ÷ 100 | +3000 以内 | +15000 以内 | 20/30/30 |
| 50001+ | 每 500 名 | +3000 以内 | +15000 以内 | 20/30/30 |

---

## 项目结构

```
.
├── main.py                      # 网页应用入口（落地页 + 省份路由）
├── db.py                        # 数据库连接
│
├── src/zhejiang/
│   ├── step1_screen.py          # 第一步：按选科/学科/预算/地域/体检/单科初筛，铺出候选池（含软科排名、招生官网）
│   ├── step2_filter.py          # 第二步：意向过滤（排除/偏好具体专业 + 预警过滤）
│   ├── step3_generate.py        # 第三步：冲稳保梯度采样生成80志愿；build_new_major_table 列出无2025位次的2026新专业
│   ├── reference.py             # 985 / 211 / 双一流学校名单
│   ├── rank_utils.py            # 专业名归一化 + 学科评估代码映射
│   └── input/
│       ├── student_input.py     # StudentInput 数据模型（位次/选科/预算/地域/体检/单科）
│       ├── disciplines.py       # 学科门类·专业类代码常量（对齐教育部 2026 版目录）
│       └── medical_rules.py     # 体检受限专业规则（国家标准）
│
├── ui/
│   └── zhejiang_page.py         # 浙江页面（三步 UI；院校名称可点击链接招生官网；新专业表；Excel/CSV导出）
│
├── data/
│   └── zhejiang/
│       ├── college.db           # 主数据库（见下方数据表说明）
│       ├── schema.sql           # 表结构
│       └── raw/                 # 历史位次 CSV、选科要求、专业目录 PDF、招录原始数据
│
└── scripts/                     # 数据处理脚本（建库、抓取、解析）
    ├── init_db.py               # 建表 / schema 初始化（含旧库列迁移）
    ├── load_data.py             # 导入数据
    ├── fetch_zhejiang_plans.py  # 抓取浙江招生计划
    ├── import_zhejiang_enrollment_2026.py  # 导入2026浙江招生计划（admission_plan_2026）
    ├── fetch_baoyan_rate.py     # 抓取保研率
    ├── fetch_tuition_duration.py# 抓取学费/学制
    ├── fetch_school_websites.py # 抓取浙江招生院校官网链接
    ├── scrape_chsi_*.py         # 阳光高考章程抓取
    ├── parse_charter_requirements.py  # 解析章程选科/单科要求
    ├── build_school_master.py   # 构建学校主表
    ├── fetch_ruanke_major_rank.py   # 抓取软科中国大学专业排名（2026，838 专业 × 1136 所学校）
    ├── fetch_ruanke_school_rank.py  # 抓取软科中国大学排名全部14个子榜（主榜/医药/财经/政法等），用rankOverall统一映射主榜排名
    ├── fetch_qianwen_2026_major_codes.py  # 登录千问志愿推荐工具，抓取浙江2026真实专业代号
    └── clean_2026_training_notes.py # 剥离2026专业名称里混入的校区/外语门槛说明，避免污染历史位次匹配
```

---

## 数据库表说明（college.db）

| 表名 | 行数 | 关键字段 | 用途 |
|------|------|---------|------|
| `admission_plan` | 23,531 | `school_name` `major_name` `major_code` `subject_requirement` `tuition` `duration` `year` | 2023–2025 浙江招生计划，含选科要求、学费、学制 |
| `admission_plan_2026` | 24,340 | `school_name` `major_name` `major_code` `province_major_code` `training_note` `source_url` `source_major` | 2026 浙江招生计划；`province_major_code` 是千问抓取的真实浙江专业代号（97.8%覆盖）；`training_note` 是从专业名称剥离出的校区/外语门槛说明；与历史位次（2025及更早）对不上的专业即「2026新专业」，第三步单独列出 |
| `historical_cutoff` | 66,563 | `school_name` `major_name` `min_rank` `min_score` `year` | 历年最低录取位次/分数，第一步筛选和冲稳保判断的核心数据 |
| `major_description` | 1,355 | `national_code` `name` `is_what` `learn_what` `do_what` | 教育部本科专业目录（2026 版），883 个三级专业 + 部分专业画像 |
| `major_profile` | 3,139 | `major_name` `career_direction` `summary` `fallback_from` | 专业画像（发展路径、学什么、做什么）；含 845 个标准专业 + 1,796 个浙江招生别名映射，浙江覆盖率 82% |
| `major_profile_source` | 845 | `major_name` `career_direction` `fresh_salary` `top_position` `top_industry` | 专业画像（标准本科专业，含薪资/岗位/行业分布） |
| `school_profile` | 3,188 | `school_name` `ruanke_rank` `recommend_master_rate` `undergraduate_admission_url` | 学校画像（保研率、软科排名、招生官网链接，2,975/3,188 所有官网） |
| `school_master` | 3,285 | `school_name` `school_code` `province` `school_level` `ruanke_rank` | 学校基础信息（985/211/双一流标记、省份、排名） |
| `discipline_evaluation` | 5,112 | `school_name` `discipline_code` `discipline_name` `grade` | 教育部第四轮学科评估全量（96 个一级学科，460 所学校，A+/A/A-/B+…） |
| `ruanke_major_rank` | 31,043 | `major_code` `school_name` `major_name` `ranking` `grade` `score` `year` | 软科中国大学专业排名（2026 版），838 个专业 × 1,136 所学校，含排名名次、A+/A/B+等评级、综合评分 |
| `ruanke_school_rank` | 1,902 | `school_name` `rank_type` `rank_value` `rank_overall` `score` `year` | 软科中国大学排名（2026版）全部14个子榜（主榜/医药/财经/政法/语言/民办等），`rank_overall` 是子榜学校对应的主榜排名 |
| `admission_charter` | 2,860 | `school_name` `year` `content` `tuition_text` `physical_requirement_text` | 各高校招生章程（2026 版），覆盖浙江招生学校 99% |
| `major_subject_requirement` | 2,611 | `normalized_major_name` `requirement_type` `requirement_subjects` | 专业单科成绩最低要求汇总 |
| `city_profile` | 326 | `city_name` `province` `city_tier` `is_capital` | 城市画像（城市等级、是否省会） |

---

## 快速开始

### 环境要求

- Python 3.10+
- uv（推荐）或 pip

### 安装

```bash
git clone https://github.com/ghh1125/CollegeApplication.git
cd CollegeApplication
uv sync          # 或 pip install -r requirements.txt
```

### 运行

```bash
streamlit run main.py
```

---

## 数据来源

本项目数据均来自公开渠道，仅供学习和参考使用，不做商业用途。

| 数据内容 | 来源 |
|---------|------|
| 历史录取位次 / 招生计划 | [阳光高考 chsi.com.cn](https://gaokao.chsi.com.cn)（教育部主管的官方平台） |
| 位次分段表 / 选考科目要求 | [浙江省教育考试院 zjzs.net](https://www.zjzs.net) |
| 学校基本信息 / 专业介绍 | [阳光高考 static-data.gaokao.cn](https://static-data.gaokao.cn) |
| 本科专业介绍（发展路径/薪资/岗位） | 公开专业介绍数据（845 个标准本科专业） |
| 第四轮学科评估 | [教育部学位中心 kaoyan.eol.cn](https://kaoyan.eol.cn)（官方 2017 年公布数据） |
| 大学综合排名 | [软科中国大学排名 2026](https://www.shanghairanking.cn/rankings/bcur/2026) |
| 软科专业排名 | [软科中国大学专业排名 2026](https://www.shanghairanking.cn/rankings/bcmr/2026)（838 专业） |
| 学费 / 学制 | 浙江招录平台（公开招生计划页） |
| 单科成绩要求 | 全国高校单科成绩要求汇总（公开整理） |
| 本科专业目录 | 2026 版教育部本科专业目录 |
| 招生章程 | 各高校 2026 年招生章程（约 1000 所） |

**版权说明**：本项目不持有、不分发任何受版权保护的原始数据集，仅供个人学习使用，请遵守各平台服务条款。最终填报请以浙江省教育考试院官方信息为准。
