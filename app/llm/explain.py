"""DashScope-compatible LLM explanation functions."""

from __future__ import annotations

from openai import OpenAI

from app.config import config

MODEL = "qwen3.7-max"


def get_client(api_key: str | None = None) -> OpenAI:
    key = api_key or config.require_dashscope_api_key()
    return OpenAI(api_key=key, base_url=config.dashscope_base_url)


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
        delta = chunk.choices[0].delta
        # skip reasoning_content (thinking tokens), only yield final answer
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

    prompt = f"""你是高考志愿规划助手，请用简洁的中文（100字以内）解释为什么推荐以下志愿，并指出风险点。

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

请按以下结构输出（不要加标题）：
第一句：层级判断依据（用位次数字说话）
第二句：历史趋势是否稳定
第三句：与考生偏好的匹配程度
第四句（如有风险）：风险提示"""

    return _stream([{"role": "user", "content": prompt}], api_key=api_key)


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
