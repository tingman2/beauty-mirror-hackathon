"""Web 层契约测试：不联网、不需要密钥。

守两条不变量：
  1. 规则生成的方案文案**必须通过** compliance_check（否则等于把违规内容发给用户）；
  2. 妆教拆解**永远有分步**（第三方卡片没有时用平台自撰框架兜底，跟练页不空）。

运行：cd products && python -m unittest beauty_mirror.tests.test_web_contract -v
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ..domain import skin_type as st
from ..tools import compliance_check
from ..web import availability
from ..web.plan import build_plan, plan_text, tutorial_fallback
from ..web.sessions import Session, SessionStore
from ..web.server import build_report, resolve_tutorial


def _vision(issues):
    return {
        "provider": "mock",
        "degraded": False,
        "fallback_used": False,
        "needs_retake": False,
        "issues": issues,
    }


ALL_ISSUES = [
    {"type": "acne_marks", "label": "痘印", "confidence": 0.72, "severity": "mild", "areas": ["左脸颊"]},
    {"type": "redness", "label": "泛红", "confidence": 0.66, "severity": "moderate", "areas": ["双颊"]},
    {"type": "t_zone_oil", "label": "T区油光", "confidence": 0.81, "severity": "moderate", "areas": ["T区"]},
    {"type": "pores", "label": "毛孔", "confidence": 0.55, "severity": "mild", "areas": ["鼻翼"]},
    {"type": "dark_circles", "label": "黑眼圈", "confidence": 0.61, "severity": "mild", "areas": ["眼下"]},
]


class TestPlanCompliance(unittest.TestCase):
    """方案文案在任意肤质 × 任意问题组合下都必须合规。"""

    CASES = [
        {"tight": "yes", "redness": "no", "oil": "no"},     # 干性
        {"tight": "no", "redness": "no", "oil": "yes"},     # 油性
        {"tight": "yes", "redness": "no", "oil": "yes"},    # 混合
        {"tight": "no", "redness": "often", "oil": "no"},   # 敏感
        {"tight": "no", "redness": "no", "oil": "no"},      # 中性
    ]

    def test_every_case_passes_compliance(self):
        for answers in self.CASES:
            skin = st.classify(answers)
            for issues in ([], ALL_ISSUES):
                plan = build_plan(skin, _vision(issues))
                result = json.loads(compliance_check(plan_text(plan)))
                self.assertEqual(
                    result["status"], "pass",
                    msg=f"{answers} + {len(issues)} issues 命中违规: {result['hits']}",
                )

    def test_structure_is_complete(self):
        plan = build_plan(st.classify({"tight": "yes", "redness": "no", "oil": "no"}), _vision(ALL_ISSUES))
        self.assertTrue(plan["sections"])
        self.assertEqual(len(plan["target"]), len(ALL_ISSUES))
        self.assertTrue(plan["makeup"])
        self.assertTrue(plan["disclaimer"])

    def test_empty_issues_is_handled(self):
        plan = build_plan(st.classify({"tight": "no", "redness": "no", "oil": "no"}), _vision([]))
        self.assertIn("未见明显可见问题", plan["headline"])
        self.assertEqual(plan["target"], [])

    def test_conflict_detected_dry_vs_tzone(self):
        plan = build_plan(
            st.classify({"tight": "yes", "redness": "no", "oil": "no"}),
            _vision([{"type": "t_zone_oil", "label": "T区油光", "confidence": 0.8, "severity": "mild", "areas": []}]),
        )
        self.assertTrue(plan["conflicts"])

    def test_oil_answer_without_tzone_detection_flags_uncertainty(self):
        """问卷说 T 区出油，但照片没检出 → 必须提示不确定，而不是当作“没问题”。"""
        answers = {"tight": "no", "redness": "no", "oil": "yes"}
        skin = {**st.classify(answers), "answers": answers}
        plan = build_plan(skin, _vision([]))
        joined = " ".join(plan["conflicts"])
        self.assertIn("T 区", joined)
        self.assertIn("未检出", joined)

    def test_redness_answer_without_detection_flags(self):
        answers = {"tight": "no", "redness": "often", "oil": "no"}
        skin = {**st.classify(answers), "answers": answers}
        plan = build_plan(skin, _vision([]))
        self.assertIn("泛红", " ".join(plan["conflicts"]))


class TestTutorialFallback(unittest.TestCase):
    def test_focus_generates_steps(self):
        tutorial = {"id": "t1", "title": "水光底妆", "focus": ["妆前", "底妆", "高光"]}
        result = tutorial_fallback("韩系水光", tutorial)
        self.assertGreaterEqual(len(result["steps"]), 3)
        self.assertEqual(result["source"], "blueprint")

    def test_empty_focus_still_has_steps(self):
        result = tutorial_fallback("清透裸妆", {"id": "x", "title": "", "focus": []})
        self.assertTrue(result["steps"])


class TestResolveTutorial(unittest.TestCase):
    def _session(self) -> Session:
        tmp = Path(tempfile.mkdtemp(prefix="bm_test_"))
        image = tmp / "a.jpg"
        image.write_bytes(b"\xff\xd8\xff")
        return Session(sid="testsid12345", image_path=image)

    def test_third_party_card_steps_win(self):
        session = self._session()
        session.cards["c1"] = {
            "id": "c1",
            "creator": "博主A",
            "platform": "xiaohongshu",
            "original_url": "https://example.com/c1",
            "tutorials": [{"id": "t1", "title": "原步骤", "steps": ["a", "b"], "focus": []}],
        }
        result = resolve_tutorial(session, "c1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["tutorial"]["source"], "creator")
        self.assertEqual(result["tutorial"]["steps"], ["a", "b"])

    def test_missing_steps_fall_back_to_blueprint(self):
        session = self._session()
        session.style_tag = "韩系水光"
        session.cards["c2"] = {
            "id": "c2",
            "creator": "博主B",
            "platform": "xiaohongshu",
            "original_url": "https://example.com/c2",
            "tutorials": [],
        }
        result = resolve_tutorial(session, "c2")
        self.assertTrue(result["ok"])
        self.assertEqual(result["tutorial"]["source"], "blueprint")
        self.assertTrue(result["tutorial"]["steps"])


class TestBuildReport(unittest.TestCase):
    """集成回归：报告头部的问题数必须与列表一致（曾经错位过）。"""

    def _session(self) -> Session:
        tmp = Path(tempfile.mkdtemp(prefix="bm_test_"))
        image = tmp / "a.jpg"
        image.write_bytes(b"\xff\xd8\xff")
        session = Session(sid="reportsid12345", image_path=image)
        session.vision_provider = "mock"
        session.answers = {"tight": "yes", "redness": "sometimes", "oil": "yes"}
        return session

    def test_headline_matches_issue_list(self):
        session = self._session()
        report = build_report(session)
        count = len(report["vision"]["issues"])
        self.assertGreaterEqual(count, 1)  # mock 默认给 2 项
        self.assertIn(f"可见问题 {count} 项", report["plan"]["headline"])
        self.assertEqual(len(report["plan"]["target"]), count)
        self.assertEqual(report["compliance"]["status"], "pass")
        self.assertTrue(report["image_url"])

    def test_low_resolution_and_disclaimer_limitations(self):
        session = self._session()
        session.image_width, session.image_height = 616, 687
        report = build_report(session)
        joined = " ".join(report["plan"]["limitations"])
        self.assertIn("616", joined)
        self.assertIn("未检出", joined)
        self.assertEqual(report["image"]["width"], 616)


class TestAvailability(unittest.TestCase):
    """可用性探测：结构完整、不抛异常、不泄露密钥。"""

    def test_collect_status_shape(self):
        status = availability.collect_status(check_apify=False)
        for key in ("vision", "apify", "cache"):
            self.assertIn(key, status)
        self.assertIn("provider", status["vision"])
        self.assertIn("token_configured", status["apify"])
        self.assertIn("styles", status["cache"])

    def test_status_never_leaks_secret(self):
        import os

        blob = json.dumps(availability.collect_status(check_apify=False), ensure_ascii=False)
        # 真实密钥值绝不能出现在对外状态里
        for env in ("DASHSCOPE_API_KEY", "ARK_API_KEY", "ZHIPU_API_KEY", "APIFY_TOKEN", "ANTHROPIC_API_KEY"):
            value = (os.getenv(env) or "").strip()
            if value:
                self.assertNotIn(value, blob, msg=f"{env} 的值不应出现在状态输出中")
        self.assertNotIn("Bearer ", blob)

    def test_apify_offline_check_does_not_raise(self):
        result = availability.apify_status(check_remote=False)
        self.assertIsInstance(result, dict)


class TestPrefetchTimeout(unittest.TestCase):
    """预热超时必须降级返回，不能无限阻塞 Web 请求。"""

    def test_timeout_degrades_instead_of_blocking(self):
        from .. import tools
        from ..config import VISION

        image = "/tmp/bm_timeout_probe.jpg"
        key = tools._vision_key(image, "")
        stop = threading.Event()
        slow = threading.Thread(target=lambda: stop.wait(5), daemon=True)
        slow.start()
        tools._PREFETCH[key] = slow
        old_wait = VISION["prefetch_wait_seconds"]
        VISION["prefetch_wait_seconds"] = 0.3
        try:
            started = time.time()
            payload = json.loads(tools.detect_visible_issues(image, ""))
            elapsed = time.time() - started
            self.assertLess(elapsed, 2.0, "预热超时应立即降级，而不是阻塞 5s")
            self.assertTrue(payload["fallback_used"])
            self.assertGreaterEqual(len(payload["detected"]), 1)
        finally:
            VISION["prefetch_wait_seconds"] = old_wait
            stop.set()
            tools._PREFETCH.pop(key, None)


class TestSessionStore(unittest.TestCase):
    def test_create_get_drop(self):
        store = SessionStore()
        session = store.create("abc12345678", b"\xff\xd8\xff", "face.jpg", "image/jpeg")
        self.assertTrue(session.image_path.exists())
        self.assertIs(store.get("abc12345678"), session)
        folder = session.image_path.parent
        store.drop("abc12345678")
        self.assertIsNone(store.get("abc12345678"))
        self.assertFalse(folder.exists())
        store.close()

    def test_unknown_sid_returns_none(self):
        store = SessionStore()
        self.assertIsNone(store.get("does-not-exist"))
        store.close()


if __name__ == "__main__":
    unittest.main()
