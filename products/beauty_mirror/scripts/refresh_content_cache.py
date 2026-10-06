"""预热内容缓存：跑 Apify 抓真实博主内容，落到 data/content_cache.json。

为什么需要这一步：Apify Actor 是秒级~分钟级重操作，不能放进用户同步链路。
预热一次，用户流程就只读本地缓存 —— 快、免费、断网可用，而且内容是**真实的**。

用法：
    cd products
    python -m beauty_mirror.scripts.refresh_content_cache                 # 全量 5 个标签
    python -m beauty_mirror.scripts.refresh_content_cache --style 清透裸妆
    python -m beauty_mirror.scripts.refresh_content_cache --dry-run       # 不调用，只看会花多少钱
"""

from __future__ import annotations

import argparse
import json
import sys

from ..config import APIFY, CONTENT
from ..domain import load_style_tags
from ..providers import apify_client
from ..providers.content_api import (
    build_apify_input,
    get_dialect,
    load_cache,
    normalize_items,
    save_cache,
    write_style_to_cache,
)

# 关键词候选：直接搜"欧美浓颜"可能 0 结果，逐步降级到更通用的叫法
_STOP = " 妆容教程"


def _keywords(tag: dict) -> list[str]:
    """按"精确 → 通用"给出候选关键词，逐个尝试直到抓到数据。"""
    name = str(tag.get("name", "")).strip()
    aliases = [str(a).strip() for a in (tag.get("aliases") or []) if str(a).strip()]
    kws = [str(k).strip() for k in (tag.get("keywords") or []) if str(k).strip()]
    cands = [f"{name}{_STOP}", name]
    cands += [f"{a} 妆容" for a in aliases]
    cands += [f"{name} {kws[0]}"] if kws else []
    cands += aliases
    seen: list[str] = []
    for c in cands:
        if c and c not in seen:
            seen.append(c)
    return seen


def _targets(only: str | None) -> list[dict]:
    tags = load_style_tags()
    if only:
        tags = [t for t in tags if only in (str(t.get("id")), str(t.get("name")))]
    return tags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="预热 Apify 内容缓存")
    parser.add_argument("--style", default="", help="只刷新某个风格标签（id 或名称）")
    parser.add_argument("--limit", type=int, default=APIFY["max_items"], help="每个标签抓多少条")
    parser.add_argument("--memory", type=int, default=APIFY["memory_mb"])
    parser.add_argument("--timeout", type=int, default=APIFY["timeout_seconds"])
    parser.add_argument("--per-tag", type=int, default=0, help="每个标签保留几条卡片（默认全部）")
    parser.add_argument("--force", action="store_true", help="即使新数据比缓存少也覆盖（默认不覆盖）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与预估花费，不调用")
    args = parser.parse_args(argv)

    tags = _targets(args.style or None)
    if not tags:
        print(f"❌ 没找到匹配的风格标签：{args.style}")
        return 2

    total_items = args.limit * len(tags)
    est = apify_client.price_per_start_usd_default() * len(tags) + APIFY["price_per_item_usd"] * total_items
    print(f"目标标签 {len(tags)} 个 × 最多 {args.limit} 条 = {total_items} 条")
    print(f"预估花费 ≈ ${est:.3f}（≈ ¥{est * 7.2:.2f}）｜actor={APIFY['actor_id']}")
    if not APIFY["token"]:
        print("❌ 缺少 APIFY_TOKEN，先填 .env")
        return 2
    if args.dry_run:
        for tag in tags:
            print(f"  - {tag.get('name')} ← 候选关键词 {_keywords(tag)}")
        return 0

    ok = 0
    spent = 0.0
    dialect = get_dialect(APIFY["actor_id"])
    print(f"actor 方言：{dialect.actor_id}\n")
    for tag in tags:
        name = str(tag.get("name"))
        cards: list[dict] = []
        used_keyword = ""
        for keyword in _keywords(tag):
            run_input = build_apify_input(keyword, args.limit, dialect.actor_id)
            result = apify_client.run_actor_sync(
                dialect.actor_id,
                run_input,
                APIFY["token"],
                timeout_seconds=args.timeout,
                memory_mb=args.memory,
                price_per_item_usd=APIFY["price_per_item_usd"],
            )
            spent += result.cost_usd_estimate
            if not result.ok:
                print(f"    ⚠ 关键词「{keyword}」失败：{result.error[:80]}")
                continue
            if not result.items:
                # 区分“真无结果”与“预览额度耗尽”
                print(f"    ⚠ 关键词「{keyword}」→ 0 条")
                continue
            cards = normalize_items(result.items, name, dialect)
            used_keyword = keyword
            break

        if args.per_tag:
            cards = cards[: args.per_tag]
        if not cards:
            print(f"  ❌ {name}: 所有关键词均无结果（试过 {len(_keywords(tag))} 个）")
            print("     提示：若所有标签都 0 条，多半是 Apify FREE 计划的预览额度用尽")
            print("          （actor 返回里会有 free_preview_notice 字段），需升级套餐")
            continue

        # 防止用少量预览数据冲掉已有的完整缓存
        existing = len(load_cache().get("by_style", {}).get(name, {}).get("cards") or [])
        if existing > len(cards) and not args.force:
            print(f"  ⏭ {name}: 新抓 {len(cards)} 条 < 已有 {existing} 条，保留旧缓存（--force 可覆盖）")
            continue

        write_style_to_cache(name, cards)
        ok += 1
        print(f"  ✅ {name}: {len(cards)} 张卡片（关键词「{used_keyword}」）")

    cache = load_cache()
    cache["generated_at"] = int(__import__("time").time())
    cache["actor_id"] = APIFY["actor_id"]
    save_cache(cache)
    print(f"\n完成 {ok}/{len(tags)} 个标签，累计花费 ≈ ${spent:.3f}")
    print(f"缓存：{CONTENT['cache_path']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
