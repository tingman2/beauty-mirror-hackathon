"""领域词表：可见问题分类 + 风格标签。

视觉段**只检测可见问题**，不做肤质分类（肤质由 3 道问卷决定，见 skin_type.py）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..config import DATA_DIR

# --- 可见问题（视觉段唯一输出空间，闭集）-----------------------------------
ISSUE_TAXONOMY: dict[str, dict[str, str]] = {
    "acne_marks": {"label": "痘印", "hint": "红色/褐色痘印、痘坑，多在面颊与下巴"},
    "redness": {"label": "泛红", "hint": "面颊或鼻翼泛红、红血丝"},
    "t_zone_oil": {"label": "T区油光", "hint": "额头、鼻部明显反光出油"},
    "pores": {"label": "毛孔", "hint": "鼻翼、面颊毛孔可见或粗大"},
    "dark_circles": {"label": "黑眼圈", "hint": "眼下暗沉、发青或发褐"},
}
ISSUE_KEYS: tuple[str, ...] = tuple(ISSUE_TAXONOMY)


def label_of(key: str) -> str:
    return ISSUE_TAXONOMY.get(key, {}).get("label", key)


def taxonomy_prompt_block() -> str:
    """给视觉模型的闭集说明，禁止它自由发挥分类名。"""
    lines = [
        f'- "{key}"（{meta["label"]}）：{meta["hint"]}'
        for key, meta in ISSUE_TAXONOMY.items()
    ]
    return "\n".join(lines)


# --- 风格标签（1.0 规模：5 个标签 × 2 个妆教）-------------------------------
STYLE_TAGS_PATH = DATA_DIR / "style_tags.yaml"


def load_style_tags() -> list[dict[str, Any]]:
    """读取风格标签清单；文件缺失时返回内置最小集，保证不崩。"""
    try:
        with STYLE_TAGS_PATH.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        tags = data.get("style_tags") or []
        if isinstance(tags, list) and tags:
            return tags
    except Exception:  # noqa: BLE001 - 兜底优先，演示不能因为文件问题挂掉
        pass
    return [
        {"id": "clean_nude", "name": "清透裸妆", "aliases": ["裸妆", "伪素颜"], "tutorials": []},
    ]


def style_tag_ids() -> list[str]:
    return [str(tag.get("id", "")) for tag in load_style_tags()]


def find_style_tag(query: str) -> dict[str, Any] | None:
    """按 id / 名称 / 别名模糊匹配风格标签。"""
    q = (query or "").strip().lower()
    if not q:
        return None
    for tag in load_style_tags():
        names = [str(tag.get("id", "")), str(tag.get("name", ""))]
        names += [str(a) for a in (tag.get("aliases") or [])]
        if any(q == n.lower() for n in names):
            return tag
    for tag in load_style_tags():
        names = [str(tag.get("name", ""))] + [str(a) for a in (tag.get("aliases") or [])]
        if any(q in n.lower() for n in names):
            return tag
    return None


def tutorial_count() -> int:
    return sum(len(tag.get("tutorials") or []) for tag in load_style_tags())


def _data_file(name: str) -> Path:
    return DATA_DIR / name


MOCK_CREATORS_PATH = _data_file("mock_creators.json")
