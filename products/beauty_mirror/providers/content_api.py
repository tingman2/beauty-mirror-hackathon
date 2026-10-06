"""内容链路：Apify（真实抓取） → 本地缓存 → 预置兜底。

架构结论（依据 https://docs.apify.com/api/v2）：
  Actor 运行是**秒级到分钟级**的重操作（同步接口上限 300s），**不能放进用户同步链路**。
  所以用户流程只读缓存；真实抓取由 `tools/refresh_content_cache.py` 预热。

降级顺序（永远不会空手而归）：
    1. 缓存（新鲜）      ← demo 主力，真实博主内容
    2. 实时抓取（可选）  ← CONTENT_LIVE=true 时；超时长、有成本
    3. 缓存（过期）      ← 宁可旧不可无
    4. data/mock_creators.json  ← 最后兜底

多 actor 适配：不同 actor 的入参与字段完全不同，全部收敛到 ActorDialect。
换供应商只加一个 dialect，业务代码零改动。
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import APIFY, CONTENT, CONTENT_API, DEMO
from ..domain import MOCK_CREATORS_PATH
from .apify_client import run_actor_sync

LOG = logging.getLogger("beauty_mirror.content")


@dataclass
class ContentFetchResult:
    cards: list[dict[str, Any]] = field(default_factory=list)
    source: str = "mock"  # api | cache | cache_stale | mock | empty
    degraded: bool = True
    error: str = ""
    latency_ms: int = 0
    cost_usd_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cards": self.cards,
            "source": self.source,
            "degraded": self.degraded,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "count": len(self.cards),
            "cost_usd_estimate": round(self.cost_usd_estimate, 4),
        }


# ---------------------------------------------------------------------------
# 多 actor 适配：每个 actor 一份方言（入参构造 + 字段映射 + 计费）
# ---------------------------------------------------------------------------


@dataclass
class ActorDialect:
    actor_id: str
    platform: str
    build_input: Callable[[str, int], dict]
    field_map: dict[str, tuple[str, ...]]
    raw_keep: dict[str, tuple[str, ...]] = field(default_factory=dict)
    price_per_item_usd: float = 0.00499


# --- 方言 1：SocialDataX（operation=search_notes）--------------------------
# 实测字段：note_id / note_url / title / summary / cover_image_url
#          author_name / author_user_id / author_avatar_url / like_count ...
_SOCIALDATAX_MAP = {
    "id": ("note_id", "id"),
    "platform": ("platform",),
    "creator": ("author_name", "author.nickname", "nickname"),
    "creator_id": ("author_user_id", "author.userid"),
    "creator_avatar": ("author_avatar_url", "author.avatar", "avatar"),
    "creator_profile": ("author_profile_url", "profile_url"),
    "title": ("title", "display_title"),
    "summary": ("summary", "desc", "content"),
    "cover_url": ("cover_image_url", "cover_url", "images.0.url", "images.0"),
    "original_url": ("note_url", "url", "share_url"),
    "style_tags": ("style_tags", "tags", "hashtags"),
}

# --- 方言 2：zen-studio（keywords 数组）已验证可用 -------------------------
# 实测字段：id / title / desc / url / images[].url / author{userid,nickname,avatar}
#          engagement{liked_count,collected_count,comments_count,shared_count}
_ZEN_MAP = {
    "id": ("id", "note_id"),
    "platform": ("platform",),
    "creator": ("author.nickname", "author_name", "nickname"),
    "creator_id": ("author.userid", "author_user_id"),
    "creator_avatar": ("author.avatar", "author_avatar_url"),
    "creator_profile": ("author.profile_url",),
    "title": ("title", "display_title"),
    "summary": ("desc", "summary", "content"),
    "cover_url": ("images.0.url", "images.0.url_large", "cover_image_url", "cover_url"),
    "original_url": ("url", "note_url", "share_url"),
    "style_tags": ("tag_info.title", "style_tags", "tags"),
}

_ZEN_RAW = {
    "note_type": ("type", "note_type"),
    "publish_time": ("timestamp", "publish_time"),
    "like_count": ("engagement.liked_count", "like_count"),
    "collect_count": ("engagement.collected_count", "collect_count"),
    "comment_count": ("engagement.comments_count", "comment_count"),
    "share_count": ("engagement.shared_count", "share_count"),
}

_SOCIALDATAX_RAW = {
    "note_type": ("note_type",),
    "publish_time": ("publish_time",),
    "like_count": ("like_count",),
    "collect_count": ("collect_count",),
    "comment_count": ("comment_count",),
    "share_count": ("share_count",),
    "video_duration_ms": ("video_duration_ms",),
}

DIALECTS: dict[str, ActorDialect] = {
    "zen-studio~rednote-search-scraper": ActorDialect(
        actor_id="zen-studio~rednote-search-scraper",
        platform="xiaohongshu",
        build_input=lambda kw, limit: {
            "keywords": [kw],
            "maxResults": int(limit),
            "sortType": "general",
            "noteType": "all",
            "timeFilter": "all",
        },
        field_map=_ZEN_MAP,
        raw_keep=_ZEN_RAW,
    ),
    "socialdatax~socialdatax-xhs-data-api": ActorDialect(
        actor_id="socialdatax~socialdatax-xhs-data-api",
        platform="xiaohongshu",
        build_input=lambda kw, limit: {
            "operation": "search_notes",
            "keyword": kw,
            "sort_type": "like_count_descending",  # 取代表性内容，而非最新
            "note_type": "all",
            "publish_time_range": "all",
            "max_items": int(limit),
            "auto_paginate": True,
        },
        field_map=_SOCIALDATAX_MAP,
        raw_keep=_SOCIALDATAX_RAW,
    ),
}

DEFAULT_ACTOR = "zen-studio~rednote-search-scraper"


def get_dialect(actor_id: str | None = None) -> ActorDialect:
    """取方言；未注册的 actor 用 zen 的字段映射兜底（字段名尽量通用）。"""
    key = (actor_id or APIFY["actor_id"] or DEFAULT_ACTOR).strip()
    if key in DIALECTS:
        return DIALECTS[key]
    LOG.warning("未注册的 Apify actor %s，使用通用字段映射兜底", key)
    return ActorDialect(
        actor_id=key,
        platform="xiaohongshu",
        build_input=lambda kw, limit: {
            "keywords": [kw],
            "keyword": kw,
            "maxResults": int(limit),
            "max_items": int(limit),
        },
        field_map=_ZEN_MAP,
        raw_keep=_ZEN_RAW,
    )


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


def _pick(raw: dict[str, Any], internal: str, field_map: dict, default: Any = "") -> Any:
    """按候选字段名（支持点号路径）取值。"""
    for candidate in field_map.get(internal, ()):
        node: Any = raw
        ok = True
        for part in candidate.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and len(node) > int(part):
                node = node[int(part)]
            else:
                ok = False
                break
        if ok and node not in (None, "", [], {}):
            return node
    return default


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if isinstance(v, dict):
                out.append(str(v.get("name") or v.get("title") or "").strip())
            else:
                out.append(str(v))
        return [x for x in out if x]
    if isinstance(value, str) and value:
        return [p.strip() for p in value.replace("#", ",").split(",") if p.strip()]
    return []


def _dedupe_tags(*groups: list[str]) -> list[str]:
    seen: list[str] = []
    lowered: set[str] = set()
    for group in groups:
        for tag in group:
            key = tag.strip().lower()
            if key and key not in lowered:
                lowered.add(key)
                seen.append(tag.strip())
    return seen[:5]


def _hashtags(text: str) -> list[str]:
    """从小红书文案里抽出 #话题 作为风格标签。"""
    if not text:
        return []
    return [t.strip() for t in re.findall(r"#([^#\s]{1,20})", text) if t.strip()]


