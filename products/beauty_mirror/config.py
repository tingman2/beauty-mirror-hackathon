"""集中配置：env 优先，全部密钥外置，不写死。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PKG_DIR = Path(__file__).resolve().parent
DATA_DIR = PKG_DIR / "data"

load_dotenv(PKG_DIR / ".env", override=True)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


# --- 文本段（复用 scaffold 的 Anthropic-compatible 通道）--------------------
TEXT = {
    "model": _env("MODEL_ID", "claude-sonnet-4-6"),
    "base_url": _env("ANTHROPIC_BASE_URL") or None,
    "api_key_env": "ANTHROPIC_API_KEY",
    "max_tokens": _env_int("MAX_TOKENS", 4000),
    "timeout_seconds": _env_float("TIMEOUT_SECONDS", 60.0),
    "max_retries": _env_int("MAX_RETRIES", 2),
}

# --- 视觉段：三家候选，统一 OpenAI 兼容协议 --------------------------------
# 只需换 base_url / key / model，adapter 一套代码。
VISION_PROVIDERS: dict[str, dict] = {
    "qwen_vl": {
        "label": "Qwen-VL（阿里云百炼）",
        "base_url": _env("QWEN_VL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "api_key_env": "DASHSCOPE_API_KEY",
        "model": _env("QWEN_VL_MODEL", "qwen-vl-max"),
    },
    "doubao_vision": {
        "label": "豆包 Vision（火山方舟）",
        "base_url": _env("DOUBAO_VISION_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        "api_key_env": "ARK_API_KEY",
        # 方舟通常填推理接入点 ID（ep-xxxx）或模型名
        "model": _env("DOUBAO_VISION_MODEL", ""),
    },
    "glm_4v": {
        "label": "GLM-4V（智谱）",
        "base_url": _env("GLM_4V_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        "api_key_env": "ZHIPU_API_KEY",
        "model": _env("GLM_4V_MODEL", "glm-4v-plus"),
    },
    "mock": {
        "label": "Mock（离线，仅用于测试/演示兜底）",
        "base_url": "",
        "api_key_env": "",
        "model": "mock-vision",
    },
}

VISION = {
    "provider": _env("VISION_PROVIDER", "qwen_vl"),
    "timeout_seconds": _env_float("VISION_TIMEOUT_SECONDS", 20.0),
    "max_tokens": _env_int("VISION_MAX_TOKENS", 1000),
    "min_confidence": _env_float("VISION_MIN_CONFIDENCE", 0.35),
    # SDK 自带重试会偷偷把超时乘几倍（实测 timeout=30s 实际放到 91s），必须关掉
    "sdk_max_retries": _env_int("VISION_SDK_MAX_RETRIES", 0),
    # 视觉预热等待上限：超过就降级返回，绝不让 Web 请求无限阻塞
    "prefetch_wait_seconds": _env_float("VISION_PREFETCH_WAIT_SECONDS", 45.0),
    # 单次分析的重试次数（>1 才生效）。默认 1 = 不重试：重试会把超时放大（PRD 第五轮结论）
    "max_attempts": _env_int("VISION_MAX_ATTEMPTS", 1),
}

# --- 第三方内容 API（文档待接入；先留接口 + 兜底）--------------------------
CONTENT_API = {
    "base_url": _env("CONTENT_API_BASE_URL"),
    "api_key": _env("CONTENT_API_KEY"),
    "api_key_header": _env("CONTENT_API_KEY_HEADER", "Authorization"),
    "api_key_prefix": _env("CONTENT_API_KEY_PREFIX", "Bearer "),
    # demo 防卡死：单次超时压到 3s，重试 1 次，总预算 ~4-5s
    "timeout_seconds": _env_float("CONTENT_API_TIMEOUT_SECONDS", 3.0),
    "max_retries": _env_int("CONTENT_API_MAX_RETRIES", 1),
    # 第三方不稳定 → 一律降级到本地缓存/预置，绝不让前端看到报错
    "fallback_to_mock": _env("CONTENT_API_FALLBACK", "true").lower() != "false",
    "page_size": _env_int("CONTENT_API_PAGE_SIZE", 6),
}

# --- Apify（实际选用的第三方抓取平台）------------------------------------
# 重要：Actor 是秒级~分钟级重操作，**不在用户同步链路里调**；
#      只用 tools/refresh_content_cache.py 预热缓存。
APIFY = {
    "token": _env("APIFY_TOKEN"),
    # 小红书：socialdatax~socialdatax-xhs-data-api（operation=search_notes）
    "actor_id": _env("APIFY_ACTOR_ID", "socialdatax~socialdatax-xhs-data-api"),
    # 抖音（如需接入再启用）
    "actor_id_douyin": _env("APIFY_ACTOR_DOUYIN", "zen-studio~douyin-profile-scraper"),
    "timeout_seconds": _env_int("APIFY_TIMEOUT_SECONDS", 120),
    "memory_mb": _env_int("APIFY_MEMORY_MB", 1024),
    "max_items": _env_int("APIFY_MAX_ITEMS", 20),
    "price_per_item_usd": _env_float("APIFY_PRICE_PER_ITEM_USD", 0.00499),
}

# --- 图像生成：AI 带妆效果图 -------------------------------
# 实测：wanx2.1-imageedit(description_edit) 几乎不上妆；
#      qwen-image-edit-plus（multimodal 同步）能真上妆且保身份。
# 隐私：图片走 base64，**不传公网**。
IMAGE_EDIT = {
    "enabled": _env("IMAGE_EDIT_ENABLED", "true").lower() != "false",
    "model": _env("IMAGE_EDIT_MODEL", "qwen-image-edit-plus"),
    "endpoint": _env(
        "IMAGE_EDIT_ENDPOINT",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    ),
    "api_key_env": "DASHSCOPE_API_KEY",
    "timeout_seconds": _env_float("IMAGE_EDIT_TIMEOUT_SECONDS", 150.0),
    # 编辑模型要求输入图长边 ≥ 512
    "min_side": _env_int("IMAGE_EDIT_MIN_SIDE", 512),
}

# --- 内容缓存（真实内容的离线副本，demo 主力）----------------------------
CONTENT = {
    "cache_path": DATA_DIR / "content_cache.json",
    # 默认关：用户链路只读缓存；预热时才打开
    "live": _env("CONTENT_LIVE", "false").lower() == "true",
    "cache_ttl_hours": _env_int("CONTENT_CACHE_TTL_HOURS", 168),
}

DEMO = {
    # 全链路离线演示：视觉与内容都走 mock，保证现场不白屏
    "offline": _env("DEMO_OFFLINE", "false").lower() == "true",
}
