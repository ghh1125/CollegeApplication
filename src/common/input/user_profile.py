"""模块①：用户画像问卷（省份无关，可复用）。

形式：一题一题选 ABCD，全部答完后由 LLM 分析、推荐专业方向。
  - 只帮还没想好的考生**找专业方向**，给建议参考。
  - 不收集位次/选科，不自动填表；用户看完建议仍需自己到「填志愿信息」手填。
"""

from __future__ import annotations

from src.common.input.llm import _stream

# 问卷题库：每题一个 question + 4 个选项。可按需增删。
QUESTIONS: list[dict] = [
    {
        "key": "interest",
        "question": "你平时对哪一类东西最感兴趣？",
        "options": {
            "A": "电脑、数码、游戏、人工智能这类技术",
            "B": "商业、金融、管理、怎么赚钱",
            "C": "文学、语言、历史、新闻、写作",
            "D": "医学、生物、自然、动物、环境",
        },
    },
    {
        "key": "subject",
        "question": "高中哪一类课你学得相对带劲、成绩也还行？",
        "options": {
            "A": "数学、物理（爱推理、爱解题）",
            "B": "化学、生物（爱实验、记得住）",
            "C": "语文、英语、政史地（文科类）",
            "D": "都一般，但动手做东西很拿手",
        },
    },
    {
        "key": "workstyle",
        "question": "毕业以后，你更想要哪种日常工作状态？",
        "options": {
            "A": "坐电脑前做技术、数据、设计",
            "B": "跟人打交道（管理、销售、咨询、教学）",
            "C": "动手操作（实验室、工地、医院、户外）",
            "D": "搞创意表达（设计、写作、影视、艺术）",
        },
    },
    {
        "key": "value",
        "question": "选专业时你最看重什么？",
        "options": {
            "A": "好就业、薪资高",
            "B": "专业本身有意思，哪怕收入一般",
            "C": "稳定，方便考公考编、进体制",
            "D": "学校名气和平台，专业其次",
        },
    },
    {
        "key": "taboo",
        "question": "有没有比较抗拒、不太想碰的方向？",
        "options": {
            "A": "不想学医（学制长、辛苦）",
            "B": "不想当老师 / 不想要太多背诵",
            "C": "不想做纯技术、天天对着代码",
            "D": "都还好，没有特别忌讳",
        },
    },
]

_SYSTEM = (
    "你叫小明，是高考选专业的顾问。下面是一位还没想好读什么的考生填写的兴趣问卷，"
    "请基于这些回答，推荐 2-3 个适合的专业方向。\n"
    "要求：\n"
    "- 每个方向：一句话说「为什么适合 ta」+ 一句「中等毕业生的真实去向和大致薪资」。\n"
    "- 就业用大白话、给真实区间，不说「前景好」这类空话。\n"
    "- 诚实提醒：这只是参考方向，冷热和是否适合还要本人权衡。\n"
    "- 不要问位次/选科（那是下一步填志愿做的事），不要输出 JSON 或代码块。\n"
    "- 控制在 250 字以内。"
)


def analyze_questionnaire(answers: list[dict], api_key: str | None = None):
    """答卷 → 流式返回专业方向建议。

    answers: [{"question": str, "choice": "A", "answer": str}, ...]
    """
    lines = ["考生兴趣问卷回答："]
    for i, a in enumerate(answers, 1):
        lines.append(f"{i}. {a['question']} → 选了：{a['answer']}")
    user_msg = "\n".join(lines) + "\n\n请给出专业方向推荐。"
    return _stream(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user_msg}],
        api_key=api_key,
    )
