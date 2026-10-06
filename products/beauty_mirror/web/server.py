"""妆镜 Web 原型服务端：传图 → 报告 → 跟练。

纯标准库实现（零新增依赖），与 CLI 共用 beauty_mirror.tools 的领域工具。
启动：
    cd products
    python -m beauty_mirror.web.server            # 默认 http://127.0.0.1:8765
    python -m beauty_mirror.web.server --open      # 自动开浏览器
    python -m beauty_mirror.web.server --offline   # 全离线（视觉走 mock，现场最稳）
    python -m beauty_mirror.web.server --provider qwen_vl

第一阶段（Web 传图页）：
    POST /api/upload    上传照片 → 后台预热视觉 + 返回问卷
    POST /api/report    提交问卷答案 → 生成报告
    GET  /api/report    读取已生成报告
第二阶段（报告页 / 跟练页）：
    GET  /api/styles    风格标签清单
    POST /api/cards     按风格拉博主卡片（缓存优先，永不空手）
    POST /api/tutorial  妆教分步拆解（第三方没有就用自撰通用步骤兜底）
    GET  /api/qr        社群入口
"""

from __future__ import annotations

import argparse
import atexit
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import signal
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import tools
from ..config import DEMO, VISION
from ..domain import load_style_tags
from ..domain import skin_type as st
from ..providers import image_edit
from . import availability
from .plan import build_plan, maybe_llm_summary, plan_text, tutorial_fallback
from .sessions import Session, SessionStore

