"""视觉段：只做「可见问题检测」，不做肤质分类。

三家候选（Qwen-VL / 豆包 Vision / GLM-4V）的 HTTP 协议都兼容 OpenAI
chat.completions，因此共用一套 adapter，只换 base_url / key / model。

设计要点：
  - 闭集输出：只能是 ISSUE_KEYS 里的 5 个 key，其他一律丢弃；
  - 失败不抛异常：返回 degraded 结果，让上层静默降级；
  - mock provider 用于离线测试与 demo 兜底（不依赖任何网络与密钥）。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import VISION, VISION_PROVIDERS
from ..domain import ISSUE_KEYS, taxonomy_prompt_block

LOG = logging.getLogger("beauty_mirror.vision")

_MIME = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

VISION_INSTRUCTION = """你是皮肤图像分析助手。只做一件事：检测照片中**肉眼可见的皮肤问题**。

可检测的问题（闭集，只能用这 5 个 key）：
{taxonomy}

严格要求：
1. 只输出 JSON，不要任何解释、不要 markdown 代码块。
2. 不是这 5 个 key 的问题一律不要输出。
3. 不要判断肤质类型（干/油/混/敏），不要给结论或建议，不要诊断疾病。
4. confidence 是你对该问题确实存在的把握，0~1 的小数；低于 0.3 的不要输出。
5. 如果图片太暗、太糊、人脸太小或有重滤镜，image_quality.ok 置 false 并说明。

输出 JSON：
{{
  "image_quality": {{"ok": true, "issues": []}},
  "detected": [
    {{"type": "acne_marks", "confidence": 0.72, "severity": "mild", "areas": ["左脸颊"]}}
  ]
}}

severity 只能取 mild / moderate / severe。没有问题就返回空数组。
"""


def build_prompt() -> str:
    return VISION_INSTRUCTION.format(taxonomy=taxonomy_prompt_block())


@dataclass
class VisionResult:
    provider: str
    image_quality: dict[str, Any] = field(default_factory=lambda: {"ok": True, "issues": []})
    detected: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    degraded: bool = False
    error: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "image_quality": self.image_quality,
            "detected": self.detected,
            "latency_ms": self.latency_ms,
            "degraded": self.degraded,
            "error": self.error,
        }

    @property
    def issue_keys(self) -> list[str]:
        return [item["type"] for item in self.detected]


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个 JSON 对象（容忍代码块与前后废话）。"""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        loaded = json.loads(cleaned)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for idx in range(start, len(cleaned)):
            char = cleaned[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        loaded = json.loads(cleaned[start : idx + 1])
                        if isinstance(loaded, dict):
                            return loaded
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("{", start + 1)
    return None


def _normalize(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    """闭集校验 + 置信度过滤 + 排序。任何脏数据在这里被清掉。"""
    quality = payload.get("image_quality")
    if not isinstance(quality, dict):
        quality = {"ok": True, "issues": []}

    detected: list[dict[str, Any]] = []
    for item in payload.get("detected") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("type", "")).strip()
        if key not in ISSUE_KEYS:  # 闭集：模型自由发挥的分类直接丢弃
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        confidence = max(0.0, min(1.0, confidence))
        if confidence < VISION["min_confidence"]:
            continue
        detected.append(
            {
                "type": key,
                "confidence": round(confidence, 3),
                "severity": str(item.get("severity", "mild")),
                "areas": [str(a) for a in (item.get("areas") or [])][:4],
            }
        )

    detected.sort(key=lambda x: x["confidence"], reverse=True)
    return {"image_quality": quality, "detected": detected}


def _encode_image(image_path: Path) -> str:
    mime = _MIME.get(image_path.suffix.lower(), "image/jpeg")
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


class OpenAICompatibleVisionProvider:
    """Qwen-VL / 豆包 Vision / GLM-4V 共用实现。"""

    def __init__(self, name: str, spec: dict[str, Any]) -> None:
        self.name = name
        self.label = spec.get("label", name)
        self.base_url = spec.get("base_url", "")
        self.model = spec.get("model", "")
        self.api_key_env = spec.get("api_key_env", "")

    def _api_key(self) -> str:
        import os

        return (os.getenv(self.api_key_env, "") or "").strip()

    def analyze(self, image_path: Path) -> VisionResult:
        started = time.time()
        if not self.model:
            return VisionResult(
                provider=self.name,
                degraded=True,
                error=f"{self.name}: 未配置模型（见 .env 的 MODEL/接入点 ID）",
            )
        api_key = self._api_key()
        if not api_key:
            return VisionResult(
                provider=self.name,
                degraded=True,
                error=f"{self.name}: 缺少环境变量 {self.api_key_env}",
            )
        if not image_path.exists():
            return VisionResult(
                provider=self.name, degraded=True, error=f"图片不存在: {image_path}"
            )

        image_data_url = _encode_image(image_path)
        attempts = max(1, int(VISION.get("max_attempts", 1)))
        text = ""
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                from openai import OpenAI  # 延迟导入：mock/离线场景不需要该依赖

                client = OpenAI(
                    api_key=api_key,
                    base_url=self.base_url,
                    timeout=VISION["timeout_seconds"],
                    max_retries=VISION["sdk_max_retries"],
                )
                response = client.chat.completions.create(
                    model=self.model,
                    max_tokens=VISION["max_tokens"],
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": build_prompt()},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_data_url},
                                },
                            ],
                        }
                    ],
                )
                text = response.choices[0].message.content or ""
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - 失败必须降级，不能冒泡
                last_error = exc
                if attempt + 1 < attempts:
                    delay = 0.8 * (attempt + 1)
                    LOG.warning(
                        "vision provider %s 第 %d/%d 次失败：%s；%.1fs 后重试",
                        self.name, attempt + 1, attempts, exc, delay,
                    )
                    time.sleep(delay)
        if last_error is not None:
            LOG.warning("vision provider %s failed: %s", self.name, last_error)
            return VisionResult(
                provider=self.name,
                latency_ms=int((time.time() - started) * 1000),
                degraded=True,
                error=f"{type(last_error).__name__}: {last_error}",
            )

        payload = _extract_json(text)
        if payload is None:
            return VisionResult(
                provider=self.name,
                latency_ms=int((time.time() - started) * 1000),
                degraded=True,
                error="模型未返回可解析的 JSON",
                raw_text=text[:500],
            )

        normalized = _normalize(payload, self.name)
        return VisionResult(
            provider=self.name,
            image_quality=normalized["image_quality"],
            detected=normalized["detected"],
            latency_ms=int((time.time() - started) * 1000),
            raw_text=text[:500],
        )


