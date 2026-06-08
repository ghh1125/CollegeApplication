"""DashScope-compatible LLM explanation functions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from config import config


# Geographic region → city list, shared across all provinces.
REGION_EXPANSIONS: dict[str, list[str]] = {
    "长三角": ["上海", "杭州", "南京", "苏州", "宁波", "合肥"],
    "珠三角": ["广州", "深圳", "佛山", "东莞", "珠海"],
    "京津冀": ["北京", "天津", "石家庄", "保定"],
    "成渝":   ["成都", "重庆"],
    "中部/长江中游": ["武汉", "长沙", "南昌", "郑州"],
    "东北":   ["沈阳", "大连", "哈尔滨", "长春"],
    "西北":   ["西安", "兰州", "乌鲁木齐", "银川"],
}


# 张雪峰式分析框架：实在、就业优先、给真实数字、点明取舍和坑、不画饼。
# 注入到「解释志愿 / 家长报告 / 兴趣问卷 / 顾问对话」各 prompt，统一全系统话术风格。
ANALYSIS_FRAMEWORK = (
    "【分析风格（务必遵守）】\n"
    "- 就业倒推法：先说这个专业/学校**中等毕业生（20-50% 普通水平、非顶尖）5 年后的真实去向和大致月薪区间**，"
    "给具体数字，绝不说「前景不错」「就业面广」「值得关注」这类空话。\n"
    "- 中位数原则：以普通毕业生的实际情况为准，不拿少数顶尖/逆袭案例当普遍结果。\n"
    "- 家庭与试错成本：涉及取舍时，点明不同家庭/分数段该怎么选——普通家庭优先就业和稳妥，"
    "有底气再冲兴趣/名校；说清这个选择一旦不合适的试错成本。\n"
    "- 专业 / 学校 / 城市谁更重要：在这条志愿上直说三者的轻重和理由，不和稀泥。\n"
    "- 冷热与坑：诚实点出专业冷热、是否要读研才好就业、是否劝退、调剂/地域风险等真实信息。\n"
    "- 表达：第一句直接给结论，再用数字和事实支撑；不绕弯子、不铺垫一堆才说重点。"
)


@dataclass
class ProvinceConfig:
    """Province-specific settings injected into LLM prompts and allocation logic.

    Each province creates one instance in src/<province>/config.py.
    """

    # One-line description of the volunteer system, e.g.:
    #   浙江: "平行志愿，最多可填 80 个专业（含学校）"
    #   江苏: "平行志愿，本科普通批最多填 40 个院校专业组，每组 6 个专业"
    volunteer_system: str = "平行志愿，最多可填 80 个专业（含学校）"

    # Total volunteer slots and tier allocation per risk preference.
    # 浙江: total=80; 江苏: total=40 (院校专业组)
    total_volunteers: int = 80
    risk_allocation: dict[str, dict[str, int]] = field(default_factory=lambda: {
        "激进": {"冲": 30, "稳": 30, "保": 15, "垫": 5},
        "均衡": {"冲": 20, "稳": 30, "保": 20, "垫": 10},
        "保守": {"冲": 10, "稳": 25, "保": 30, "垫": 15},
    })

    # Subject selection description shown in the prompt, e.g.:
    #   浙江: "7 选 3（物理/化学/生物/历史/地理/思想政治/技术）"
    #   江苏: "3+1+2（首选物理或历史，再选 2 门）"
    subject_system: str = "7 选 3（物理/化学/生物/历史/地理/思想政治/技术）"

    # 志愿单位名称（用于话术）：浙江"专业(含学校)"；江苏"院校专业组"
    volunteer_unit: str = "专业（含学校）"

    # 收集选科信息的话术提示（填报模式步骤里用）
    subject_collect_hint: str = "选考科目（7选3，如物理、化学、生物）"

    # AI 在填报模式末尾输出的 JSON 示例（字段随省份不同）
    json_example: str = (
        '{"rank":..., "total_score":..., "selected_subjects":[...], '
        '"preferred_majors":[...], "preferred_cities":[...], '
        '"main_priority":"专业优先/学校优先/城市优先", "risk_preference":"激进/均衡/保守"}'
    )


# Default config = Zhejiang (backwards-compatible; callers that don't pass
# a config get Zhejiang behavior unchanged)
_DEFAULT_PROVINCE_CONFIG = ProvinceConfig()


def get_client(api_key: str | None = None) -> Any:
    key = (api_key or config.require_dashscope_api_key()).strip()
    if not key:
        raise RuntimeError("百炼 API Key 不能为空。")
    try:
        key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            "百炼 API Key 只能包含英文、数字和英文符号，不能包含中文或中文说明；"
            "请检查左侧输入框或 .env 里的 DASHSCOPE_API_KEY。"
        ) from exc
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for LLM calls.") from exc
    return OpenAI(api_key=key, base_url=config.dashscope_base_url)


MODEL = "qwen3.7-max"

_EDIT_VERBS = re.compile(r"改|换|删|调整|更新|去掉|加上|修改")
_PARAM_NOUNS = re.compile(r"位次|城市|专业|排序|优先|选科|分数|志愿数|风险|学校|省份|层次")


def should_search(message: str) -> bool:
    """Advisor mode: search unless the message is clearly a parameter edit."""
    return not (_EDIT_VERBS.search(message) and _PARAM_NOUNS.search(message))


_CONV_WORDS = re.compile(
    # longer alternatives must come first to avoid partial matches
    r"怎么样|好不好|值不值|能不能|帮我|告诉我|的话|"
    r"你|我|请|能|帮|是否|怎么|可以|"
    r"推荐|评价|分析|介绍|说说|看看|觉得|认为|了解|判断|"
    r"一下|如何|好吗|吗|呢|啊|哦|嗯"
)


def _build_search_query(message: str) -> str:
    """Strip conversational words and append context for a clean DDG query."""
    q = _CONV_WORDS.sub("", message.strip()).strip()
    if not q:
        q = message.strip()
    if not re.search(r"20\d{2}", q):
        q = f"{q} 2025"
    if not any(kw in q for kw in ("就业", "评价", "排名", "薪资")):
        q = f"{q} 评价 就业"
    return q


def search_web(query: str, max_results: int = 5) -> list[str]:
    try:
        from ddgs import DDGS
        search_query = _build_search_query(query)
        with DDGS(timeout=8) as ddgs:
            results = list(ddgs.text(search_query, max_results=max_results, region="cn-zh"))
        return [
            f"{r['title']}: {r['body'][:250]}"
            for r in results if r.get("body")
        ]
    except Exception:
        return []


def _stream(messages: list[dict], api_key: str | None = None):
    """Yield text chunks, skipping reasoning/thinking tokens."""
    client = get_client(api_key)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        extra_body={"enable_thinking": True},
        stream=True,
    )
    for chunk in resp:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


# ─── 单条志愿解释 ──────────────────────────────────────────────────────────────

_EXPLAIN_PARAGRAPHS = {
    "专业优先": """\
