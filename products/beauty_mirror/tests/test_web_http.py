"""Web 真实 HTTP 集成测试：起一个本地服务，用 urllib 走完整链路。

不联网、不需要密钥（强制 mock 视觉 + 离线模式）。
守的契约：
  - 未勾选隐私确认 → 拒绝上传（400），不接收照片
  - 勾选后可上传 → 问卷 3 题 → 报告（头部问题数 == 列表条数）→ 跟练卡片 → 妆教分步
  - /api/status 结构完整（不泄露密钥）
  - 无效会话 → 404

运行：cd products && python -m unittest beauty_mirror.tests.test_web_http -v
"""

from __future__ import annotations

import base64
import json
import struct
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from http.server import ThreadingHTTPServer

from ..config import DEMO, VISION
from ..web.server import Handler


def _png(width: int = 48, height: int = 48) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw += bytes((226 - (x % 20), 190 - (y % 18), 172, 255))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


class TestWebHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._old_provider = VISION["provider"]
        cls._old_offline = DEMO["offline"]
        VISION["provider"] = "mock"
        DEMO["offline"] = True
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        VISION["provider"] = cls._old_provider
        DEMO["offline"] = cls._old_offline

    # --- 辅助 -------------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str):
        with urllib.request.urlopen(self._url(path), timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, body: dict):
        request = urllib.request.Request(
            self._url(path), data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    # --- 用例 -------------------------------------------------------------
    def test_upload_requires_consent(self):
        status, data = self._post("/api/upload", {
            "filename": "face.png", "mime": "image/png",
            "data_base64": base64.b64encode(_png()).decode(),
        })
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertIn("隐私", data["error"])

    def test_full_flow(self):
        status, up = self._post("/api/upload", {
            "filename": "face.png", "mime": "image/png",
            "data_base64": base64.b64encode(_png()).decode(), "consent": True,
        })
        self.assertEqual(status, 200)
        sid = up["sid"]
        self.assertEqual(len(up["questionnaire"]), 3)

        status, report = self._post("/api/report", {
            "sid": sid, "answers": {"tight": "yes", "redness": "sometimes", "oil": "yes"},
        })
        self.assertEqual(status, 200)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["vision"]["issues"]), len(report["plan"]["target"]))
        self.assertEqual(report["compliance"]["status"], "pass")
        self.assertIn("可见问题", report["plan"]["headline"])
        # 结论依据 + 问卷逐题回显 + 检测局限
        self.assertTrue(report["plan"].get("basis"))
        self.assertTrue(report["plan"].get("limitations"))
        self.assertEqual(len(report["skin"].get("answers_display") or []), 3)

        # 报告可复读
        status, again = self._get(f"/api/report?sid={sid}")
        self.assertEqual(status, 200)
        self.assertEqual(again["plan"]["headline"], report["plan"]["headline"])

        # 跟练
        status, cards = self._post("/api/cards", {"sid": sid, "style_tag": "清透裸妆", "limit": 3})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(cards["count"], 1)
        card_id = cards["cards"][0]["id"]
        status, tutorial = self._post("/api/tutorial", {
            "sid": sid, "card_id": card_id, "style_tag": "清透裸妆",
        })
        self.assertEqual(status, 200)
        self.assertTrue(tutorial["tutorial"]["steps"])

    def test_status_endpoint(self):
        status, data = self._get("/api/status?apify=0")
        self.assertEqual(status, 200)
        for key in ("vision", "apify", "cache"):
            self.assertIn(key, data)
        blob = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("apify_api_", blob)

    def test_report_unknown_session_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/report?sid=INVALIDSESSION99")
        self.assertEqual(ctx.exception.code, 404)

    def test_static_pages_served(self):
        for path in ("/", "/report", "/follow", "/static/app.js", "/static/app.css"):
            with urllib.request.urlopen(self._url(path), timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                self.assertGreater(len(resp.read()), 100)


if __name__ == "__main__":
    unittest.main()