class MockVisionProvider:
    """离线 provider：优先读 `<图片>.mock.json` 旁路文件，否则返回通用结果。

    用途：①评测框架的 smoke test；②第三方/视觉服务全挂时的 demo 兜底。
    """

    name = "mock"
    label = "Mock（离线）"

    def analyze(self, image_path: Path) -> VisionResult:
        started = time.time()
        sidecar = image_path.with_suffix(image_path.suffix + ".mock.json")
        payload: dict[str, Any] | None = None
        if sidecar.exists():
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                LOG.warning("bad mock sidecar %s: %s", sidecar, exc)

        if payload is None:
            # 通用兜底：给一个保守的"会议光线下也大概率成立"的结果
            payload = {
                "image_quality": {"ok": True, "issues": ["mock"]},
                "detected": [
                    {"type": "t_zone_oil", "confidence": 0.6, "severity": "mild", "areas": ["T区"]},
                    {"type": "pores", "confidence": 0.5, "severity": "mild", "areas": ["鼻翼"]},
                ],
            }

        normalized = _normalize(payload, self.name)
        return VisionResult(
            provider=self.name,
            image_quality=normalized["image_quality"],
            detected=normalized["detected"],
            latency_ms=int((time.time() - started) * 1000),
            degraded=True,  # mock 永远标记为降级，避免被当成真实结论
            error="使用 mock 视觉结果（非真实检测）",
        )


def list_vision_providers() -> list[str]:
    return list(VISION_PROVIDERS.keys())


def get_vision_provider(name: str | None = None):
    key = (name or VISION["provider"] or "qwen_vl").strip()
    if key not in VISION_PROVIDERS:
        raise KeyError(
            f"未知视觉 provider: {key}；可选 {', '.join(VISION_PROVIDERS)}"
        )
    if key == "mock":
        return MockVisionProvider()
    return OpenAICompatibleVisionProvider(key, VISION_PROVIDERS[key])
