# 浙江高考志愿推荐系统

输入位次和偏好，自动生成冲稳保梯度志愿方案。

**在线体验**：[https://collegeapplication-imqdnkfg5pggujsmkofkbo.streamlit.app](https://collegeapplication-imqdnkfg5pggujsmkofkbo.streamlit.app)

> **注意事项**：本工具基于历史数据生成参考方案，不能保证录取结果。历史位次每年会有波动，部分学校/专业数据可能存在缺失或误差。**最终填报请以浙江省教育考试院官方公布的数据为准，建议同时参考学校招生章程和专业录取规则。**
>
> 本工具输出的志愿方案仅供参考，不构成填报建议。是否填报某所学校或专业，取决于考生本人的意愿、家庭情况和综合判断，请结合实际情况自行决定。

---

## 支持范围

浙江 3+3（7选3），志愿单位是「学校+专业」，最多 80 个，2023–2025 年历史数据。

**三步生成流程：**

1. **初步筛选**：选考科目 / 学科门类 / 地域偏好 / 体检限制 / 经济预算 / 单科成绩（必填）
2. **二轮意向过滤**：可排除/偏好指定门类·专业类·具体专业，可开启预警专业过滤（2020–2024 撤销布点 Top30）
3. **三轮分档 → 参考 80 志愿**：先按位次将二轮候选池分入冲/稳/保三档（无数量限制），确认后再按比例从三档中选出 80 个参考志愿

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
├── main.py                      # 网页应用入口
├── db.py                        # 数据库连接
├── config.py                    # 配置（API Key 等）
│
├── src/zhejiang/
│   ├── step1_screen.py          # 第一步：按选科/学科/预算/地域/体检/单科初筛，铺出候选池
│   ├── step2_filter.py          # 第二步：意向过滤（排除/偏好门类·专业类·具体专业 + 预警过滤）
│   ├── step3_generate.py        # 第三步：冲稳保梯度采样，生成最终 80 个志愿
│   ├── persona.py               # 用户分档（顶尖/高分/中坚/一段线下）
│   └── input/
│       ├── student_input.py     # StudentInput 数据模型（位次/选科/预算/地域/体检/单科）
│       ├── disciplines.py       # 学科门类·专业类代码常量（对齐教育部 2026 版目录）
│       └── medical_rules.py     # 体检受限专业规则（国家标准）
│
├── ui/
│   └── zhejiang_page.py         # 浙江页面（三步 UI，调用 src/zhejiang/ 各步骤）
│
├── data/
│   └── zhejiang/
│       ├── college.db           # 主数据库（见下方数据表说明）
│       ├── schema.sql           # 表结构
│       └── raw/                 # 历史位次 CSV、选科要求、专业目录 PDF、招录原始数据
│
└── scripts/                     # 数据处理脚本（建库、抓取、解析）
```

---

## 数据库表说明（college.db）

| 表名 | 行数 | 关键字段 | 用途 |
|------|------|---------|------|
| `admission_plan` | 23,531 | `school_name` `major_name` `major_code` `subject_requirement` `tuition` `duration` `year` | 2023–2025 浙江招生计划，含选科要求、学费、学制 |
| `historical_cutoff` | 66,563 | `school_name` `major_name` `min_rank` `min_score` `year` | 历年最低录取位次/分数，第一步筛选和冲稳保判断的核心数据 |
| `major_description` | 1,355 | `national_code` `name` `is_what` `learn_what` `do_what` | 教育部本科专业目录（2026 版），883 个三级专业 + 部分专业画像 |
| `major_profile` | 3,139 | `major_name` `career_direction` `summary` `fallback_from` | 专业画像（发展路径、学什么、做什么）；含 845 个标准专业 + 1,796 个浙江招生别名映射，浙江覆盖率 82% |
| `major_profile_source` | 845 | `major_name` `career_direction` `fresh_salary` `top_position` `top_industry` | 专业画像（标准本科专业，含薪资/岗位/行业分布） |
| `school_profile` | 3,144 | `school_name` `ruanke_rank` `recommend_master_rate` | 学校画像（保研率、软科排名） |
| `school_master` | 3,285 | `school_name` `school_code` `province` `school_level` `ruanke_rank` | 学校基础信息（985/211/双一流标记、省份、排名） |
| `discipline_evaluation` | 5,112 | `school_name` `discipline_code` `discipline_name` `grade` | 教育部第四轮学科评估全量（96 个一级学科，460 所学校，A+/A/A-/B+…） |
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

### 配置

创建 `.env` 文件：

```
DASHSCOPE_API_KEY=your_key_here
```

AI 对话功能需要阿里云百炼的 API Key，志愿生成本身不需要。

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
| 大学排名 | [软科 shanghairanking.cn](https://www.shanghairanking.cn) |
| 学费 / 学制 | 浙江招录平台（公开招生计划页） |
| 单科成绩要求 | 全国高校单科成绩要求汇总（公开整理） |
| 本科专业目录 | 2026 版教育部本科专业目录 |
| 招生章程 | 各高校 2026 年招生章程（约 1000 所） |

**版权说明**：本项目不持有、不分发任何受版权保护的原始数据集，仅供个人学习使用，请遵守各平台服务条款。最终填报请以浙江省教育考试院官方信息为准。
