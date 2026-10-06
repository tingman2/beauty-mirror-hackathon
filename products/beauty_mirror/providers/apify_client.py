"""Apify 客户端：统一封装 Actor 调用。

为什么单独一层：Apify Actor 是**秒级到分钟级**的重操作（同步接口上限 300s），
绝不能放在用户请求的同步链路里。默认给缓存预热脚本用（`tools/refresh_content_cache.py`）。

文档依据（https://docs.apify.com/api/v2）：
  - 认证：`?token=<APIFY_TOKEN>` 或 `Authorization: Bearer`
  - 同步跑：POST /v2/acts/{actorId}/run-sync-get-dataset-items（直接返回 dataset items）
  - 限流：全局 250,000 req/min；Run Actor 400 req/s；超限返回 429
  - 429 需指数退避重试（DELAY 500ms 起，每次翻倍，随机抖动）
  - 错误体：{"error": {"type": "...", "message": "..."}}
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("beauty_mirror.apify")

API_BASE = "https://api.apify.com/v2"
SYNC_MAX_TIMEOUT = 300  # 同步接口硬上限（秒）


@dataclass
class ActorRunResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False
    status: str = ""
    error: str = ""
    latency_ms: int = 0
    cost_usd_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "items": len(self.items),
            "latency_ms": self.latency_ms,
            "cost_usd_estimate": round(self.cost_usd_estimate, 4),
            "error": self.error,
        }


class ApifyError(RuntimeError):
    pass


def _request(url: str, body: dict | None, timeout: float) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("Accept", "application/json")
    if data:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def run_actor_sync(
    actor_id: str,
    run_input: dict[str, Any],
    token: str,
    *,
    timeout_seconds: int = 120,
    memory_mb: int = 1024,
    max_retries: int = 3,
    price_per_item_usd: float = 0.00499,
    price_per_start_usd: float = 0.00005,
) -> ActorRunResult:
    """同步跑一个 Actor 并取回 dataset items。

    actor_id 形如 `username~actor-name`（注意是波浪号，不是斜杠）。
    """
    started = time.time()
    timeout_seconds = max(30, min(int(timeout_seconds), SYNC_MAX_TIMEOUT))
    query = urllib.parse.urlencode(
        {"token": token, "timeout": timeout_seconds, "memory": memory_mb}
    )
    url = f"{API_BASE}/acts/{actor_id.replace('/', '~')}/run-sync-get-dataset-items?{query}"

    delay_ms = 500
    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            payload = _request(url, run_input, timeout=timeout_seconds + 15)
            items = payload if isinstance(payload, list) else (payload or {}).get("items", [])
            items = [x for x in items if isinstance(x, dict)]
            return ActorRunResult(
                items=items,
                ok=True,
                status="SUCCEEDED",
                latency_ms=int((time.time() - started) * 1000),
                cost_usd_estimate=price_per_start_usd + price_per_item_usd * len(items),
            )
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            last_error = f"HTTP {exc.code}: {body}"
            if exc.code == 429:  # 按官方文档做指数退避 + 抖动
                wait = random.uniform(delay_ms, 2 * delay_ms) / 1000
                LOG.warning("Apify 限流(429)，%.2fs 后重试 (%d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
                delay_ms *= 2
                continue
            if exc.code >= 500:  # 服务端 5xx 同样可重试
                wait = random.uniform(delay_ms, 2 * delay_ms) / 1000
                LOG.warning("Apify 服务端 %d，%.2fs 后重试 (%d/%d)", exc.code, wait, attempt + 1, max_retries)
                time.sleep(wait)
                delay_ms *= 2
                continue
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                json.JSONDecodeError) as exc:
            # 网络抖动 / SSL 中断 / 读超时：这些都是一次性的，必须重试
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_retries:
                wait = random.uniform(delay_ms, 2 * delay_ms) / 1000
                LOG.warning("Apify 网络异常：%s，%.2fs 后重试 (%d/%d)", last_error, wait, attempt + 1, max_retries)
                time.sleep(wait)
                delay_ms *= 2
                continue
            break

    return ActorRunResult(
        ok=False,
        status="FAILED",
        error=last_error,
        latency_ms=int((time.time() - started) * 1000),
    )


def estimate_cost_usd(item_count: int, price_per_item_usd: float = 0.00499) -> float:
    """粗略成本估算（按条计费 + 起步费）。"""
    return price_per_start_usd_default() + price_per_item_usd * max(0, item_count)


def price_per_start_usd_default() -> float:
    return 0.00005
