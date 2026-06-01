"""DashScope-compatible LLM explanation functions."""

from __future__ import annotations

import re
from typing import Any

from app.config import config


def get_client(api_key: str | None = None) -> Any:
    key = api_key or config.require_dashscope_api_key()
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for LLM calls.") from exc
    return OpenAI(api_key=key, base_url=config.dashscope_base_url)


MODEL = "qwen3.7-max"

SEARCH_KEYWORDS = ["就业", "薪资", "工资", "前景", "行情", "替代", "招聘", "行业", "待遇", "毕业去向"]


def should_search(message: str) -> bool:
    return any(kw in message for kw in SEARCH_KEYWORDS)


def search_web(query: str, max_results: int = 3) -> list[str]:
    try:
        from ddgs import DDGS
        search_query = query if re.search(r"\b20\d{2}\b", query) else f"{query} 2025"
        with DDGS(timeout=8) as ddgs:
            results = list(ddgs.text(search_query, max_results=max_results))
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

_EXPLAIN_4_SENTENCES = {
    "专业优先": """\
第1句：专业切入——先点出这个专业的学科评估等级（若有），再说录取概率（均值位次X，考生位次Y，gap Z，属于冲/稳/保），一句话说清楚
第2句：专业深度——这个专业普通毕业生（非顶尖）典型去向和薪资区间；优先引用上方搜索数据；无数据则写"就业数据待查"
第3句：历史趋势——直接引用历史位次数字说该专业录取线涨跌，若只有1年数据则说参考价值有限
第4句：专业匹配——引用上方推荐理由里的专业相关内容，说明该专业与考生偏好的契合情况；有预警则点出""",
    "学校优先": """\
第1句：学校切入——先点出软科排名（若有）和学校层次，再说录取概率（均值位次X，考生位次Y，gap Z，属于冲/稳/保），一句话说清楚
第2句：学校深度——该校毕业生整体就业质量、平均薪资或典型雇主；优先引用上方搜索数据；无数据则写"就业数据待查"
第3句：历史趋势——直接引用历史位次数字说该校该专业录取线涨跌，若只有1年数据则说参考价值有限
第4句：学校匹配——引用上方推荐理由里的学校相关内容，说明该学校层次是否达到考生预期；有预警则点出""",
    "城市优先": """\
第1句：城市切入——先点出城市经济层级（城市画像里有GDP则引用，没有则只说层级/省会等信息，不编造数字），再说录取概率（均值位次X，考生位次Y，gap Z，属于冲/稳/保），一句话说清楚
第2句：城市深度——城市产业结构 + 这个专业在该城市的就业吸纳情况和薪资水平；优先引用上方搜索数据；城市画像无数据则写"城市就业数据待查"；无搜索数据则写"就业数据待查"
第3句：历史趋势——直接引用历史位次数字说录取线涨跌，若只有1年数据则说参考价值有限
第4句：城市匹配——引用上方推荐理由里的城市相关内容，说明该城市是否符合考生地区预期；有预警则点出""",
}

_REPORT_PARA2 = {
    "专业优先": (
        '第2段：专业深度——列出出现最多的2-3个专业方向，说每个方向普通毕业生（非顶尖）'
        '的典型去向和薪资区间；优先引用搜索数据，无数据则写"就业数据待查"'
    ),
    "学校优先": (
        '第2段：学校深度——分析Top学校的软科排名分布，说这些院校毕业生整体就业质量和薪资水平；'
        '优先引用搜索数据，无数据则写"就业数据待查"'
    ),
    "城市优先": (
        '第2段：城市深度——分析Top城市的产业结构和就业市场，说这些城市对志愿表中主要专业'
        '的吸纳能力和薪资水平；优先引用搜索数据，无数据则写"就业数据待查"'
    ),
}

_REPORT_PARA1 = {
    "专业优先": "第1段：从专业角度点出整体——志愿表覆盖了哪几个专业方向、各方向的学科评估等级分布如何，用具体数字（冲稳保垫各几条）",
    "学校优先": "第1段：从学校角度点出整体——志愿表的学校层次分布（985/211/双一流/普通本科各几条），用具体数字（冲稳保垫各几条）",
    "城市优先": "第1段：从城市角度点出整体——志愿表覆盖了哪几个城市/地区、各城市经济层级分布如何，用具体数字（冲稳保垫各几条）",
}

_REPORT_PARA3 = {
    "专业优先": '第3段：专业风险——最需要家长注意的1-2个专业选择风险，点到具体志愿（如"第X条XXX"），重点说专业就业或冷热门风险',
    "学校优先": '第3段：学校风险——最需要家长注意的1-2个学校选择风险，点到具体志愿（如"第X条XXX"），重点说学校层次落差或数据不足风险',
    "城市优先": '第3段：地区风险——最需要家长注意的1-2个地区分布风险，点到具体志愿（如"第X条XXX"），重点说城市就业市场或地区集中度风险',
}

