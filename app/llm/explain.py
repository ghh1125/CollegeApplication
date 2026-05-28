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


# ─── 对话式填报 ────────────────────────────────────────────────────────────────

_CHAT_SYSTEM = """\
你是高考志愿填报助手小智。通过多轮对话收集考生信息，最终输出结构化参数。

需要收集的信息：
- rank: 全省位次（整数，必填）
- total_score: 总分（整数，可选）
- selected_subjects: 选考科目，从[物理,化学,生物,历史,地理,思想政治,技术]选3个（必填）
- preferred_majors: 偏好专业关键词列表（可为空列表）
- preferred_cities: 偏好城市列表（可为空列表）
- main_priority: "专业优先" 或 "学校优先"（必填）
- risk_preference: "激进"、"均衡" 或 "保守"（必填）

对话规则：
1. 第一条消息是系统触发的"开始"，你要做自我介绍并引导用户，不要把这条当作用户消息
2. 先收集：位次、选考科目（最重要）
3. 再收集：偏好专业、城市（可跳过）
4. 最后确认：风险偏好（默认均衡）
5. 当必填信息齐全后，在回复末尾输出JSON代码块：
```json
{"rank": 36500, "total_score": 626, "selected_subjects": ["物理","化学","生物"], "preferred_majors": ["计算机"], "preferred_cities": ["北京"], "main_priority": "专业优先", "risk_preference": "均衡"}
```
6. 输出JSON后，请告诉用户"请确认上方参数是否正确，正确的话点击下方'确认填入表单'按钮即可"
7. 用户说不对时，修正后重新输出完整JSON

每次回复不超过120字（JSON不计入），语言亲切简洁\
"""


def chat_extract_profile(messages: list[dict], api_key: str | None = None):
    """
    Multi-turn conversation to extract a structured student profile.
    Yields text chunks; final assistant turn ends with a ```json ... ``` block.
    messages: list of {role, content} dicts (no system message — added here).
    """
    full_messages = [{"role": "system", "content": _CHAT_SYSTEM}] + messages
    return _stream(full_messages, api_key=api_key)


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
