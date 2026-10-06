"""AI 带妆效果图：qwen-image-edit（multimodal 同步）。

实测结论：`wanx2.1-imageedit(description_edit)` 太保守，**几乎不上妆**；
换成 `qwen-image-edit-plus` 后能真正上妆且保持人物身份。

隐私：图片走 **base64 data URL**，不传公网；结果图是远端 URL，只返回前端展示，
不下载、不落盘。日志不记录照片内容与密钥。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..config import IMAGE_EDIT, VISION_PROVIDERS

LOG = logging.getLogger("beauty_mirror.image_edit")

_MIME = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}


_ISSUE_MAKEUP = {
    "acne_marks": "局部遮瑕盖住痘印，底妆少量多次，不要厚涂",
    "redness": "泛红处用绿色系妆前/遮瑕中和，底妆选偏暖中性色号",
    "t_zone_oil": "T 区用哑光控油底妆与定妆，避开高光",
    "pores": "用修饰毛孔的妆前打底，顺着毛孔方向轻拍",
    "dark_circles": "眼下用偏橘调遮瑕提亮，少量多次避免卡纹",
}

# 结构化妆面配方：把“风格名”翻译成模型能执行的具化妆面
_RECIPE_ORDER = ("base", "brow", "eyes", "blush", "lips", "contour")
_RECIPE_LABEL = {"base": "底妆", "brow": "眉", "eyes": "眼", "blush": "腮红", "lips": "唇", "contour": "修容"}


def build_prompt(
    style_name: str,
    keywords: tuple[str, ...] = (),
    focus: tuple[str, ...] = (),
    skin_type: str = "",
    issues: tuple[str, ...] = (),
    makeup_spec: str = "",
    recipe: dict[str, str] | None = None,
) -> str:
    parts = [f"给照片中的女性化妆，风格＝「{style_name}」。"]
    if recipe:
        items = [f"{_RECIPE_LABEL[k]}—{recipe[k]}" for k in _RECIPE_ORDER if recipe.get(k)]
        if items:
            parts.append("妆面配方（严格执行）：" + "；".join(items) + "。")
    if makeup_spec:
        parts.append("参考妆容（可参考）：" + makeup_spec + "。")
    if keywords:
        parts.append("风格要点：" + "、".join(keywords) + "。")
    if focus:
        parts.append("重点步骤：" + "、".join(focus) + "。")
    if skin_type:
        parts.append(f"她的肤质：{skin_type}。")
    tips = [_ISSUE_MAKEUP[key] for key in issues if key in _ISSUE_MAKEUP]
    if tips:
        parts.append("针对性处理：" + "；".join(tips) + "。")
    parts.append(
        "要求：明显可见的妆容变化（底妆 / 眉形 / 眼影与睫毛 / 腮红 / 唇色），"
        "**只改变妆容**；严格保持她的身份、五官、脸型、背景与衣着不变，"
        "**不要改变发型与头发长度**，**必须保留并修饰眉毛（不得抹掉或变淡）**；"
        "妆容干净、边缘自然，整体自然不夸张、口红薄涂，不要过浓过艳；"
        "**保留皮肤真实纹理与毛孔，不要磨皮、不要塑料感/假面感**；"
        "写实照片风格，不换脸、不要改变年龄。"
    )
    return "".join(parts)


def _guess_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    return "image/jpeg"


def fetch_bytes(url: str, timeout: float = 15, max_bytes: int = 8_000_000) -> bytes:
    """把参考图（博主封面缩略图）拉下来；失败返回空 bytes。"""
    if not url or not url.startswith("http"):
        return b""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "beauty-mirror/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
        return data if data and len(data) <= max_bytes else b""
    except Exception as exc:  # noqa: BLE001
        LOG.warning("参考图拉取失败: %s", type(exc).__name__)
        return b""


_SPEC_PROMPT = (
    "只描述这张照片里人物的妆容：底妆质感、眉形、眼影颜色与画法、眼线、睫毛、腮红、"
    "唇色、修容。不要描述人物长相、脸型、发型、年龄、身份。用一段中文，30-80 字。"
)


def extract_makeup_spec(image_bytes: bytes) -> str:
    """用视觉模型把参考图“读”成一段**妆容描述**（只取妆，不取脸），供文字上妆用。"""
    spec = VISION_PROVIDERS.get("qwen_vl", {})
    key = (os.getenv(spec.get("api_key_env", "")) or "").strip()
    if not key or not image_bytes:
        return ""
    try:
        from openai import OpenAI  # 延迟导入

        client = OpenAI(api_key=key, base_url=spec.get("base_url"), timeout=40, max_retries=0)
        data_url = f"data:{_guess_mime(image_bytes)};base64," + base64.b64encode(image_bytes).decode("ascii")
        response = client.chat.completions.create(
            model=spec.get("model"), max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": _SPEC_PROMPT},
            ]}],
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("妆容描述提取失败: %s", type(exc).__name__)
        return ""


def _data_url(path: Path) -> str:
    mime = _MIME.get(path.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _post(url: str, body: dict, key: str, timeout: float = 150) -> Any:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _extract_image_url(data: Any) -> str:
    output = (data or {}).get("output") or {}
    for choice in output.get("choices") or []:
        content = (choice.get("message") or {}).get("content") or []
        for item in content:
            if isinstance(item, dict) and item.get("image"):
                return str(item["image"])
    match = re.search(r"https?://[^\s\"']+", json.dumps(output))
    return match.group(0) if match else ""


def generate(
    image_path: Path,
    prompt: str,
    width: int = 0,
    height: int = 0,
) -> dict[str, Any]:
    """单图 + 文字上妆（**不再回传参考人脸**，避免身份漂移）。任何失败都不抛异常。"""
    if not IMAGE_EDIT["enabled"]:
        return {"ok": False, "error": "图像生成未启用"}
    key = (os.getenv(IMAGE_EDIT["api_key_env"]) or "").strip()
    if not key:
        return {"ok": False, "error": f"未配置 {IMAGE_EDIT['api_key_env']}"}
    if max(width, height) and max(width, height) < IMAGE_EDIT["min_side"]:
        return {"ok": False, "error": f"照片太小（{width}×{height}），生成需长边 ≥ {IMAGE_EDIT['min_side']}，请重拍后再试"}

    content = [{"image": _data_url(image_path)}, {"text": prompt}]
    body = {"model": IMAGE_EDIT["model"], "input": {"messages": [{"role": "user", "content": content}]}}
    try:
        data = _post(IMAGE_EDIT["endpoint"], body, key, timeout=float(IMAGE_EDIT["timeout_seconds"]))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        LOG.warning("image-edit 调用失败 HTTP %s", exc.code)
        return {"ok": False, "error": f"调用失败 HTTP {exc.code}: {detail}"}
    except Exception as exc:  # noqa: BLE001
        LOG.warning("image-edit 调用失败: %s", type(exc).__name__)
        return {"ok": False, "error": f"调用失败 {type(exc).__name__}: {exc}"}

    image_url = _extract_image_url(data)
    if not image_url:
        return {"ok": False, "error": "模型未返回图片（可能被内容审核拦截或指令未生效）"}
    return {"ok": True, "image_url": image_url}


def generate_many(image_path: Path, prompt: str, n: int = 3, width: int = 0, height: int = 0) -> list[dict[str, Any]]:
    """并发生成 n 张候选（一次出多张，让用户挑）。"""
    n = max(1, min(int(n), 4))
    if n == 1:
        return [generate(image_path, prompt, width, height)]
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(generate, image_path, prompt, width, height) for _ in range(n)]
        return [f.result() for f in futures]


_SCORE_PROMPT = (
    "你是美妆评审。第 1 张是原图，后面是同一人化「{style}」妆的若干候选图。\n"
    "请对每张候选图打分（0-10），三个维度：\n"
    "- style：像不像「{style}」这个风格\n"
    "- identity：是否还是同一个人（未换脸、未改发型长度）\n"
    "- natural：是否自然、不假面\n"
    "只输出 JSON，不要解释：{{\"candidates\":[{{\"index\":0,\"style\":8,\"identity\":9,\"natural\":7,\"note\":\"一句短评\"}}]}}"
)


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        loaded = json.loads(cleaned)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.S)
    if match:
        try:
            loaded = json.loads(match.group(0))
            return loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def score_candidates(original_path: Path, candidate_urls: list[str], style_name: str) -> list[dict[str, Any]]:
    """用一个视觉模型当“评委”给候选图打分（初筛，不是最终结论）。失败返回空列表。"""
    spec = VISION_PROVIDERS.get("qwen_vl", {})
    key = (os.getenv(spec.get("api_key_env", "")) or "").strip()
    if not key or not candidate_urls:
        return []
    try:
        from openai import OpenAI  # 延迟导入

        content: list[dict[str, Any]] = [{"type": "image_url", "image_url": {"url": _data_url(original_path)}}]
        for url in candidate_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        content.append({"type": "text", "text": _SCORE_PROMPT.format(style=style_name)})
        client = OpenAI(api_key=key, base_url=spec.get("base_url"), timeout=60, max_retries=0)
        response = client.chat.completions.create(
            model=spec.get("model"), max_tokens=600,
            messages=[{"role": "user", "content": content}],
        )
        data = _extract_json(response.choices[0].message.content or "")
        candidates = (data or {}).get("candidates")
        return [c for c in candidates if isinstance(c, dict)] if isinstance(candidates, list) else []
    except Exception as exc:  # noqa: BLE001 - 评分失败不影响出图
        LOG.warning("候选图评分失败: %s", type(exc).__name__)
        return []
