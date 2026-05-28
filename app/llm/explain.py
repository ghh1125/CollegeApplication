"""DashScope-compatible LLM explanation functions."""

from __future__ import annotations

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
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(f"{query} 2025", max_results=max_results))
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

def explain_volunteer(volunteer: dict, profile: dict, api_key: str | None = None):
    """
    Generate a plain-language explanation for a single recommended program.

    volunteer keys: school_name, major_name, school_city, gap_info,
                    history, subject_requirement_json, ruanke_rank, _warnings
    profile keys: rank, selected_subjects, preferred_majors, preferred_cities
    """
    gap = volunteer.get("gap_info") or {}
    history = volunteer.get("history") or []
    history_str = "  ".join(
        f"{h['year']}年位次{h['min_rank']}"
        for h in sorted(history, key=lambda x: x["year"])
        if h.get("min_rank")
    )
    warnings = "  ".join(volunteer.get("_warnings") or [])

    prompt = f"""你是高考志愿规划助手，请用简洁的中文解释为什么推荐以下志愿。

【考生信息】
- 全省位次：{profile.get("rank")}
- 选考科目：{"、".join(profile.get("selected_subjects", []))}
- 偏好专业：{"、".join(profile.get("preferred_majors", [])) or "未指定"}
- 偏好城市：{"、".join(profile.get("preferred_cities", [])) or "未指定"}

【志愿信息】
- 学校：{volunteer.get("school_name")}（{volunteer.get("school_city")}）
- 专业：{volunteer.get("major_name")}
- 层级：{gap.get("tier", "未知")}
- 历史位次：{history_str or "暂无"}
- 加权均值位次：{gap.get("weighted_avg")}，与考生位次差：{gap.get("gap")}
- 软科排名：{volunteer.get("ruanke_rank") or "未上榜"}
- 预警：{warnings or "无"}

请按以下结构输出4句话（不加标题、不加序号）：
第1句：录取概率判断——用位次数字直接说（如"均值位次X，考生位次Y，gap Z，属于冲/稳/保"）
第2句：就业倒推——该专业中等毕业生（非顶尖）典型去向和薪资区间是什么，不说"前景不错"等模糊话
第3句：历史趋势——近年位次是涨是跌还是稳定，数据充足还是不足
第4句：风险或匹配点——如有预警/数据不足/与偏好不符则点出，否则说与考生偏好的匹配之处"""

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
        lines += [
            "【分析框架】",
            "- 就业倒推法：解释志愿/专业时先给中等毕业生（非顶尖）的典型去向和薪资区间，不说「前景不错」等模糊话",
            "- 中位数原则：看20-50%普通毕业生5年后的实际情况，不引用极端顶尖案例",
            "- 家庭背景分流：用户问开放性问题时先反问家庭背景和试错成本，再给针对性建议",
            "- 表达规则：第一句直接给结论，引用具体数字，不先铺垫四段再给判断",
            "",
        ]

    if search_results:
        lines += ["【实时搜索数据（优先引用，不编造数字）】"]
        lines += [f"- {r}" for r in search_results]
        lines += [""]

    lines += [
        "【规则】",
        "- 填报模式（无推荐表）：收集完参数后在末尾输出JSON（顾问模式不输出JSON）：",
        '  ```json',
        '  {"rank":..., "total_score":..., "selected_subjects":[...], "preferred_majors":[...], "preferred_cities":[...], "main_priority":"...", "risk_preference":"..."}',
        '  ```',
        "- 填报模式输出JSON后，在下一段提醒用户做三件事：",
        "  ① 核对上方提取的参数是否正确（位次、选科等）",
        "  ② 左侧表单还有一些可选项可以按需设置：",
        "     - 主排序（专业优先 or 学校优先）",
        "     - 偏好城市 / 排除省份",
        "     - 学校层次过滤（985/211）",
        "     - 风险偏好（激进/均衡/保守）",
        "     - 是否接受民办/中外合作",
        "  ③ 确认无误后点击对话框下方的「确认填入表单」按钮",
        "- 顾问模式不做上述提醒，专注于志愿分析",
        "- 建议调整参数时，明确说明在左侧表单哪里修改",
        "- 回答专业就业、学校排名等问题基于你的知识直接回答",
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
    api_key: str | None = None,
):
    """
    Generate an overall analysis report for the entire 80-volunteer list.
    """
    # Summarize tier distribution
    tier_detail: dict[str, list[str]] = {}
    for v in volunteers:
        tier = (v.get("gap_info") or {}).get("tier", "未知")
        tier_detail.setdefault(tier, []).append(
            f"{v.get('school_name')}·{v.get('major_name')}"
        )

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

    prompt = f"""你是高考志愿规划助手，请用300字以内对以下志愿表做总体分析，语言简洁，给家长看。

【考生信息】
- 全省位次：{profile.get("rank")}
- 选考科目：{"、".join(profile.get("selected_subjects", []))}
- 偏好专业：{"、".join(profile.get("preferred_majors", [])) or "未指定"}
- 偏好城市：{"、".join(profile.get("preferred_cities", [])) or "未指定"}
- 风险偏好：{profile.get("risk_preference", "均衡")}

【志愿统计】
- 总计：{stats.get("total")}个志愿
- 冲：{stats.get("冲")}  稳：{stats.get("稳")}  保：{stats.get("保")}  垫：{stats.get("垫")}
- 数据不足：{no_history_count}个  需核对：{warning_count}个

【层级明细】
{tier_summary}

【城市分布Top5】
{", ".join(f"{c}({n}个)" for c, n in top_cities)}

【学校分布Top5】
{", ".join(f"{s}({n}个)" for s, n in top_schools)}

请输出：
1. 整体评价（冲稳保比例是否合理，一句话结论）
2. 亮点（最匹配考生偏好的部分）
3. 风险提示（需要家长特别注意的地方）
4. 建议（1-2条行动建议）"""

    return _stream([{"role": "user", "content": prompt}], api_key=api_key)