第1段：录取把握。直接给结论：这条是冲/稳/保/垫，引用考生位次、近三年分数线/位次、加权均值位次和gap，说明为什么放在这个层级。
第2段：学校介绍。介绍学校层次、城市、软科排名/标签/优势学科/办学特点；没有数据库信息就明确说"学校画像待补充"，不要编。
第3段：专业分析。重点讲专业优势：学什么、普通毕业生去向、薪资区间、适合什么学生；优先引用专业画像和搜索数据，无数据则写"就业数据待查"。
第4段：劣势与风险。讲清专业冷热、是否要读研、就业门槛、专业组调剂/地域/历史数据不足风险，并给一句是否建议保留在志愿表。""",
    "学校优先": """\
第1段：录取把握。直接给结论：这条是冲/稳/保/垫，引用考生位次、近三年分数线/位次、加权均值位次和gap，说明为什么放在这个层级。
第2段：学校介绍。重点讲学校优势：学校层次、城市、软科排名/标签/优势学科/办学特点，以及它是否满足用户的学校优先目标；没有数据库信息就明确说"学校画像待补充"。
第3段：专业分析。说明这个专业学什么、普通毕业生去向、薪资区间、在这所学校里是否值得读；优先引用专业画像和搜索数据，无数据则写"就业数据待查"。
第4段：劣势与风险。讲清学校层次落差、专业冷热、是否要读研、就业门槛、专业组调剂/地域/历史数据不足风险，并给一句是否建议保留在志愿表。""",
    "城市优先": """\
