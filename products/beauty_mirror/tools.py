"""领域工具实现：只做 I/O 与确定性规则，推理交给文本段模型。

设计原则（对齐 BLUEPRINT「模型就是 Agent，代码只跑循环」）：
  - 工具管"取数据 / 算规则 / 查词表"，模型管"推理与写作"；
  - **合规审核用规则而非模型自审**：避免同一模型自己批自己；
  - 每个工具都失败降级、返回结构化 JSON，不抛异常。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import VISION
from .domain import ISSUE_TAXONOMY, find_style_tag, label_of, load_style_tags
from .domain import skin_type as st
from .providers import fetch_creator_cards as _fetch_cards
from .providers import get_vision_provider
from .providers.vision import MockVisionProvider

LOG = logging.getLogger("beauty_mirror.tools")

# 会话内缓存：让 get_tutorial_breakdown 能查到刚拉到的卡片
_CARD_CACHE: dict[str, dict[str, Any]] = {}
_LAST_VISION: dict[str, Any] = {}

# 视觉预热：视觉调用很慢（豆包实测中位 18s），但用户答 3 道问卷要 5-8 秒。
# 照片一到就后台开跑，等用户答完问卷再取结果——把延迟藏起来。
_PREFETCH: dict[str, threading.Thread] = {}
_PREFETCH_RESULT: dict[str, str] = {}


def _vision_key(image_path: str, provider: str) -> str:
    return f"{Path(image_path).expanduser()}|{provider or 'default'}"


def start_vision_prefetch(image_path: str, provider: str = "") -> str:
    """照片上传后立刻调：后台并行做视觉检测，别阻塞用户答问卷。"""
    key = _vision_key(image_path, provider)
    existing = _PREFETCH.get(key)
    if existing is not None and existing.is_alive():
        return _dump({"ok": True, "status": "already_running", "key": key})

    def _work() -> None:
        try:
            _PREFETCH_RESULT[key] = _detect_visible_issues(image_path, provider)
        except Exception as exc:  # noqa: BLE001 - 后台线程绝不能让异常冒出去
            LOG.warning("视觉预热失败: %s", exc)

    thread = threading.Thread(target=_work, name="vision-prefetch", daemon=True)
    _PREFETCH[key] = thread
    thread.start()
    return _dump({
        "ok": True,
        "status": "prefetching",
        "hint": "已后台开始视觉检测；请同时向用户展示 3 道问卷",
    })


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 1. 视觉段：只检测可见问题
# ---------------------------------------------------------------------------


def detect_visible_issues(image_path: str, provider: str = "") -> str:
    """检测照片中可见皮肤问题（痘印/泛红/T区油光/毛孔/黑眼圈）。

    返回闭集结果；provider 失败时返回 degraded=true，由上层决定是否提示用户重拍。
    若之前调过 start_vision_prefetch，这里会等它跑完（此时用户已答完问卷）。
    等待**有上限**（VISION_PREFETCH_WAIT_SECONDS）：网络黑洞时也不无限阻塞 Web 请求。
    """
    key = _vision_key(image_path, provider)
    thread = _PREFETCH.get(key)
    if thread is not None and thread is not threading.current_thread():
        wait = float(VISION.get("prefetch_wait_seconds", 45.0))
        if thread.is_alive():
            LOG.info("复用视觉预热结果（最多再等 %.0fs）", wait)
            thread.join(timeout=wait)
        if thread.is_alive():
            LOG.warning("视觉预热超时（>%.0fs），降级返回示例结果", wait)
            return _fallback_payload(image_path, provider, f"视觉检测超时（>{wait:.0f}s）")
        cached = _PREFETCH_RESULT.get(key)
        if cached:
            return cached
    return _detect_visible_issues(image_path, provider)


def _fallback_payload(image_path: str, provider: str, reason: str) -> str:
    """真实 provider 不可用/超时时的兜底：用本地 mock 结果垫底并**明确标注**。

    绝不伪造：返回 degraded=true + fallback_used=true + 说明文案，前端会显著提示。
    """
    fallback = MockVisionProvider().analyze(Path(image_path).expanduser())
    payload = fallback.to_dict()
    payload.update({
        "ok": False,
        "provider": provider or VISION["provider"],
        "fallback_used": True,
        "needs_retake": False,
        "issues_labeled": [
            {**item, "label": label_of(item["type"])} for item in fallback.detected
        ],
        "hint": f"视觉检测不可用（{reason}），已用本地示例结果垫底；请勿当作真实检测结论。",
    })
    _LAST_VISION.clear()
    _LAST_VISION.update(payload)
    return _dump(payload)


def _detect_visible_issues(image_path: str, provider: str = "") -> str:
    """真实的视觉调用（不含预热复用逻辑）。"""
    path = Path(image_path).expanduser()
    try:
        vision = get_vision_provider(provider or None)
    except KeyError as exc:
        return _dump({"ok": False, "error": str(exc)})

    result = vision.analyze(path)
    used_fallback = False

    # 兜底：真实 provider 失败且没给出任何结果时，用本地 mock 垫底。
    # 目的：demo 现场既不报错也不白屏（见 PRD 第三轮「兜底必须加」）。
    if result.degraded and not result.detected and vision.name != "mock":
        fallback = MockVisionProvider().analyze(path)
        if fallback.detected:
            used_fallback = True
            result = replace(
                result,
                image_quality=fallback.image_quality,
                detected=fallback.detected,
            )

    payload = result.to_dict()
    payload["ok"] = not result.degraded
    payload["fallback_used"] = used_fallback
    payload["issues_labeled"] = [
        {**item, "label": label_of(item["type"])} for item in result.detected
    ]

    # 画质不达标且无检出：不是脏数据，也不能静默返回空——要明确让用户重拍
    quality = result.image_quality or {}
    unusable = (not result.detected) and (quality.get("ok") is False)
    payload["needs_retake"] = bool(unusable)
    if unusable:
        reasons = "、".join(str(x) for x in (quality.get("issues") or [])) or "图片质量不足"
        payload["hint"] = f"照片质量不足（{reasons}）。请在自然光下正对镜头重拍一张无妆素颜照。"

    _LAST_VISION.clear()
    _LAST_VISION.update(payload)

    if used_fallback:
        payload["hint"] = (
            f"真实视觉服务不可用（{result.error}），已用本地示例结果垫底；"
            "请勿当作真实检测结论。"
        )
    elif result.degraded and not payload.get("hint"):
        payload["hint"] = (
            "视觉检测降级（未得到真实结果）。建议：换自然光重拍，或直接走问卷结论。"
        )
    return _dump(payload)


# ---------------------------------------------------------------------------
# 2. 肤质问卷（3 道，确定性规则）
# ---------------------------------------------------------------------------


def get_questionnaire() -> str:
    return _dump({"ok": True, **st.question_sheet()})


def classify_skin_type(tight: str = "", redness: str = "", oil: str = "") -> str:
    """把 3 个答案映射成肤质结论（纯规则，零模型成本）。"""
    answers = {"tight": tight, "redness": redness, "oil": oil}
    result = st.classify(answers)
    return _dump({"ok": True, "answers": answers, **result})


# ---------------------------------------------------------------------------
# 3. 内容：第三方 API + mock 兜底
# ---------------------------------------------------------------------------


def fetch_creator_cards(style_tag: str, platform: str = "", limit: int = 6) -> str:
    """按风格标签拉博主代表视频/妆教文案卡片。上游挂了自动走本地 mock。"""
    resolved = find_style_tag(style_tag) or {}
    query = str(resolved.get("name") or style_tag or "")
    result = _fetch_cards(query, platform or None, limit)
    for card in result.cards:
        _CARD_CACHE[str(card.get("id"))] = card

    payload = result.to_dict()
    payload["ok"] = True  # 兜底保证永远有内容，前端不白屏
    payload["style_tag"] = {
        "id": resolved.get("id", ""),
        "name": query,
        "aliases": resolved.get("aliases", []),
    }
    if result.degraded:
        payload["notice"] = "上游内容服务暂不可用，已展示预置示例内容。"
    return _dump(payload)


def get_tutorial_breakdown(card_id: str, tutorial_id: str = "") -> str:
    """取某张卡片下某个妆教的拆解步骤。"""
    card = _CARD_CACHE.get(str(card_id))
    if card is None:
        return _dump({
            "ok": False,
            "error": f"未找到卡片 {card_id}；请先调用 fetch_creator_cards 拉取。",
        })
    tutorials = card.get("tutorials") or []
    if not tutorials:
        return _dump({
            "ok": False,
            "error": "该卡片暂无妆教拆解数据",
            "card_id": card_id,
            "original_url": card.get("original_url", ""),
        })
    chosen = None
    for item in tutorials:
        if not tutorial_id or str(item.get("id")) == str(tutorial_id):
            chosen = item
            break
    if chosen is None:
        return _dump({
            "ok": False,
            "error": f"卡片 {card_id} 下没有妆教 {tutorial_id}",
            "available": [t.get("id") for t in tutorials],
        })
    return _dump({
        "ok": True,
        "card_id": card_id,
        "creator": card.get("creator", ""),
        "platform": card.get("platform", ""),
        "original_url": card.get("original_url", ""),
        "tutorial": chosen,
    })


# ---------------------------------------------------------------------------
# 4. 合规审核（规则，不交给模型自审）
# ---------------------------------------------------------------------------

_MEDICAL_REDLINE = (
    "诊断", "确诊", "治疗", "治愈", "根治", "痊愈", "处方", "用药", "药物",
    "消炎", "医美级", "药用", "疗效", "病症",
)
_ABSOLUTE_CLAIM = (
    "最好", "第一", "唯一", "100%", "百分百", "彻底", "永久", "立竿见影",
    "7天见效", "三天见效", "立刻见效", "无效退款", "包治",
)
_PROMISE = ("保证", "一定能", "必定", "绝对有效", "必好")


def compliance_check(text: str) -> str:
    """生成内容的合规体检：医疗红线 / 绝对化用语 / 效果承诺。

    返回 status = pass | rework。命中即要求改写，不允许直接放行。
    """
    body = text or ""
    hits: list[dict[str, str]] = []
    for word in _MEDICAL_REDLINE:
        if word in body:
            hits.append({"category": "医疗红线", "word": word,
                         "fix": "改为『建议尽早就医确认』，不要给诊断或治疗方案"})
    for word in _ABSOLUTE_CLAIM:
        if word in body:
            hits.append({"category": "绝对化用语", "word": word,
                         "fix": "改为客观描述，如『帮助改善』『部分人反馈』"})
    for word in _PROMISE:
        if word in body:
            hits.append({"category": "效果承诺", "word": word,
                         "fix": "删除承诺，改为『效果因人而异』"})
    return _dump({
        "ok": True,
        "status": "rework" if hits else "pass",
        "hits": hits,
        "rule_count": len(_MEDICAL_REDLINE) + len(_ABSOLUTE_CLAIM) + len(_PROMISE),
        "note": "规则审核为第一道闸；人工复核用于高风险内容。",
    })


# ---------------------------------------------------------------------------
# 5. 社群入口（形态待定）
# ---------------------------------------------------------------------------


def get_community_qr() -> str:
    """返回当前有效的社群二维码。形态（企业微信活码）后期再定。"""
    return _dump({
        "ok": True,
        "status": "pending",
        "note": "群二维码形态后期确定；届时接企业微信活码，避免 7 天失效。",
    })


# ---------------------------------------------------------------------------
# 辅助：给系统提示词注入词表
# ---------------------------------------------------------------------------


def issue_taxonomy_block() -> str:
    return "\n".join(
        f'- {key}（{meta["label"]}）' for key, meta in ISSUE_TAXONOMY.items()
    )


def style_tags_block() -> str:
    return "\n".join(
        f'- {tag.get("id")}：{tag.get("name")}（{"/".join(map(str, tag.get("aliases") or []))}）'
        for tag in load_style_tags()
    )
