# 高考志愿推荐系统

面向浙江高考的志愿填报推荐工具，基于历史录取位次、选科要求和学生偏好，生成冲稳保梯度志愿方案，并提供 AI 对话助手辅助分析。

**在线体验**：[https://collegeapplication-imqdnkfg5pggujsmkofkbo.streamlit.app](https://collegeapplication-imqdnkfg5pggujsmkofkbo.streamlit.app)

---

## 功能

- **智能筛选**：按选科要求、学校层次、城市、专业关键词过滤候选学校专业
- **位次匹配**：用近三年历史录取位次（加权 0.5/0.3/0.2）计算 gap，划分冲/稳/保/垫/高危冲五档
- **多维排序**：支持专业优先 / 学校优先 / 城市优先三种模式，各模式内按专业匹配度 × 学校质量 × 城市偏好排序
- **专业匹配**：五层精细匹配（精确 → 关键词 → 专业类 → 相近学科 → 无关），基于第四轮学科评估代码和学科亲缘 cluster
- **AI 对话**：小明助手支持自然语言填报、问卷引导（不知道读什么时逐题分析兴趣）、志愿解释、整体方案报告

---

## 项目结构

| 路径 | 说明 |
|------|------|
| `main.py` | Streamlit 应用入口 |
| `db.py` | SQLite 连接管理 |
| `config.py` | 环境变量配置（DashScope API Key 等） |
| `src/input/profile.py` | StudentProfile 数据模型 |
| `src/input/filter.py` | 候选池筛选（选科 + 硬约束 + 城市 + 专业关键词） |
| `src/input/llm.py` | AI 对话、参数提取、报告生成 |
| `src/input/ingest.py` | 原始数据导入 |
| `src/ranking/rank.py` | 位次计算、专业打分、排序、历史数据富化 |
| `src/ranking/profiles.py` | 学校/专业画像富化 |
| `src/allocation/builder.py` | 冲稳保志愿数量分配 |
| `src/allocation/recommend.py` | 端到端流水线组装 |
| `src/export/excel.py` | Excel 导出 |
| `ui/form_helpers.py` | Streamlit 表单辅助函数 |
| `data/college.db` | SQLite 数据库（学校/专业/历史录取/选科要求） |
| `data/schema.sql` | 数据库表结构 |
| `scripts/` | 数据构建脚本（初始化 DB、抓取数据等） |
| `tests/` | 单元测试 |

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

AI 对话功能（小明助手、报告生成）需要 DashScope API Key，志愿生成本身不需要。

### 运行

```bash
streamlit run main.py
```

---

## 核心模块接口

各子模块均可独立复用，输入输出明确：

| 模块 | 函数 | 输入 | 输出 |
|------|------|------|------|
| 筛选 | `load_admission_plans(conn, year)` | DB 连接 + 年份 | 招生计划列表 |
| 筛选 | `apply_subject_filter(programs, subjects)` | 计划列表 + 选科 | (合格, 排除) |
| 历史数据 | `load_all_history_data(conn, year)` | DB 连接 + 年份 | `HistoryData` |
| 历史数据 | `attach_history(candidates, data)` | 候选列表 + HistoryData | 富化后列表 |
| 专业匹配 | `_major_level(program, majors, cats)` | 专业信息 + 偏好 | 0-4 匹配等级 |
| 排序 | `sort_candidates(candidates, ...)` | 候选列表 + 偏好 + 模式 | 排序后列表 |
| 分配 | `build_volunteer_list(candidates, ...)` | 排序列表 + 风险偏好 | 志愿表 + 备选池 |

---

## 数据来源

本项目数据均来自公开渠道，仅供学习和参考使用，不做商业用途。

| 数据类型 | 来源 | 说明 |
|---------|------|------|
| 历史录取位次 / 招生计划 | [阳光高考 chsi.com.cn](https://gaokao.chsi.com.cn) | 教育部主管的官方高考信息平台 |
| 学校基本信息 / 专业介绍 | [阳光高考 static-data.gaokao.cn](https://static-data.gaokao.cn) | 同上，JSON 接口 |
| 软科大学排名 | [软科 shanghairanking.cn](https://www.shanghairanking.cn) | 手动整理，仅排名数字 |
| 第四轮学科评估结果 | [教育部](https://www.moe.gov.cn) | 2017年公开发布，存于 `data/discipline_eval.sql` |
| 城市 GDP / 人口数据 | [维基百科](https://zh.wikipedia.org) | 爬取各城市词条经济数据 |
| 选考科目要求 | 阳光高考 + 人工校对 | 部分数据经 LLM 辅助解析，标注 `need_review` |

**版权说明**：本项目不持有、不分发任何受版权保护的原始数据集。数据库文件（`college.db`）仅用于个人学习目的，使用时请遵守各平台服务条款。软科排名、学科评估等数据的版权归原发布机构所有。

---

## 技术栈

- **前端**：Streamlit
- **数据库**：SQLite
- **AI**：阿里云百炼 DashScope（兼容 OpenAI 接口）
- **数据模型**：Pydantic v2