第1段：录取把握。直接给结论：这条是冲/稳/保/垫，引用考生位次、近三年分数线/位次、加权均值位次和gap，说明为什么放在这个层级。
第2段：学校介绍。介绍学校层次、城市、软科排名/标签/优势学科/办学特点，以及学校在该城市的就业与实习便利；没有数据库信息就明确说"学校画像待补充"。
第3段：专业分析。说明专业学什么、普通毕业生去向、薪资区间，以及这个专业在该城市产业里的吸纳能力；优先引用城市画像、专业画像和搜索数据。
第4段：劣势与风险。讲清城市就业市场局限、专业冷热、是否要读研、就业门槛、专业组调剂/地域/历史数据不足风险，并给一句是否建议保留在志愿表。""",
}

_REPORT_REVIEW_SECTIONS = {
    "专业优先": """\
第1段：总体结论。判断这张志愿表偏激进/均衡/保守，引用冲稳保垫数量，说明它是否符合专业优先和当前风险偏好。
第2段：录取风险。引用冲、稳、保、垫代表志愿的位次gap，指出哪些是高危冲、哪些是真稳、哪些负责兜底，不能只说感觉。
第3段：学校与城市结构。说明学校层次、城市分布是否服务于专业目标，有没有学校层次断档、城市过度集中或地区就业市场风险。
第4段：专业结构。说明专业方向是否集中、是否符合偏好，点出就业优势、读研压力、冷热门、专业组调剂风险。
第5段：调整建议。给3条可执行建议，例如删掉哪些高危项、补哪些保底方向、是否放宽城市/专业/民办限制。""",
    "学校优先": """\
第1段：总体结论。判断这张志愿表偏激进/均衡/保守，引用冲稳保垫数量，说明它是否符合学校优先和当前风险偏好。
第2段：录取风险。引用冲、稳、保、垫代表志愿的位次gap，指出哪些是高危冲、哪些是真稳、哪些负责兜底，不能只说感觉。
第3段：学校与城市结构。说明学校层次分布、城市分布是否符合学校优先，有没有学校层次断档、名校冲刺过多或城市过度集中。
第4段：专业结构。说明专业方向是否能接受，点出就业优势、读研压力、冷热门、专业组调剂风险，避免只看学校不看专业。
第5段：调整建议。给3条可执行建议，例如删掉哪些高危项、补哪些保底学校、是否放宽城市/专业/民办限制。""",
    "城市优先": """\
