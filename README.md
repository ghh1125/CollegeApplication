# 高考志愿推荐系统

面向浙江高考的志愿填报推荐工具。输入位次和偏好，自动生成冲稳保梯度志愿方案，并提供 AI 对话助手帮你分析和解读。

**在线体验**：[https://collegeapplication-imqdnkfg5pggujsmkofkbo.streamlit.app](https://collegeapplication-imqdnkfg5pggujsmkofkbo.streamlit.app)

> **注意事项**：浙江高考实行平行志愿，最多可填 80 个专业（含学校），按位次从高到低依次检索，未被录取即落档。本工具基于历史数据生成参考方案，不能保证录取结果。历史位次每年会有波动，部分学校/专业数据可能存在缺失或误差。**最终填报请以浙江省教育考试院官方公布的数据为准，建议同时参考学校招生章程和专业录取规则。**
>
> 本工具输出的志愿方案仅供参考，不构成填报建议。是否填报某所学校或专业，取决于考生本人的意愿、家庭情况和综合判断，请结合实际情况自行决定。

---

## 能做什么

- **自动筛选**：根据你的选考科目，排除掉不符合要求的专业，再按学校层次、城市、专业方向进一步缩小范围
- **录取把握判断**：对比你的位次和该专业近三年历史录取位次，判断是冲、稳、保还是垫底，不靠感觉靠数据
- **三种排序模式**：专业优先（先找你想读的专业）、学校优先（先找好学校）、城市优先（先找你想去的城市），同一套数据，按你的侧重点排
- **专业相关度识别**：能区分"这就是你想读的专业"、"这个专业类里的"、"跟你想读的方向沾边"、"完全不相关"，不会把无关专业混进来
- **AI 对话助手**：不知道填什么可以跟小明聊，它会问你几个问题帮你分析适合读什么方向，也能解释为什么推荐某条志愿

---

## 项目结构

```
.
├── main.py                   # 网页应用主文件，运行后在浏览器打开
├── db.py                     # 数据库连接
├── config.py                 # 配置（API Key 等）
│
├── src/
│   ├── input/                # 接收用户输入，做初步筛选
│   │   ├── profile.py        # 学生信息格式定义（位次、选科、偏好等）
│   │   ├── filter.py         # 筛掉不符合条件的学校专业
│   │   ├── llm.py            # AI 对话逻辑
│   │   └── ingest.py         # 原始数据导入数据库
│   │
│   ├── ranking/              # 给候选学校专业打分排序
│   │   ├── rank.py           # 位次计算、专业匹配打分、排序
│   │   └── profiles.py       # 补充学校和专业的详细信息
│   │
│   ├── allocation/           # 生成最终志愿表
│   │   ├── builder.py        # 按冲稳保比例分配志愿数量
│   │   └── recommend.py      # 把所有步骤串起来，输出完整结果
│   │
│   └── export/
│       └── excel.py          # 导出 Excel 表格
│
├── ui/
│   └── form_helpers.py       # 网页表单辅助
│
├── data/
│   ├── college.db            # 数据库文件（包含所有学校、专业、历史录取数据）
│   ├── schema.sql            # 数据库表结构定义
│   └── raw/                  # 原始数据文件
│       ├── historical_cutoff_2023/2024/2025.csv   # 各年历史录取位次
│       ├── subject_requirement.pdf                # 选考科目要求原始PDF
│       ├── subject_req_parsed.csv                 # PDF 解析后的结构化数据
│       ├── school_profiles_raw.json               # 学校基本信息
│       ├── school_locations_raw.json              # 学校城市信息
│       └── city_wiki_raw.json                     # 城市经济数据
│
├── scripts/                  # 数据处理脚本（初始化数据库、抓取数据等，一次性使用）
└── tests/                    # 自动化测试
```

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

AI 对话功能（小明助手、报告生成）需要阿里云百炼的 API Key，志愿生成本身不需要。

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
| 学校基本信息 / 专业介绍 | [阳光高考 static-data.gaokao.cn](https://static-data.gaokao.cn) |
| 浙江省位次分段表 / 选考科目要求 | [浙江省教育考试院](https://www.zjzs.net) |
| 大学排名 | [软科 shanghairanking.cn](https://www.shanghairanking.cn)（手动整理） |
| 学科评估等级 | [教育部第四轮学科评估](https://www.moe.gov.cn)（2017年公开发布） |
| 城市经济数据 | [维基百科](https://zh.wikipedia.org) |

**版权说明**：本项目不持有、不分发任何受版权保护的原始数据集，仅供个人学习使用，请遵守各平台服务条款。

---

## 技术栈

- **网页框架**：Streamlit
- **数据库**：SQLite
- **AI**：阿里云百炼 DashScope