LOG = logging.getLogger("beauty_mirror.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "image/bmp"}
SID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# 图片代理：小红书图床在浏览器里时好时坏 → 服务端抓取 + 本地缓存，只用我们自己的源
IMG_CACHE_DIR = Path(tempfile.gettempdir()) / "beauty_mirror_imgcache"
IMG_PROXY_HOSTS = ("xhscdn.com", "placehold.co", "aliyuncs.com")


def _proxy_allowed(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return any(host == h or host.endswith("." + h) for h in IMG_PROXY_HOSTS)


def thumb_url(url: str, width: int = 720) -> str:
    """把小红书图床的大图换成缩略参数（实测原图可达 8MB，网页加载不动）。"""
    if not url:
        return url
    if "xhscdn.com" in url:
        return url.split("?")[0] + f"?imageView2/2/w/{width}/format/jpg/quality/80"
    return url

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".wasm": "application/wasm",
    ".task": "application/octet-stream",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

STORE = SessionStore()

# 静态页路由
PAGES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/report": "report.html",
    "/follow": "follow.html",
}


# ---------------------------------------------------------------------------
# 业务：生成报告 / 解析妆教
# ---------------------------------------------------------------------------


def _loads(payload: str) -> dict[str, Any]:
    try:
        return json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return {}


def _limitations(session: Session) -> list[str]:
    """视觉检测的固有限制：如实告知，避免把"未检出"当成"不存在"。"""
    out: list[str] = []
    width, height = session.image_width, session.image_height
    if width and height and max(width, height) < 1000:
        out.append(
            f"照片分辨率偏低（{width}×{height}），痘印、毛孔等细小特征容易被漏检；"
            "建议长边 ≥1000px、自然光下正对镜头重拍。"
        )
    out.append(
        "视觉检测对同一张照片可能给出不一致的结果，且会漏检——未检出 ≠ 不存在，"
        "请结合自身体感判断；涉及健康问题请就医。"
    )
    return out


def build_report(session: Session) -> dict[str, Any]:
    """跑完视觉 + 肤质 + 方案 + 合规，组装报告。"""
    vision = _loads(tools.detect_visible_issues(str(session.image_path), session.vision_provider))
    skin = _loads(tools.classify_skin_type(**{
        "tight": session.answers.get("tight", ""),
        "redness": session.answers.get("redness", ""),
        "oil": session.answers.get("oil", ""),
    }))

    # 逐题回显用户答案：让报告与问卷可对照（而不是只给一个结论）
    answers_display = []
    try:
        for question in st.question_sheet().get("questions", []):
            chosen = session.answers.get(question["id"], "")
            label = next(
                (opt.get("label") for opt in question.get("options", []) if opt.get("key") == chosen),
                "",
            )
            answers_display.append({
                "id": question["id"],
                "question": question["text"],
                "answer": label or "未回答",
                "answered": bool(label),
            })
    except Exception:  # noqa: BLE001 - 回显失败不影响报告
        answers_display = []
    skin["answers_display"] = answers_display

    issues = []
    for item in vision.get("issues_labeled") or []:
        issues.append({
            "type": item.get("type", ""),
            "label": item.get("label", ""),
            "confidence": item.get("confidence", 0),
            "severity": item.get("severity", ""),
            "areas": item.get("areas") or [],
        })

    plan = build_plan(skin, {**vision, "issues": issues})
    plan["limitations"] = _limitations(session)

    llm_summary = maybe_llm_summary(plan)
    if llm_summary:
        plan["llm_summary"] = llm_summary
        plan["generated_by"] = "rule+llm"

    compliance = _loads(tools.compliance_check(plan_text(plan)))

    session.vision = vision
    session.skin = skin
    session.plan = plan
    session.compliance = compliance
    session.report = {
        "ok": True,
        "sid": session.sid,
        "created_at": int(session.created_at),
        "image_url": f"/uploads/{session.sid}",
        "image_name": session.image_name,
        "image": {
            "width": session.image_width,
            "height": session.image_height,
        },
        "vision": {
            "provider": vision.get("provider", ""),
            "degraded": vision.get("degraded", False),
            "fallback_used": vision.get("fallback_used", False),
            "needs_retake": vision.get("needs_retake", False),
            "hint": vision.get("hint", ""),
            "error": vision.get("error", ""),
            "image_quality": vision.get("image_quality", {}),
            "issues": issues,
        },
        "skin": skin,
        "plan": plan,
        "compliance": compliance,
    }
    return session.report


def resolve_tutorial(session: Session, card_id: str, tutorial_id: str = "") -> dict[str, Any]:
    """妆教拆解：优先第三方卡片自带步骤，缺失则用风格标签 + 自撰通用步骤。"""
    card = session.cards.get(str(card_id)) or {}
    tutorials = card.get("tutorials") or []
    chosen = None
    for item in tutorials:
        if not tutorial_id or str(item.get("id")) == str(tutorial_id):
            chosen = item
            break
    if chosen and chosen.get("steps"):
        return {
            "ok": True,
            "card_id": str(card_id),
            "creator": card.get("creator", ""),
            "platform": card.get("platform", ""),
            "original_url": card.get("original_url", ""),
            "tutorial": {
                "id": chosen.get("id", ""),
                "title": chosen.get("title", ""),
                "duration": chosen.get("duration", ""),
                "steps": chosen.get("steps", []),
                "focus": chosen.get("focus", []),
                "source": "creator",
            },
        }

    # 第三方卡片没有分步：用所选风格的结构化教程 + 自撰通用步骤
    style = _find_style(session.style_tag)
    style_tutorials = (style or {}).get("tutorials") or []
    picked = None
    for item in style_tutorials:
        if not tutorial_id or str(item.get("id")) == str(tutorial_id):
            picked = item
            break
    if picked is None and style_tutorials:
        picked = style_tutorials[0]
    picked = picked or {"id": "", "title": "", "focus": []}

    fallback = tutorial_fallback((style or {}).get("name", ""), picked)
    return {
        "ok": True,
        "card_id": str(card_id),
        "creator": card.get("creator", ""),
        "platform": card.get("platform", ""),
        "original_url": card.get("original_url", ""),
        "tutorial": fallback,
        "notice": "该来源未提供分步数据，以下为平台自撰的通用跟练框架（非博主原文）。",
    }


def _find_style(query: str) -> dict[str, Any] | None:
    from ..domain import find_style_tag

    return find_style_tag(query)


def styles_payload() -> list[dict[str, Any]]:
    out = []
    for tag in load_style_tags():
        out.append({
            "id": tag.get("id", ""),
            "name": tag.get("name", ""),
            "aliases": tag.get("aliases") or [],
            "keywords": tag.get("keywords") or [],
            "tutorials": [
                {"id": t.get("id", ""), "title": t.get("title", ""), "focus": t.get("focus") or []}
                for t in (tag.get("tutorials") or [])
            ],
        })
    return out


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "BeautyMirrorWeb/0.1"
    protocol_version = "HTTP/1.1"

    # --- 工具 -------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 基类签名
        LOG.debug("%s - %s", self.address_string(), fmt % args)

    def _access_log(self, status: int) -> None:
        """结构化访问日志：只记方法/路由/状态/耗时，**不记请求体、密钥或照片内容**。"""
        try:
            route = getattr(self, "_route", (self.path or "").split("?", 1)[0])
            started = getattr(self, "_t0", None)
            elapsed = int((time.time() - started) * 1000) if started else -1
            log = LOG.info if route.startswith("/api/") else LOG.debug
            log("HTTP %s %s -> %s %dms", self.command, route, status, elapsed)
        except Exception:  # noqa: BLE001 - 日志绝不能影响响应
            pass

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                LOG.debug("客户端提前断开：%s", getattr(self, "_route", ""))
        self._access_log(status)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > MAX_UPLOAD_BYTES * 2:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc
        return loaded if isinstance(loaded, dict) else {}

    def _serve_file(self, path: Path, extra: dict[str, str] | None = None) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self._error("文件不存在", 404)
            return
        except OSError as exc:
            self._error(f"读取失败: {exc}", 500)
            return
        self._send(200, body, MIME_TYPES.get(path.suffix.lower(), "application/octet-stream"), extra)

    # --- GET --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._t0 = time.time()
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        self._route = route
        query = urllib.parse.parse_qs(parsed.query)
        STORE.purge_expired()

        if route in PAGES:
            self._serve_file(STATIC_DIR / PAGES[route])
            return
        if route.startswith("/static/"):
            self._serve_static(route[len("/static/"):])
            return
        if route.startswith("/uploads/"):
            self._serve_upload(route[len("/uploads/"):])
            return
        if route == "/api/img":
            self._serve_image_proxy(query)
            return
        if route == "/api/health":
            self._json({"ok": True, "provider": VISION["provider"], "offline": DEMO["offline"]})
            return
        if route == "/api/status":
            remote = str((query.get("apify") or ["1"])[0]).lower() not in ("0", "false", "no")
            self._json(availability.collect_status(check_apify=remote))
            return
        if route == "/api/styles":
            self._json({"ok": True, "styles": styles_payload()})
            return
        if route == "/api/report":
            self._get_report(query)
            return
        if route == "/api/qr":
            self._json(_loads(tools.get_community_qr()))
            return
        if route == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._error("未知路径", 404)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _serve_static(self, rel: str) -> None:
        rel = rel.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            self._error("非法路径", 403)
            return
        if not target.is_file():
            self._error("静态资源不存在", 404)
            return
        self._serve_file(target)

    def _serve_upload(self, sid: str) -> None:
        if not SID_RE.match(sid or ""):
            self._error("非法会话", 400)
            return
        session = STORE.get(sid)
        if session is None:
            self._error("会话不存在或已过期", 404)
            return
        self._serve_file(
            session.image_path,
            extra={"Content-Disposition": "inline", "Cache-Control": "private, max-age=600"},
        )

    def _serve_image_proxy(self, query: dict[str, list[str]]) -> None:
        """图片代理：服务端拓一次并缓存，避开图床对浏览器的偶发拦截。只允许白名单域名。"""
        url = (query.get("url") or [""])[0]
        if not url or not _proxy_allowed(url):
            self._error("不允许的图片地址", 403)
            return
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            suffix = ".jpg"
        cache = IMG_CACHE_DIR / (key + suffix)
        if not cache.exists():
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    data = response.read(8_000_000)
                if not data:
                    raise ValueError("empty image")
                IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(data)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("图片代理失败 %s: %s", url[:60], type(exc).__name__)
                self._error("图片获取失败", 502)
                return
        self._serve_file(cache, extra={"Cache-Control": "public, max-age=86400"})

    def _get_report(self, query: dict[str, list[str]]) -> None:
        sid = (query.get("sid") or [""])[0]
        session = STORE.get(sid)
        if session is None:
            self._error("会话不存在或已过期", 404)
            return
        if not session.report:
            self._json({"ok": True, "ready": False, "sid": session.sid})
            return
        self._json(session.report)

    # --- POST -------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        self._t0 = time.time()
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        self._route = route
        STORE.purge_expired()
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._error(str(exc), 413)
            return

        handlers = {
            "/api/upload": self._post_upload,
            "/api/report": self._post_report,
            "/api/cards": self._post_cards,
            "/api/tutorial": self._post_tutorial,
            "/api/beautify": self._post_beautify,
        }
        handler = handlers.get(route)
        if handler is None:
            self._error("未知接口", 404)
            return
        try:
            handler(payload)
        except Exception as exc:  # noqa: BLE001 - 任何异常都转成结构化错误，不白屏
            LOG.exception("接口 %s 失败", route)
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def _post_upload(self, payload: dict[str, Any]) -> None:
        # 上传前必须显式同意隐私说明（前端勾选框）；不同意就不接收照片
        if payload.get("consent") is not True:
            self._error("上传前需先阅读并勾选隐私说明", 400)
            return
        data = str(payload.get("data_base64") or "")
        if "," in data and data.strip().startswith("data:"):
            data = data.split(",", 1)[1]
        if not data:
            self._error("缺少图片数据")
            return
        try:
            image_bytes = base64.b64decode(data, validate=False)
        except Exception as exc:  # noqa: BLE001
            self._error(f"图片解码失败: {exc}")
            return
        if not image_bytes:
            self._error("图片为空")
            return
        if len(image_bytes) > MAX_UPLOAD_BYTES:
            self._error("图片超过 10MB，请压缩后重试", 413)
            return

        mime = str(payload.get("mime") or _sniff_mime(image_bytes)).lower()
        if mime not in ALLOWED_MIME:
            self._error(f"不支持的图片类型: {mime}（支持 JPEG/PNG/WebP/GIF/BMP）")
            return

        sid = secrets.token_urlsafe(12)
        session = STORE.create(sid, image_bytes, str(payload.get("filename") or ""), mime)
        session.vision_provider = VISION["provider"]
        try:
            session.image_width = int(payload.get("width") or 0)
            session.image_height = int(payload.get("height") or 0)
        except (TypeError, ValueError):
            session.image_width = session.image_height = 0

        # 照片一到就后台预热视觉（与用户答问卷并行）
        prefetch = _loads(tools.start_vision_prefetch(str(session.image_path), session.vision_provider))
        questionnaire = _loads(tools.get_questionnaire())

        LOG.info("新会话 %s 图片 %.1fKB provider=%s", sid, len(image_bytes) / 1024, session.vision_provider)
        self._json({
            "ok": True,
            "sid": sid,
            "image_url": f"/uploads/{sid}",
            "vision_provider": session.vision_provider,
            "prefetch": prefetch.get("status", ""),
            "questionnaire": questionnaire.get("questions", []),
        })

    def _post_report(self, payload: dict[str, Any]) -> None:
        session = STORE.get(str(payload.get("sid") or ""))
        if session is None:
            self._error("会话不存在或已过期，请重新上传照片", 404)
            return
        answers = payload.get("answers") or {}
        if not isinstance(answers, dict):
            self._error("answers 必须是对象")
            return
        session.answers = {k: str(v) for k, v in answers.items()}
        try:
            report = build_report(session)
        except Exception:  # noqa: BLE001 - 单次失败给可重试提示，不让前端白屏
            LOG.exception("生成报告失败 sid=%s", session.sid)
            self._json({"ok": False, "error": "生成报告失败，请稍后重试", "retryable": True}, 500)
            return
        LOG.info("报告已生成 sid=%s issues=%d provider=%s",
                 session.sid, len(session.report["vision"]["issues"]), session.vision_provider)
        self._json(report)

    def _post_cards(self, payload: dict[str, Any]) -> None:
        session = STORE.get(str(payload.get("sid") or ""))
        if session is None:
            self._error("会话不存在或已过期", 404)
            return
        style_tag = str(payload.get("style_tag") or "").strip()
        if not style_tag:
            self._error("缺少 style_tag")
            return
        limit = int(payload.get("limit") or 6)
        result = _loads(tools.fetch_creator_cards(style_tag, limit=limit))
        session.style_tag = style_tag
        session.cards = {}
        cards = []
        for card in result.get("cards", []):
            session.cards[str(card.get("id"))] = card
            extra = card.get("raw_extra") or {}
            cards.append({
                "id": card.get("id", ""),
                "platform": card.get("platform", ""),
                "creator": card.get("creator", ""),
                "creator_avatar": thumb_url(card.get("creator_avatar", ""), 96),
                "creator_profile": card.get("creator_profile", ""),
                "title": card.get("title", ""),
                "summary": card.get("summary", ""),
                "cover_url": thumb_url(card.get("cover_url", "")),
                "original_url": card.get("original_url", ""),
                "style_tags": card.get("style_tags") or [],
                "like_count": extra.get("like_count"),
                "text_stripped": card.get("text_stripped", False),
                "has_tutorials": bool(card.get("tutorials")),
                "source": card.get("source", ""),
            })
        style = _find_style(style_tag) or {}
        self._json({
            "ok": True,
            "style_tag": {
                "id": style.get("id", ""),
                "name": style.get("name", style_tag),
                "aliases": style.get("aliases") or [],
            },
            "source": result.get("source", ""),
            "degraded": result.get("degraded", False),
            "notice": result.get("notice", ""),
            "count": len(cards),
            "cards": cards,
            "style_tutorials": [
                {"id": t.get("id", ""), "title": t.get("title", ""), "focus": t.get("focus") or []}
                for t in (style.get("tutorials") or [])
            ],
        })

    def _post_tutorial(self, payload: dict[str, Any]) -> None:
        session = STORE.get(str(payload.get("sid") or ""))
        if session is None:
            self._error("会话不存在或已过期", 404)
            return
        card_id = str(payload.get("card_id") or "")
        tutorial_id = str(payload.get("tutorial_id") or "")
        # 平台跟练脚本可以不带 card_id（只用风格标签下的 tutorial_id）
        if not card_id and not tutorial_id:
            self._error("缺少 card_id 或 tutorial_id")
            return
        if payload.get("style_tag"):
            session.style_tag = str(payload.get("style_tag"))
        self._json(resolve_tutorial(session, card_id, tutorial_id))

    def _post_beautify(self, payload: dict[str, Any]) -> None:
        """AI 带妆效果图：用上传的照片 + 风格名生成一张“你化完妆”的预览（仅风格参考）。"""
        session = STORE.get(str(payload.get("sid") or ""))
        if session is None:
            self._error("会话不存在或已过期", 404)
            return
        style_tag = str(payload.get("style_tag") or session.style_tag or "").strip()
        if not style_tag:
            self._error("缺少 style_tag")
            return
        if not session.image_path.exists():
            self._error("照片已失效，请重新上传", 410)
            return
        cached = session.beautified.get(style_tag)
        if cached:
            self._json(cached)
            return
        style = _find_style(style_tag) or {}
        style_name = str(style.get("name") or style_tag)
        keywords = tuple(str(k) for k in (style.get("keywords") or []))
        focus = tuple(dict.fromkeys(
            str(f) for t in (style.get("tutorials") or []) for f in (t.get("focus") or [])
        ))
        skin_type = str((session.skin or {}).get("skin_type") or "")
        issues = tuple(
            str(item.get("type"))
            for item in ((session.report or {}).get("vision", {}) or {}).get("issues", []) or []
        )
        reference_url = str(payload.get("reference_url") or "").strip()
        makeup_spec = ""
        if reference_url:
            ref_bytes = image_edit.fetch_bytes(reference_url)
            if ref_bytes:
                makeup_spec = image_edit.extract_makeup_spec(ref_bytes)  # 只读“妆”，不读“脸”
        prompt = image_edit.build_prompt(style_name, keywords, focus, skin_type, issues,
                                         makeup_spec=makeup_spec, recipe=style.get("recipe"))
        LOG.info("生成带妆效果图 sid=%s style=%s spec_len=%d", session.sid, style_tag, len(makeup_spec))
        result = image_edit.generate_many(session.image_path, prompt, n=3,
                                          width=session.image_width, height=session.image_height)
        ok = [r for r in result if r.get("ok")]
        if not ok:
            err = next((r.get("error") for r in result if r.get("error")), "生成失败")
            self._json({"ok": False, "error": err, "fallback": "cover"}, 200)
            return
        urls = [r["image_url"] for r in ok]
        scores = image_edit.score_candidates(session.image_path, urls, style_name)
        by_index = {s.get("index"): s for s in scores if isinstance(s.get("index"), int)}
        candidates = []
        for i, u in enumerate(urls):
            s = by_index.get(i) or {}
            candidates.append({
                "index": i,
                "image_url": u,
                "score": {"style": s.get("style"), "identity": s.get("identity"), "natural": s.get("natural")},
                "note": s.get("note", ""),
            })

        def _total(c: dict) -> float:
            sc = c.get("score") or {}
            vals = [v for v in (sc.get("style"), sc.get("identity"), sc.get("natural"))
                    if isinstance(v, (int, float))]
            return sum(vals) if vals else -1

        recommended = max(range(len(candidates)), key=lambda i: _total(candidates[i])) if candidates else 0
        out = {
            "ok": True,
            "style_tag": style_tag,
            "style_name": style_name,
            "kind": "generated",
            "candidates": candidates,
            "recommended": recommended,
            "image_url": candidates[recommended]["image_url"],
            "used_reference": bool(makeup_spec),
            "makeup_spec": makeup_spec,
            "label": "AI 生成的你的带妆效果图（仅供风格参考）",
            "notice": "由通义万相生成，非真实照片；评分为视觉模型初筛，仅作参考。",
        }
        session.beautified[style_tag] = out
        self._json(out)


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------


def serve(host: str, port: int, open_browser: bool = False) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    atexit.register(STORE.close)

    def _graceful(signum, _frame):  # SIGTERM/SIGINT：优雅退出并删除临时照片目录
        LOG.info("收到信号 %s，正在退出并清理临时目录…", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _graceful)
        except (ValueError, OSError):
            pass  # 非主线程等场景下忽略

    url = f"http://{host}:{port}/"
    LOG.info("会话临时目录: %s", STORE.root)
    print("=" * 58, flush=True)
    print("  妆镜 Web 原型已启动", flush=True)
    print(f"  地址：{url}", flush=True)
    try:
        status = availability.collect_status(check_apify=not DEMO["offline"])
        apify = status["apify"]
        content = "可用" if apify["usable"] and not apify.get("preview_limited") else "仅缓存/示例"
        print(f"  视觉段：{status['vision']['provider']}（{status['vision']['mode']}）", flush=True)
        print(f"  第三方内容：Apify {content}；本条：{apify.get('reason', '')}", flush=True)
        print(f"  内容缓存：{status['cache']['total_cards']} 条 / {len(status['cache']['styles'])} 个风格", flush=True)
    except Exception as exc:  # noqa: BLE001 - 状态探测失败不能影响启动
        LOG.warning("启动状态探测失败：%s", exc)
    print("  按 Ctrl+C 停止", flush=True)
    print("=" * 58, flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
        STORE.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="妆镜 Web 原型")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "8765")))
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--offline", action="store_true", help="全离线：视觉走 mock")
    parser.add_argument("--provider", default="", help="视觉 provider（qwen_vl/doubao_vision/glm_4v/mock）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # 防止底层 HTTP 客户端在 DEBUG 时把带 Authorization 头的请求/图片体打进日志
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.offline:
        DEMO["offline"] = True
        VISION["provider"] = "mock"
    if args.provider:
        VISION["provider"] = args.provider

    serve(args.host, args.port, open_browser=args.open)
    return 0


if __name__ == "__main__":
    sys.exit(main())