第1段：总体结论。判断这张志愿表偏激进/均衡/保守，引用冲稳保垫数量，说明它是否符合城市优先和当前风险偏好。
第2段：录取风险。引用冲、稳、保、垫代表志愿的位次gap，指出哪些是高危冲、哪些是真稳、哪些负责兜底，不能只说感觉。
第3段：学校与城市结构。说明城市分布、学校层次是否合理，有没有城市过度集中、区域就业市场单一或学校层次断档。
第4段：专业结构。说明专业方向是否适合这些城市的产业，点出就业优势、读研压力、冷热门、专业组调剂风险。
第5段：调整建议。给3条可执行建议，例如删掉哪些高危项、补哪些保底城市/学校、是否放宽城市/专业/民办限制。""",
}


def _city_quality_tier(source_name: str) -> str:
    """Return 'official' / 'wiki' / 'template' based on source_name."""
    if not source_name:
        return "template"
    if "统计局" in source_name or "统计公报" in source_name:
        return "official"
    if "维基" in source_name or "Wikipedia" in source_name:
        return "wiki"
    return "template"


def _school_profile_block(p: dict) -> str:
    """学校画像行：只输出有值的字段（简介/类型/建校/院士博硕点/校训/标签）。"""
    if not p:
        return "学校画像：暂无"
    parts: list[str] = []
    if p.get("summary"):
        parts.append(str(p["summary"]))
    meta = []
    for label, key in (("类型", "school_type"), ("性质", "school_nature"), ("层次", "education_level")):
        if p.get(key):
            meta.append(f"{label}{p[key]}")
    if p.get("founded_year"):
        meta.append(f"{p['founded_year']}年建校")
    if meta:
        parts.append("、".join(meta))
    counts = []
    if p.get("academician_count"):
        counts.append(f"{p['academician_count']}位院士")
    if p.get("doctor_count"):
        counts.append(f"{p['doctor_count']}个博士点")
    if p.get("master_count"):
        counts.append(f"{p['master_count']}个硕士点")
    if counts:
        parts.append("、".join(counts))
    if p.get("motto"):
        parts.append(f"校训「{p['motto']}」")
    if p.get("tags"):
        parts.append(f"标签：{p['tags']}")
    if p.get("source_url"):
        parts.append(f"来源：{p['source_url']}")
    return "学校画像：" + "；".join(parts)


def _city_profile_block(city_profile: dict) -> str:
    """Build the city profile line for the prompt, labelled by data quality tier."""
    source = city_profile.get("source_name") or ""
    tier = _city_quality_tier(source)
    summary = city_profile.get("summary") or "暂无"
    gdp = city_profile.get("gdp") or ""
    pop = city_profile.get("population") or ""

    if tier == "official":
        return (
            f"城市画像（官方统计）：{summary}；"
            f"GDP：{gdp}；常住人口：{pop}；"
            f"来源：{city_profile.get('source_url') or source}"
        )
    if tier == "wiki":
        gdp_str = f"GDP约{gdp}（百科参考，勿作权威依据）" if gdp else "GDP：暂无"
        pop_str = f"人口约{pop}（百科参考）" if pop else "人口：暂无"
        return (
            f"城市画像（百科参考，数据供参考，不作强依据）：{summary}；"
            f"{gdp_str}；{pop_str}"
        )
    # template
    return f"城市画像（仅城市层级）：{summary}；GDP/产业数据待补充"


def explain_volunteer(
    volunteer: dict,
    profile: dict,
    search_results: list[str] | None = None,
    main_priority: str = "学校优先",
    api_key: str | None = None,
):
    """
    Generate a plain-language explanation for a single recommended program.

    volunteer keys: school_name, major_name, school_city, gap_info,
                    history, subject_requirement_json, ruanke_rank, _warnings
    profile keys: rank, selected_subjects, preferred_majors, preferred_cities, main_priority
    """
    gap = volunteer.get("gap_info") or {}
    history = volunteer.get("history") or []
    history_bits: list[str] = []
    for h in sorted(history, key=lambda x: x["year"]):
        if not h.get("min_rank"):
            continue
        score = h.get("min_score")
        score_text = f"分数线{score}，" if score else ""
        history_bits.append(f"{h['year']}年{score_text}位次{h['min_rank']}")
    history_str = "  ".join(history_bits)
    warnings = "  ".join(volunteer.get("_warnings") or [])
    school_profile = volunteer.get("school_profile") or {}
    major_profile = volunteer.get("major_profile") or {}
    city_profile = volunteer.get("city_profile") or {}
    sort_reason = volunteer.get("sort_reason") or ""

    search_block = ""
    if search_results:
        search_block = "\n【就业参考数据（优先引用，不编造）】\n" + "\n".join(f"- {r}" for r in search_results) + "\n"

    prompt = f"""你是高考志愿规划助手，请用简洁的中文解释为什么推荐以下志愿。

【考生信息】
- 全省位次：{profile.get("rank")}
- 选考科目：{"、".join(profile.get("selected_subjects", []))}
- 偏好专业：{"、".join(profile.get("preferred_majors", [])) or "未指定"}
- 偏好城市：{"、".join(profile.get("preferred_cities", [])) or "未指定"}
- 主排序：{main_priority}

【推荐理由（系统按{main_priority}自动生成，务必引用此内容解释推荐原因）】
{sort_reason or "暂无"}

【志愿信息】
- 学校：{volunteer.get("school_name")}（{volunteer.get("school_city")}）
- 专业：{volunteer.get("major_name")}
- 层级：{gap.get("tier", "未知")}
- 历史位次：{history_str or "暂无"}
- 加权均值位次：{gap.get("weighted_avg")}，与考生位次差：{gap.get("gap")}
- 软科排名：{volunteer.get("ruanke_rank") or "未上榜"}
- 预警：{warnings or "无"}

【本地结构化画像（来自数据库，优先引用，不编造）】
- {_school_profile_block(school_profile)}
- 专业画像：{major_profile.get("summary") or "暂无"}；学什么：{major_profile.get("learn_what") or "暂无"}；就业/去向：{major_profile.get("career_direction") or "暂无"}；fallback：{major_profile.get("fallback_from") or "无"}；来源：{major_profile.get("source_url") or "暂无"}
- {_city_profile_block(city_profile)}
{search_block}
{ANALYSIS_FRAMEWORK}

【禁止输出的词】：前景不错、就业面广、高度契合、相对稳定、值得关注、综合来看

