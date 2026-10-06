"""转授权兜底：把缓存里的博主文案清掉，只保留跳转链与必要元信息。

背景：Apify 是第三方抓取，不是官方授权。若合同**没有**转授权/展示条款，
      就不能在我们的界面上展示博主文案。

用法：
    cd products
    python -m beauty_mirror.scripts.scrub_cache_text --dry-run   # 先看会清掉什么
    python -m beauty_mirror.scripts.scrub_cache_text             # 执行

保留：id / platform / creator / original_url / style_tags / cover_url
清除：title / summary（博主原创文案）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import CONTENT
from ..providers.content_api import load_cache, save_cache

# 必须清除的字段（博主原创内容）
STRIP_FIELDS = ("summary", "title", "raw_extra")
# 明确保留的字段
KEEP_FIELDS = ("id", "platform", "creator", "creator_id", "creator_avatar",
               "creator_profile", "cover_url", "original_url", "style_tags", "source")


def scrub_card(card: dict) -> dict:
    cleaned = {k: v for k, v in card.items() if k in KEEP_FIELDS}
    # 文案没了，卡片标题退化为博主名 + 风格标签，不冒充原作
    tags = "、".join(cleaned.get("style_tags") or [])
    cleaned["title"] = f"{cleaned.get('creator', '博主')} · {tags}".strip(" ·")
    cleaned["summary"] = ""
    cleaned["text_stripped"] = True
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清除缓存中的博主文案（转授权兜底）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = Path(CONTENT["cache_path"])
    cache = load_cache()
    by_style = cache.get("by_style", {})
    if not by_style:
        print(f"⚠️ 缓存为空或不存在：{path}")
        return 1

    total = 0
    for style, entry in by_style.items():
        cards = entry.get("cards") or []
        entry["cards"] = [scrub_card(c) for c in cards]
        total += len(cards)
        print(f"  {style}: {len(cards)} 条文案已清除")

    if args.dry_run:
        print(f"\n[dry-run] 将清除 {total} 条卡片的 title/summary，未写盘。")
        print("去掉 --dry-run 即执行。")
        return 0

    cache["text_stripped"] = True
    save_cache(cache)
    print(f"\n✅ 已清除 {total} 条文案，保留跳转链。缓存：{path}")
    print("   提示：此后 refresh_content_cache 重新抓取会恢复文案，请再次执行本脚本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