def normalize_item(
    raw: dict[str, Any],
    style_name: str,
    dialect: ActorDialect,
) -> dict[str, Any] | None:
    """把一条上游数据洗成前端卡片；信息太少就丢弃。"""
    if not isinstance(raw, dict):
        return None
    fm = dialect.field_map
    title = str(_pick(raw, "title", fm)).strip()
    summary = str(_pick(raw, "summary", fm)).strip()
    original_url = str(_pick(raw, "original_url", fm)).strip()
    if not (title or summary or original_url):
        return None

    item_id = str(_pick(raw, "id", fm) or abs(hash(f"{title}{original_url}")) % 10**10)
    extra = {
        key: _pick(raw, key, dialect.raw_keep, None)
        for key in dialect.raw_keep
    }
    return {
        "id": item_id,
        "platform": str(_pick(raw, "platform", fm) or dialect.platform),
        "creator": str(_pick(raw, "creator", fm) or "未知博主"),
        "creator_id": str(_pick(raw, "creator_id", fm)),
        "creator_avatar": str(_pick(raw, "creator_avatar", fm)),
        "creator_profile": str(_pick(raw, "creator_profile", fm)),
        "title": title or summary[:40],
        "summary": summary[:280],
        "cover_url": str(_pick(raw, "cover_url", fm)),
        "original_url": original_url,
        "style_tags": _dedupe_tags([style_name], _hashtags(summary), _as_list(_pick(raw, "style_tags", fm))),
        "tutorials": raw.get("tutorials") or [],
        "raw_extra": {k: v for k, v in extra.items() if v is not None},
        "source": "api",
    }