请输出4个短段落，每段2-3句话，不加标题、不加序号，不要使用表格：
{_EXPLAIN_PARAGRAPHS.get(main_priority, _EXPLAIN_PARAGRAPHS["学校优先"])}"""

    return _stream([{"role": "user", "content": prompt}], api_key=api_key)


# ─── 对话式填报 ────────────────────────────────────────────────────────────────

def _build_advisor_system(
    profile_ctx: dict | None = None,
    recommendation_ctx: dict | None = None,
    search_results: list[str] | None = None,
    province_config: ProvinceConfig | None = None,
) -> str:
    province_config = province_config or _DEFAULT_PROVINCE_CONFIG
    if recommendation_ctx:
        name, role = "小明", "志愿顾问"
        task = "解读推荐志愿，分析冲稳保策略，给出调整建议，回答专业/学校问题"
    else:
        name, role = "小明", "填报助手"
        task = "通过对话收集考生信息，在回复末尾输出JSON供表单自动填写"

    lines = [
        f"你叫{name}，角色是{role}，当前核心任务：{task}。",
        "整个对话中只有你一个AI，始终以这个角色回复。",
        f"【志愿制度】本省实行{province_config.volunteer_system}，按位次从高到低依次检索，未被录取即落档。",
        f"【选科制度】本省实行{province_config.subject_system}。",
        "",
    ]

    if profile_ctx and profile_ctx.get("rank"):
        lines += [
            "【当前考生信息】",
            f"- 全省位次：{profile_ctx.get('rank')}",
            f"- 选考科目：{'、'.join(profile_ctx.get('selected_subjects') or [])}",
            f"- 偏好专业：{'、'.join(profile_ctx.get('preferred_majors') or []) or '未指定'}",
            f"- 偏好城市：{'、'.join(profile_ctx.get('preferred_cities') or []) or '未指定'}",
            f"- 主排序：{profile_ctx.get('main_priority', '未设置')}",
            f"- 风险偏好：{profile_ctx.get('risk_preference', '未设置')}",
            "",
        ]
    else:
        lines += [
            "【当前状态】参数未填，引导用户提供：位次、选考科目（必填），偏好专业/城市/风险偏好（选填）",
            "",
        ]

    if recommendation_ctx:
        vols = recommendation_ctx.get("volunteers") or []
        stats = recommendation_ctx.get("stats") or {}
        lines += [
            "【当前推荐志愿表】",
            f"总计{stats.get('total', 0)}条  "
            f"冲{stats.get('冲', 0)} 稳{stats.get('稳', 0)} 保{stats.get('保', 0)} 垫{stats.get('垫', 0)}",
            "序 层级 学校·专业 均值位次 gap 城市",
        ]
        for v in vols:
            gi = v.get("gap_info") or {}
            lines.append(
                f"{v.get('volunteer_no')}.[{gi.get('tier', '')}] "
                f"{v.get('school_name')}·{v.get('major_name')} "
                f"均值:{gi.get('weighted_avg', '—')} gap:{gi.get('gap', '—')} "
                f"{v.get('school_city', '')}"
            )
        lines.append("")

    if recommendation_ctx:
        _priority = (profile_ctx or {}).get("main_priority", "学校优先")
        _priority_focus = {
            "专业优先": (
                "- 优先级视角：用户以专业为第一考量——解析志愿时优先说学科评估等级和专业就业质量，"
                "学校排名作为次要参考；建议调整时先问「这个专业方向是否符合你的兴趣」"
            ),
            "学校优先": (
                "- 优先级视角：用户以学校品牌/层次为第一考量——解析志愿时优先说软科排名、学校整体就业质量，"
                "专业作为次要参考；建议调整时先问「这个学校的层次/名气是否达到预期」"
            ),
            "城市优先": (
                "- 优先级视角：用户以城市地区为第一考量——解析志愿时优先说城市经济体量、产业结构和就业市场，"
                "学校和专业作为次要参考；建议调整时先问「这个城市是否符合你的地区预期」"
            ),
        }.get(_priority, "")
        lines += [ANALYSIS_FRAMEWORK]
        if _priority_focus:
            lines.append(_priority_focus)
        lines.append("")

    if search_results:
        lines += ["【实时搜索数据（优先引用，不编造数字）】"]
        lines += [f"- {r}" for r in search_results]
        lines += [""]

    lines += [
        "【规则】",
        "- 填报模式（无推荐表）收集参数流程，严格按顺序执行：",
        f"  步骤0：确认{province_config.subject_collect_hint}（必填）。",
        "  步骤1：确认主排序。用户未说明时问：「你更看重专业方向、学校排名/层次，还是地理位置？」",
        "  步骤2（BLOCKER）：",
        "    ★ 若 main_priority=专业优先 且 用户还没提过任何偏好专业 → 禁止输出JSON。",
        "      必须先问：「你有目标专业方向吗？比如计算机、金融、机械——不填专业的话，",
        "      专业优先和学校优先效果一样。没想好可以告诉我兴趣，我帮你判断。」",
        "      只有用户给出专业方向，或明确说「不知道/随便」（触发问卷引导）后，才能进入步骤3。",
        "  步骤3：所有必要信息收集完毕后，在末尾输出JSON（顾问模式不输出JSON）：",
        '  ```json',
        f'  {province_config.json_example}',
        "  preferred_cities 填具体城市名，不填地区概念。常见地区转换：",
        *[f"    {region} → [{chr(44).join(cities)}]"
          for region, cities in REGION_EXPANSIONS.items()],
        '  ```',
        "- 填报模式输出JSON后，在下一段提醒用户做两件事：",
        "  ① 核对上方提取的参数是否正确（位次、选科、主排序等）；有误请直接说要改什么",
        "  ② 确认后点击下方「确认填入表单」按钮，左侧表单还有筛选条件可以按需调整（排除省份、学校层次、是否接受民办等）",
        "- 【问卷引导模式】填报模式下，若用户提供了位次和选科但明确表示不知道读什么",
        "  （如「不知道」「没想好」「你帮我决定」「随便」），先不要催主排序，而是进入对话式问卷：",
        "  每次只问一个问题，等用户回答后再问下一个，共4问：",
        "  Q1：「理科课（数学/物理/化学）和文科课（语文/历史/政治），哪边让你更有成就感，或者相对更喜欢？」",
        "  Q2：「毕业后更倾向哪种日常工作？",
        "       A 坐电脑前做技术/数据/设计  B 跟人打交道（管理/销售/咨询/教学）  C 动手操作（实验室/工地/医院/户外）」",
        "  Q3：「选专业你最看重什么？",
        "       A 好找工作、薪资高  B 专业本身有意思，哪怕收入一般  C 稳定、能考公考编」",
        "  Q4：「有没有绝对不想碰的方向？比如医学（学制长）、师范（要上课）、法学（要背）、艺术（需功底）等」",
        "  收到4个回答后：",
        "  1. 给出2-3个推荐专业方向 + 每个方向一句理由（基于回答逻辑推导，不要说「根据你的回答」）",
        "  2. 主排序默认「专业优先」，把推荐方向填入preferred_majors，输出完整JSON",
        "  3. 按填报模式正常提示用户核对、确认",
        "- 顾问模式不做上述提醒，专注于志愿分析",
        "- 顾问模式下，每条消息先判断用户意图，再决定怎么回复：",
        "  ① 【明确修改】用户直接说要换参数，如「换成学校优先」「我想留在江浙沪」「改保守一点」「加上金融专业」",
        "    → 基于【当前考生信息】输出更新后的完整JSON（格式同上），JSON后提示点击「确认填入表单」重新生成",
        "  ② 【咨询提问】用户在问问题或评价志愿，如「软件工程就业怎么样」「这条志愿值得冲吗」「杭州和南京哪个好」",
        "    → 直接回答，不输出JSON",
        "  ③ 【意图模糊】用户随口提到某方向但没明确说要改，如「其实我也可以考虑金融」「我不太想去太远的地方」",
        "    → 先简短回答，最后问一句：「需要我把XX加入偏好/调整参数重新生成吗？」等用户确认再输出JSON",
        "- 回答专业就业、学校排名等问题基于你的知识直接回答",
        "- 系统只支持以下可调参数：位次、总分、选考科目、偏好专业、偏好城市、主排序（专业/学校/城市优先）、风险偏好（激进/均衡/保守）。"
        "  不要建议用户修改系统不存在的参数（如志愿数量上限、某城市配额、学校白名单数量等），这些功能不存在，建议了也无法执行。",
        "- 回复控制在200字以内（列表/JSON不计入）",
    ]

    return "\n".join(lines)


def chat_with_advisor(
    messages: list[dict],
    profile_ctx: dict | None = None,
    recommendation_ctx: dict | None = None,
    search_results: list[str] | None = None,
    api_key: str | None = None,
    province_config: ProvinceConfig | None = None,
):
    system = _build_advisor_system(profile_ctx, recommendation_ctx, search_results, province_config)
    return _stream([{"role": "system", "content": system}] + messages, api_key=api_key)


# ─── 总体报告 ──────────────────────────────────────────────────────────────────

def generate_overall_report(
    volunteers: list[dict],
    stats: dict,
    profile: dict,
    search_results: list[str] | None = None,
    main_priority: str = "学校优先",
    api_key: str | None = None,
):
    """Generate an overall analysis report for the entire volunteer list."""
    # Summarize tier distribution
    tier_detail: dict[str, list[str]] = {}
    for v in volunteers:
        tier = (v.get("gap_info") or {}).get("tier", "未知")
        tier_detail.setdefault(tier, []).append(
            f"{v.get('school_name')}·{v.get('major_name')}"
        )

    # Representative sort reasons per tier — include all tiers so parents see 垫/数据不足 risks
    TIER_SAMPLES = [("冲", 3), ("稳", 2), ("保", 2), ("垫", 1), ("数据不足", 2), ("高危冲", 1)]
    top_reasons_lines = []
    for tier, n in TIER_SAMPLES:
        tier_vols = [v for v in volunteers if (v.get("gap_info") or {}).get("tier") == tier]
        if not tier_vols:
            continue
        for v in tier_vols[:n]:
            reason = (v.get("sort_reason") or "").split("；", 1)[-1]  # strip tier prefix
            top_reasons_lines.append(
                f"[{tier}]{v.get('volunteer_no')}.{v.get('school_name')}·{v.get('major_name')}：{reason[:120]}"
            )
    top_reasons_block = "\n".join(top_reasons_lines) if top_reasons_lines else "暂无"

    # Collect risk flags
    warning_count = sum(1 for v in volunteers if v.get("_warnings"))
    no_history_count = sum(
        1 for v in volunteers
        if (v.get("gap_info") or {}).get("data_years", 0) == 0
    )
    cities = [v.get("school_city") for v in volunteers if v.get("school_city")]
    from collections import Counter
    top_cities = Counter(cities).most_common(5)

    # Top schools
    schools = [v.get("school_name") for v in volunteers]
    top_schools = Counter(schools).most_common(5)

    tier_summary = "\n".join(
        f"  {tier}（{len(names)}个）：{', '.join(names[:3])}{'…' if len(names) > 3 else ''}"
        for tier, names in tier_detail.items()
        if names
    )

    search_block = ""
    if search_results:
        search_block = "\n【主要专业就业参考数据（优先引用，不编造）】\n" + "\n".join(f"- {r}" for r in search_results) + "\n"

    prompt = f"""你是高考志愿规划助手，给家长看这份{stats.get("total")}个志愿的志愿表审查报告。语言直接，不说废话。

