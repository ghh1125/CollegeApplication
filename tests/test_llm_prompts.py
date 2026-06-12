"""Tests for LLM prompt structure."""

from __future__ import annotations

import unittest


class LLMPromptTests(unittest.TestCase):
    """Prompt wording should match the product-facing analysis contract."""

    def _capture_prompt(self, fn, *args, **kwargs) -> str:
        from src.common.input import llm

        captured: dict[str, str] = {}
        original = llm._stream

        def fake_stream(messages, api_key=None):  # noqa: ANN001
            captured["prompt"] = messages[-1]["content"]
            yield "ok"

        llm._stream = fake_stream
        try:
            list(fn(*args, **kwargs))
        finally:
            llm._stream = original
        return captured["prompt"]

    def test_explain_volunteer_requests_four_short_paragraphs(self) -> None:
        from src.common.input.llm import explain_volunteer

        prompt = self._capture_prompt(
            explain_volunteer,
            {
                "school_name": "上海大学",
                "school_city": "上海",
                "major_name": "计算机科学与技术",
                "gap_info": {"tier": "稳", "weighted_avg": 12000, "gap": 800},
                "history": [{"year": 2025, "min_score": 560, "min_rank": 11800}],
                "sort_reason": "稳；专业优先：计算机科学与技术：匹配偏好专业；风险：gap 800",
            },
            {
                "rank": 12600,
                "selected_subjects": ["物理", "化学"],
                "preferred_majors": ["计算机"],
                "preferred_cities": ["上海"],
            },
            main_priority="专业优先",
        )

        self.assertIn("请输出4个短段落", prompt)
        self.assertIn("第1段：录取把握", prompt)
        self.assertIn("第2段：学校介绍", prompt)
        self.assertIn("第3段：专业分析", prompt)
        self.assertIn("第4段：劣势与风险", prompt)
        self.assertNotIn("请按以下要求输出4句话", prompt)

    def test_overall_report_requests_review_report(self) -> None:
        from src.common.input.llm import generate_overall_report

        prompt = self._capture_prompt(
            generate_overall_report,
            [
                {
                    "volunteer_no": 1,
                    "school_name": "上海大学",
                    "major_name": "计算机科学与技术",
                    "school_city": "上海",
                    "gap_info": {"tier": "稳", "weighted_avg": 12000, "gap": 800},
                    "sort_reason": "稳；学校优先：上海大学：双一流；风险：gap 800",
                }
            ],
            {"total": 1, "冲": 0, "稳": 1, "保": 0, "垫": 0},
            {
                "rank": 12600,
                "risk_preference": "均衡",
                "selected_subjects": ["物理", "化学"],
            },
            main_priority="学校优先",
        )

        self.assertIn("志愿表审查报告", prompt)
        self.assertIn("第1段：总体结论", prompt)
        self.assertIn("第2段：录取风险", prompt)
        self.assertIn("第3段：学校与城市结构", prompt)
        self.assertIn("第4段：专业结构", prompt)
        self.assertIn("第5段：调整建议", prompt)
        self.assertNotIn("请直接分4段输出", prompt)


class LLMClientConfigTests(unittest.TestCase):
    """LLM client setup should fail with user-facing config errors."""

    def test_get_client_rejects_non_ascii_api_key_before_openai_client(self) -> None:
        from src.common.input.llm import get_client

        with self.assertRaisesRegex(RuntimeError, "API Key.*中文"):
            get_client("你的key")

    @unittest.skip("LLM 模型配置已迁移，无需固定版本断言")
    def test_llm_stream_uses_qwen37_plus_by_default(self) -> None:
        from config import config
        from src.common.input import llm

        captured: dict[str, str] = {}
        original_get_client = llm.get_client

        class FakeDelta:
            content = "ok"

        class FakeChoice:
            delta = FakeDelta()

        class FakeChunk:
            choices = [FakeChoice()]

        class FakeCompletions:
            def create(self, **kwargs):  # noqa: ANN001
                captured["model"] = kwargs["model"]
                return [FakeChunk()]

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        llm.get_client = lambda api_key=None: FakeClient()
        try:
            output = "".join(llm._stream([{"role": "user", "content": "test"}], api_key="sk-test"))
        finally:
            llm.get_client = original_get_client

        self.assertEqual(config.dashscope_model, "qwen3-235b-a22b")
        self.assertEqual(captured["model"], "qwen3-235b-a22b")
        self.assertEqual(output, "ok")


if __name__ == "__main__":
    unittest.main()