def normalize_items(
    raw_items: list[dict[str, Any]], style_name: str, dialect: ActorDialect
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_items:
        card = normalize_item(item, style_name, dialect)
        if card and card["id"] not in seen_ids:
            seen_ids.add(card["id"])
            out.append(card)
    return out


# ---------------------------------------------------------------------------
# 上游调用
# ---------------------------------------------------------------------------


def build_apify_input(style_name: str, limit: int, actor_id: str | None = None) -> dict[str, Any]:
    """按 dialect 构造 Actor 入参（不同 actor 入参完全不同）。"""
    return get_dialect(actor_id).build_input(style_name, limit)


def _call_apify(style_name: str, limit: int, actor_id: str | None = None):
    if not APIFY["token"]:
        raise RuntimeError("缺少 APIFY_TOKEN")
    dialect = get_dialect(actor_id)
    result = run_actor_sync(
        dialect.actor_id,
        dialect.build_input(style_name, limit),
        APIFY["token"],
        timeout_seconds=APIFY["timeout_seconds"],
        memory_mb=APIFY["memory_mb"],
        price_per_item_usd=APIFY["price_per_item_usd"],
    )
    if not result.ok:
        raise RuntimeError(f"Apify 失败: {result.error}")
    cards = normalize_items(result.items, style_name, dialect)
    return cards, result.cost_usd_estimate, result.status


def _call_generic(style_name: str, platform: str | None, limit: int) -> list[dict[str, Any]]:
    """兼容非 Apify 的通用 JSON 接口（保留，便于换供应商）。"""
    query = urllib.parse.urlencode(
        {"style_tag": style_name, **({"platform": platform} if platform else {}), "limit": limit}
    )
    url = f"{CONTENT_API['base_url'].rstrip('/')}/creators/by_style?{query}"
    request = urllib.request.Request(url, method="GET")
    if CONTENT_API["api_key"]:
        request.add_header(
            CONTENT_API["api_key_header"],
            f"{CONTENT_API['api_key_prefix']}{CONTENT_API['api_key']}",
        )
    request.add_header("Accept", "application/json")
    with urllib.request.urlopen(request, timeout=CONTENT_API["timeout_seconds"]) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    for key in ("data", "items", "list", "results", "notes"):
        if isinstance(payload.get(key), list):
            return [x for x in payload[key] if isinstance(x, dict)]
    return []


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------


def _empty_cache() -> dict[str, Any]:
    return {"_note": "真实抓取内容的离线副本；由 scripts/refresh_content_cache.py 生成", "by_style": {}}


def load_cache() -> dict[str, Any]:
    try:
        with Path(CONTENT["cache_path"]).open(encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("by_style"), dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        LOG.warning("内容缓存读取失败: %s", exc)
    return _empty_cache()


def save_cache(cache: dict[str, Any]) -> None:
    path = Path(CONTENT["cache_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def write_style_to_cache(style_name: str, cards: list[dict[str, Any]], source: str = "apify") -> None:
    cache = load_cache()
    cache["by_style"][style_name] = {
        "updated_at": int(time.time()),
        "source": source,
        "cards": cards,
    }
    save_cache(cache)


def _read_cached(style_name: str, limit: int) -> tuple[list[dict[str, Any]], bool, float]:
    """返回 (cards, is_fresh, age_hours)。"""
    entry = load_cache().get("by_style", {}).get(style_name)
    if not entry:
        return [], False, 0.0
    age_hours = (time.time() - float(entry.get("updated_at", 0))) / 3600
    fresh = age_hours <= CONTENT["cache_ttl_hours"]
    return list(entry.get("cards") or [])[:limit], fresh, age_hours


# ---------------------------------------------------------------------------
# 兜底
# ---------------------------------------------------------------------------


def _load_mock_cards() -> list[dict[str, Any]]:
    try:
        with Path(MOCK_CREATORS_PATH).open(encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("预置数据读取失败: %s", exc)
        return []
    items = data.get("cards") if isinstance(data, dict) else data
    cards = [c for c in (items or []) if isinstance(c, dict)]
    for card in cards:
        card["source"] = "mock"
    return cards


def _mock_for_style(style_name: str, limit: int) -> list[dict[str, Any]]:
    cards = _load_mock_cards()
    if not cards:
        return []
    query = (style_name or "").strip().lower()
    matched = [
        card
        for card in cards
        if any(query and query in str(tag).lower() for tag in (card.get("style_tags") or []))
        or query in str(card.get("title", "")).lower()
    ]
    return (matched or cards)[:limit]


# ---------------------------------------------------------------------------
# 对外入口：永远返回结果，永远不抛
# ---------------------------------------------------------------------------


def fetch_creator_cards(
    style_tag: str,
    platform: str | None = None,
    limit: int | None = None,
) -> ContentFetchResult:
    limit = int(limit or CONTENT_API["page_size"])
    started = time.time()
    style_name = (style_tag or "").strip()

    # 1) 缓存优先
    cached, fresh, age_hours = _read_cached(style_name, limit)
    if cached and fresh and not CONTENT["live"]:
        return ContentFetchResult(
            cards=cached,
            source="cache",
            degraded=False,
            latency_ms=int((time.time() - started) * 1000),
        )

    # 2) 实时抓取：**只有 CONTENT_LIVE=true 才会发请求**
    #    默认关：缓存未命中直接走兜底（快、零成本、测试不打网络）
    has_upstream = bool(APIFY["token"] or CONTENT_API["base_url"])
    live_enabled = CONTENT["live"] and has_upstream
    if live_enabled and not cached and not DEMO["offline"]:
        try:
            if APIFY["token"]:
                cards, cost, _status = _call_apify(style_name, limit)
            else:
                raw_items = _call_generic(style_name, platform, limit)
                cards = normalize_items(raw_items, style_name, get_dialect())
                cost = 0.0
            if cards:
                write_style_to_cache(style_name, cards)
                return ContentFetchResult(
                    cards=cards[:limit],
                    source="api",
                    degraded=False,
                    latency_ms=int((time.time() - started) * 1000),
                    cost_usd_estimate=cost,
                )
        except Exception as exc:  # noqa: BLE001 - 任何异常都降级，不能冒泡
            LOG.warning("内容实时抓取失败，走降级：%s", exc)

    # 3) 过期缓存（宁可旧不可无）
    if cached:
        return ContentFetchResult(
            cards=cached,
            source="cache_stale",
            degraded=True,
            error=f"缓存已过期 {age_hours:.1f}h，实时抓取不可用",
            latency_ms=int((time.time() - started) * 1000),
        )

    # 4) 预置兜底
    if not CONTENT_API["fallback_to_mock"]:
        return ContentFetchResult(
            source="empty",
            degraded=True,
            error="无缓存且实时抓取不可用",
            latency_ms=int((time.time() - started) * 1000),
        )
    cards = _mock_for_style(style_name, limit)
    return ContentFetchResult(
        cards=cards,
        source="mock",
        degraded=True,
        error="无可用缓存，已使用预置示例内容",
        latency_ms=int((time.time() - started) * 1000),
    )