【考生信息】
- 全省位次：{profile.get("rank")}  风险偏好：{profile.get("risk_preference", "均衡")}
- 选考科目：{"、".join(profile.get("selected_subjects", []))}
- 主排序：{main_priority}

【冲稳保代表志愿推荐理由（系统按{main_priority}自动生成，报告中直接引用这些具体依据）】
{top_reasons_block}

【志愿统计】
- 冲{stats.get("冲")} 稳{stats.get("稳")} 保{stats.get("保")} 垫{stats.get("垫")}
- 数据不足：{no_history_count}个  有预警：{warning_count}个

【层级明细】
{tier_summary}

【城市分布Top5】
{", ".join(f"{c}({n}个)" for c, n in top_cities)}

【学校分布Top5】
{", ".join(f"{s}({n}个)" for s, n in top_schools)}
{search_block}
{ANALYSIS_FRAMEWORK}

【禁止输出的词】：比例合理、高度聚焦、利于发展、整体来看、综合考量、值得注意

请直接分5段输出（不加标题序号），每段2-4句话，必须引用上面的具体数字和代表志愿：
{_REPORT_REVIEW_SECTIONS.get(main_priority, _REPORT_REVIEW_SECTIONS["学校优先"])}"""

    return _stream([{"role": "user", "content": prompt}], api_key=api_key)