_REPORT_PARA4 = {
    "专业优先": "第4段：1条最重要的可执行建议——专注于专业匹配度优化，说清楚在左侧哪里修改",
    "学校优先": "第4段：1条最重要的可执行建议——专注于院校层次覆盖优化，说清楚在左侧哪里修改",
    "城市优先": "第4段：1条最重要的可执行建议——专注于城市分布和地区就业市场，说清楚在左侧哪里修改",
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
    history_str = "  ".join(
        f"{h['year']}年位次{h['min_rank']}"
        for h in sorted(history, key=lambda x: x["year"])
        if h.get("min_rank")
    )
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
- 学校画像：{school_profile.get("summary") or "暂无"}；标签：{school_profile.get("tags") or "暂无"}；来源：{school_profile.get("source_url") or "暂无"}
- 专业画像：{major_profile.get("summary") or "暂无"}；就业/去向：{major_profile.get("career_direction") or "暂无"}；fallback：{major_profile.get("fallback_from") or "无"}；来源：{major_profile.get("source_url") or "暂无"}
- {_city_profile_block(city_profile)}
{search_block}
【禁止输出的词】：前景不错、就业面广、高度契合、相对稳定、值得关注、综合来看

请按以下要求输出4句话（不加标题、不加序号）：
{_EXPLAIN_4_SENTENCES.get(main_priority, _EXPLAIN_4_SENTENCES["学校优先"])}"""

    return _stream([{"role": "user", "content": prompt}], api_key=api_key)


# ─── 对话式填报 ────────────────────────────────────────────────────────────────

def _build_advisor_system(
    profile_ctx: dict | None = None,
    recommendation_ctx: dict | None = None,
    search_results: list[str] | None = None,
) -> str:
    if recommendation_ctx:
        name, role = "小明", "志愿顾问"
        task = "解读推荐志愿，分析冲稳保策略，给出调整建议，回答专业/学校问题"
    else:
        name, role = "小明", "填报助手"
        task = "通过对话收集考生信息，在回复末尾输出JSON供表单自动填写"

    lines = [
        f"你叫{name}，角色是{role}，当前核心任务：{task}。",
        "整个对话中只有你一个AI，始终以这个角色回复。",
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
        lines += [
            "【分析框架】",
            "- 就业倒推法：解释志愿/专业时先给中等毕业生（非顶尖）的典型去向和薪资区间，不说「前景不错」等模糊话",
            "- 中位数原则：看20-50%普通毕业生5年后的实际情况，不引用极端顶尖案例",
            "- 家庭背景分流：用户问开放性问题时先反问家庭背景和试错成本，再给针对性建议",
            "- 表达规则：第一句直接给结论，引用具体数字，不先铺垫四段再给判断",
        ]
        if _priority_focus:
            lines.append(_priority_focus)
        lines.append("")

    if search_results:
        lines += ["【实时搜索数据（优先引用，不编造数字）】"]
        lines += [f"- {r}" for r in search_results]
        lines += [""]

    lines += [
        "【规则】",
        '- 填报模式（无推荐表）：必须先收集主排序再输出JSON。主排序是必填项，用户未说明时主动问一句：“',
        '  "你更看重专业方向、学校排名/层次，还是地理位置？"（对应专业优先/学校优先/城市优先）',
        "  收集完后在末尾输出JSON（顾问模式不输出JSON）：",
        '  ```json',
        '  {"rank":..., "total_score":..., "selected_subjects":[...], "preferred_majors":[...], "preferred_cities":[...], "main_priority":"专业优先/学校优先/城市优先（必填，用户说了专业倾向填专业优先，说了城市倾向填城市优先，说了学校层次填学校优先）", "risk_preference":"激进/均衡/保守（未提及填均衡）"}',
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
):
    system = _build_advisor_system(profile_ctx, recommendation_ctx, search_results)
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

    prompt = f"""你是高考志愿规划助手，给家长看这份{stats.get("total")}个志愿的分析报告。语言直接，不说废话。

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
【禁止输出的词】：比例合理、高度聚焦、利于发展、整体来看、综合考量、值得注意

请直接分4段输出（不加标题序号）：
{_REPORT_PARA1.get(main_priority, _REPORT_PARA1["学校优先"])}
{_REPORT_PARA2.get(main_priority, _REPORT_PARA2["学校优先"])}
{_REPORT_PARA3.get(main_priority, _REPORT_PARA3["学校优先"])}
{_REPORT_PARA4.get(main_priority, _REPORT_PARA4["学校优先"])}"""

    return _stream([{"role": "user", "content": prompt}], api_key=api_key)
