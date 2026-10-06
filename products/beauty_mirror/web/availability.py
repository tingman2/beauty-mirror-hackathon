"""运行时可用性探测：视觉段 / 第三方内容(Apify) / 内容缓存。

原则：
  - **绝不打印或返回任何密钥**；只回答"是否已配置"与账号级公开信息（用户名 / 套餐）。
  - Apify 远程探测只调 `GET /v2/users/me`（**不计费、不跑 Actor**），结果带 TTL 缓存。
  - 任何网络异常都不抛出，转成结构化状态，避免把首页搞崩。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..config import APIFY, CONTENT, DEMO, VISION, VISION_PROVIDERS

LOG = logging.getLogger("beauty_mirror.web.availability")

_APIFY_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_APIFY_TTL_SECONDS = 300.0


def vision_status() -> dict[str, Any]:
    """视觉段是否可真实调用（只看配置，不发请求、不花钱）。"""
    provider = VISION["provider"]
    spec = VISION_PROVIDERS.get(provider, {})
    key_env = str(spec.get("api_key_env") or "")
    key_configured = (not key_env) or bool((os.getenv(key_env) or "").strip())
    is_mock = provider == "mock" or DEMO["offline"]
    ready = bool(spec.get("model")) and key_configured and not is_mock
    return {
        "provider": provider,
        "label": spec.get("label", provider),
        "model": spec.get("model", ""),
        "key_configured": key_configured,
        "ready": ready,
        "mode": "mock" if is_mock else "live",
    }


def apify_status(check_remote: bool = False, timeout: float = 8.0) -> dict[str, Any]:
    """Apify 是否可用。check_remote=True 时调 users/me 校验（不计费，带 5 分钟缓存）。"""
    token = APIFY["token"]
    base: dict[str, Any] = {
        "token_configured": bool(token),
        "actor_id": APIFY["actor_id"],
        "username": None,
        "plan": None,
        "plan_name": None,
        "max_monthly_usage_usd": None,
        "remote_checked": False,
        "usable": False,
        "preview_limited": False,
        "reason": "",
    }
    if not token:
        base["reason"] = "未配置 APIFY_TOKEN，内容将使用本地缓存/预置示例"
        return base

    if not check_remote:
        base["usable"] = True
        base["reason"] = "凭证已配置（未做远程校验）"
        return base

    now = time.time()
    cached = _APIFY_CACHE.get("data")
    if cached and now - float(_APIFY_CACHE["at"]) < _APIFY_TTL_SECONDS:
        return dict(cached)

    url = f"https://api.apify.com/v2/users/me?token={urllib.parse.quote(token)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace")).get("data", {}) or {}
        plan = data.get("plan") or {}
        plan_id = str(plan.get("id") or plan.get("name") or "")
        base.update({
            "username": data.get("username"),
            "plan": plan_id or None,
            "plan_name": plan.get("name"),
            "max_monthly_usage_usd": plan.get("maxMonthlyUsageUsd"),
            "remote_checked": True,
            "usable": True,
            "reason": "凭证有效",
        })
        if plan_id.upper() == "FREE":
            # 实测：FREE 计划两个小红书 actor 都带预览阀，抓少量后返回 0 条
            base["preview_limited"] = True
            base["reason"] = "凭证有效，但账号为 FREE 预览额度，无法全量刷新（内容走缓存/示例）"
    except urllib.error.HTTPError as exc:
        base.update({"remote_checked": True, "usable": False,
                     "reason": f"凭证校验失败：HTTP {exc.code}"})
    except Exception as exc:  # noqa: BLE001 - 探测失败不能影响主流程
        base.update({"remote_checked": True, "usable": False,
                     "reason": f"凭证校验失败：{type(exc).__name__}"})

    _APIFY_CACHE["at"] = now
    _APIFY_CACHE["data"] = base
    return dict(base)


def cache_status() -> dict[str, Any]:
    """本地内容缓存：哪些风格有真实卡片、是否已按方案 B 清除文案。"""
    try:
        from ..providers.content_api import load_cache

        cache = load_cache()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("读取内容缓存失败：%s", exc)
        return {"styles": {}, "total_cards": 0, "text_stripped": False}

    by_style = cache.get("by_style", {}) or {}
    styles: dict[str, int] = {}
    total = 0
    for name, entry in by_style.items():
        count = len((entry or {}).get("cards") or [])
        styles[str(name)] = count
        total += count
    return {
        "path": str(CONTENT["cache_path"]),
        "styles": styles,
        "total_cards": total,
        "text_stripped": bool(cache.get("text_stripped")),
        "ttl_hours": CONTENT["cache_ttl_hours"],
    }


def collect_status(check_apify: bool = True) -> dict[str, Any]:
    """汇总三块可用性，给首页状态卡与日志用。"""
    return {
        "ok": True,
        "generated_at": int(time.time()),
        "offline": bool(DEMO["offline"]),
        "vision": vision_status(),
        "apify": apify_status(check_remote=check_apify),
        "cache": cache_status(),
    }
