"""离线契约测试：不联网、不需要密钥。

守的是本产品的三条不变量：
  1. 问卷→肤质 是确定性的（可复现，不靠模型）
  2. 合规审核必须命中医疗红线/绝对化用语
  3. 内容与视觉的兜底**永远不抛异常、永远有输出**（demo 不白屏）

运行：cd products && python -m unittest beauty_mirror.tests.test_offline_contract -v
"""

from __future__ import annotations

import json
import unittest

from ..domain import skin_type as st
from ..tools import (
    classify_skin_type,
    compliance_check,
    detect_visible_issues,
    fetch_creator_cards,
    get_questionnaire,
    get_tutorial_breakdown,
)


class TestSkinTypeRules(unittest.TestCase):
    def test_deterministic(self):
        answers = {"tight": "yes", "redness": "no", "oil": "no"}
        first = json.loads(classify_skin_type(**answers))
        second = json.loads(classify_skin_type(**answers))
        self.assertEqual(first, second)

    def test_dry(self):
        result = st.classify({"tight": "yes", "redness": "no", "oil": "no"})
        self.assertEqual(result["skin_type"], "干性")
        self.assertFalse(result["sensitive"])

    def test_oily(self):
        result = st.classify({"tight": "no", "redness": "no", "oil": "yes"})
        self.assertEqual(result["skin_type"], "油性")

    def test_sensitive_overlay(self):
        result = st.classify({"tight": "no", "redness": "often", "oil": "no"})
        self.assertTrue(result["sensitive"])
        self.assertIn("敏感", result["labels"])

    def test_partial_answers_tolerated(self):
        result = st.classify({"tight": "yes"})
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["answered"], 1)

    def test_questionnaire_has_three_questions(self):
        payload = json.loads(get_questionnaire())
        self.assertEqual(len(payload["questions"]), 3)


class TestComplianceRules(unittest.TestCase):
    def test_medical_redline_blocks(self):
        result = json.loads(compliance_check("建议用这个方案根治痘痘并治疗皮炎"))
        self.assertEqual(result["status"], "rework")
        categories = {hit["category"] for hit in result["hits"]}
        self.assertIn("医疗红线", categories)

    def test_absolute_claim_blocks(self):
        result = json.loads(compliance_check("这款精华见效最快，百分百有效"))
        self.assertEqual(result["status"], "rework")

    def test_clean_text_passes(self):
        result = json.loads(compliance_check("早晚温和清洁，注意保湿，效果因人而异。"))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["hits"], [])


class TestFallbackNeverBreaks(unittest.TestCase):
    def test_content_returns_cards_for_unknown_style(self):
        """风格标签写错也必须给内容，不能空手而归。"""
        payload = json.loads(fetch_creator_cards("这个标签根本不存在"))
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(len(payload["cards"]), 1)

    def test_content_cards_have_jump_link(self):
        payload = json.loads(fetch_creator_cards("韩系水光"))
        for card in payload["cards"]:
            self.assertTrue(card["original_url"])
            self.assertTrue(card["creator"])

    def test_tutorial_breakdown_after_fetch(self):
        cards = json.loads(fetch_creator_cards("日系元气"))
        card_id = cards["cards"][0]["id"]
        detail = json.loads(get_tutorial_breakdown(card_id))
        self.assertTrue(detail["ok"])
        self.assertIn("tutorial", detail)

    def test_vision_falls_back_when_provider_unavailable(self):
        """真实 provider 缺密钥时，必须用 mock 垫底并给出结果。"""
        payload = json.loads(
            detect_visible_issues("/tmp/不存在的照片.jpg", provider="qwen_vl")
        )
        self.assertTrue(payload["fallback_used"])
        self.assertGreaterEqual(len(payload["detected"]), 1)
        self.assertTrue(payload["issues_labeled"][0]["label"])


if __name__ == "__main__":
    unittest.main()
