"""「妆镜」Agent 主循环（Step 5 · 骨架层）。

两段式：视觉段（detect_visible_issues）+ 文本段（本 loop 的主模型）。
运行：
    cd products && python -m beauty_mirror.agent            # 交互
    cd products && python -m beauty_mirror.agent --demo     # 离线全链路演示
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import tools
from .config import PKG_DIR, TEXT, VISION

LOG = logging.getLogger("beauty_mirror.agent")
TRAJECTORY_DIR = PKG_DIR / ".trajectories"


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., str]
    permission: str = "allow"


TOOL_SPECS: dict[str, Tool] = {
    "start_vision_prefetch": Tool(
        name="start_vision_prefetch",
        description=(
            "照片上传后**立即**调用：后台并行开始视觉检测。因为视觉很慢（实测中位 18s），"
            "而用户答 3 道问卷要 5-8s，并行可以把延迟藏起来。调完马上展示问卷。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "provider": {"type": "string"},
            },
            "required": ["image_path"],
        },
        handler=tools.start_vision_prefetch,
    ),
    "detect_visible_issues": Tool(
        name="detect_visible_issues",
        description=(
            "检测照片中的可见皮肤问题（痘印/泛红/T区油光/毛孔/黑眼圈），带置信度。"
            "只做检测，不做肤质分类。失败会返回 degraded=true。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "provider": {"type": "string"},
            },
            "required": ["image_path"],
        },
        handler=tools.detect_visible_issues,
    ),
    "get_questionnaire": Tool(
        name="get_questionnaire",
        description="返回 3 道极简肤质问卷（洗完脸紧绷/换季泛红/中午T区出油）。",
        input_schema={"type": "object", "properties": {}},
        handler=tools.get_questionnaire,
    ),
    "classify_skin_type": Tool(
        name="classify_skin_type",
        description=(
            "用 3 个问卷答案确定性推导肤质（干/油/混/敏感）。"
            "参数取值为各题选项 key：tight(yes/sometimes/no)、"
            "redness(often/sometimes/no)、oil(yes/sometimes/no)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tight": {"type": "string"},
                "redness": {"type": "string"},
                "oil": {"type": "string"},
            },
        },
        handler=tools.classify_skin_type,
    ),
    "fetch_creator_cards": Tool(
        name="fetch_creator_cards",
        description=(
            "按风格标签拉取博主代表视频与妆教文案卡片。上游不可用会自动降级到预置内容，"
            "返回的 cards[].original_url 供前端跳转原平台。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "style_tag": {"type": "string"},
                "platform": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["style_tag"],
        },
        handler=tools.fetch_creator_cards,
    ),
    "get_tutorial_breakdown": Tool(
        name="get_tutorial_breakdown",
        description="取某张卡片下某个妆教的分步拆解。",
        input_schema={
            "type": "object",
            "properties": {
                "card_id": {"type": "string"},
                "tutorial_id": {"type": "string"},
            },
            "required": ["card_id"],
        },
        handler=tools.get_tutorial_breakdown,
    ),
    "compliance_check": Tool(
        name="compliance_check",
        description="对准备输出给用户的内容做合规体检（医疗红线/绝对化用语/效果承诺）。",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=tools.compliance_check,
    ),
    "get_community_qr": Tool(
        name="get_community_qr",
        description="取社群入口二维码（展示在流程最后）。",
        input_schema={"type": "object", "properties": {}},
        handler=tools.get_community_qr,
    ),
}

TOOL_DEFS = [
    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
    for t in TOOL_SPECS.values()
]

WORKDIR = Path.cwd().resolve()

SYSTEM_PROMPT = f"""你是「妆镜」的皮肤顾问兼美妆导览，服务 20-38 岁女性用户。

职责：看懂用户的皮肤现状 → 给出护肤方案与美妆建议 → 推荐可跟练的妆教。

工作顺序（不要跳步）：
1. 拿到照片路径后，**先立刻调 start_vision_prefetch**（后台跑视觉），紧接着调
   get_questionnaire 向用户要答案 —— 两者并行，把视觉的十几秒藏起来。
2. 用户答完后调 detect_visible_issues，这时结果通常已就绪（会直接复用预热结果）。
   肤质**只来自问卷**，不要试图从照片判断肤质：把答案交给 classify_skin_type。
3. 结合「问卷肤质 + 视觉可见问题」两个因子，写护肤方案与美妆要点。
   两个因子冲突时（如问卷说干、照片显示 T 区油光）要如实说明，不要抹平。
4. 用户选定风格标签后，调 fetch_creator_cards 拿卡片；需要细节再调
   get_tutorial_breakdown。卡片要带 original_url 跳转原平台。
5. 输出前必须调 compliance_check 自查；status=rework 就按 fix 改写后重查。
6. 流程最后调 get_community_qr 展示社群入口。

硬性规则：
- 医疗红线：不做诊断、不给治疗方案；疑似痤疮/皮炎/色斑病变/严重敏感，明确建议就医。
- 诚实置信：视觉结果有误差，必须展示置信度；degraded=true 时说明是降级结果。
- 不夸大功效：不用"根治/治愈/百分百/立竿见影"这类表述。
- 商品中立：只给"品类 + 关键成分 + 适配理由"，标注"非广告，仅供参考"，不承诺效果。
- 隐私：照片仅本次分析使用，不持久化。
- 来源透明：博主内容来自第三方平台，必须标注来源与原作者，不冒充原创。

可用可见问题（闭集）：{tools.issue_taxonomy_block()}

可用风格标签：{tools.style_tags_block()}

当前视觉 provider：{VISION["provider"]}。
"""


# ---------------------------------------------------------------------------
# 权限检查
# ---------------------------------------------------------------------------


def permission_check(tool: Tool, args: dict) -> str:
    """本产品全部工具为只读/无副作用，默认 allow。

    保留检查点：将来 `save_profile` 这类写操作在此改成 ask。
    """
    return "deny" if tool.permission == "deny" else "allow"


# ---------------------------------------------------------------------------
# 文本段模型调用（惰性构造 client，缺 key 时报错清晰）
# ---------------------------------------------------------------------------

_client: Any = None


def get_text_client():
    global _client
    if _client is None:
        from anthropic import Anthropic

        if not os.getenv(TEXT["api_key_env"]):
            raise RuntimeError(f"缺少环境变量 {TEXT['api_key_env']}（文本段模型密钥）")
        _client = Anthropic(base_url=TEXT["base_url"])
    return _client


def call_model(messages: list[dict]) -> Any:
    """调文本段模型，带重试。"""
    from anthropic import APIConnectionError, APIStatusError, APITimeoutError

    client = get_text_client()
    last_error: Exception | None = None
    for attempt in range(TEXT["max_retries"]):
        try:
            return client.messages.create(
                model=TEXT["model"],
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOL_DEFS,
                max_tokens=TEXT["max_tokens"],
                timeout=TEXT["timeout_seconds"],
            )
        except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
            last_error = exc
            delay = 0.5 * (2**attempt)
            LOG.warning("文本段调用失败 (%d/%d): %s；%.1fs 后重试",
                        attempt + 1, TEXT["max_retries"], exc, delay)
            time.sleep(delay)
    raise RuntimeError(f"文本段模型调用失败：{last_error}")


# ---------------------------------------------------------------------------
# 轨迹采集
# ---------------------------------------------------------------------------


def record_trajectory(entry: dict) -> None:
    try:
        TRAJECTORY_DIR.mkdir(parents=True, exist_ok=True)
        path = TRAJECTORY_DIR / f"trajectory-{time.strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        LOG.warning("轨迹写入失败: %s", exc)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


def _validate(tool: Tool, args: dict) -> dict:
    for field_name in tool.input_schema.get("required", []):
        if field_name not in args:
            raise ValueError(f"缺少必填参数: {field_name}")
    return args


def run_tool(name: str, args: dict) -> str:
    """执行一个工具（未经模型），返回字符串结果。"""
    tool = TOOL_SPECS.get(name)
    if tool is None:
        return json.dumps({"ok": False, "error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        validated = _validate(tool, dict(args or {}))
        if permission_check(tool, validated) == "deny":
            return json.dumps({"ok": False, "error": "权限拒绝"}, ensure_ascii=False)
        output = tool.handler(**validated)
    except Exception as exc:  # noqa: BLE001
        output = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
    record_trajectory({"ts": time.time(), "tool": name, "input": args, "output": output[:1000]})
    return output


def agent_loop(prompt: str, history: list[dict] | None = None) -> str:
    history = history if history is not None else []
    history.append({"role": "user", "content": prompt})

    while True:
        response = call_model(history)
        history.append({"role": "assistant", "content": response.content})

        tool_calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_calls:
            return "".join(
                b.text for b in response.content if getattr(b, "type", None) == "text"
            )

        results = []
        for block in tool_calls:
            output = run_tool(block.name, dict(block.input or {}))
            LOG.info("tool=%s -> %s", block.name, output[:160].replace("\n", " "))
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        history.append({"role": "user", "content": results})


# ---------------------------------------------------------------------------
# 离线全链路演示（无模型 / 无网络也能跑，demo 兜底）
# ---------------------------------------------------------------------------


def run_offline_demo(image_path: str, style_tag: str = "清透裸妆") -> None:
    print("=" * 66)
    print("离线全链路演示（不调用文本模型；视觉与内容均走兜底）")
    print("=" * 66)

    print("\n[1/6] 视觉段：检测可见问题")
    vision = json.loads(run_tool("detect_visible_issues", {"image_path": image_path}))
    if vision.get("issues_labeled"):
        for item in vision["issues_labeled"]:
            print(f"  - {item['label']:<6} 置信度 {item['confidence']:.2f}  {item.get('areas')}")
    else:
        # 空结果也要说清楚：是“没查到”，还是“要重拍”
        print(f"  未检测到明显可见问题（provider={vision.get('provider')}）")
    if vision.get("needs_retake"):
        print(f"  ↻ {vision.get('hint', '建议重拍')}")
    if vision.get("fallback_used"):
        print(f"  ⚠ 已启用兜底：{vision.get('hint', '')}")
    elif vision.get("degraded"):
        print(f"  ⚠ 降级：{vision.get('error', '')}")

    print("\n[2/6] 肤质问卷（3 题）")
    sheet = json.loads(run_tool("get_questionnaire", {}))
    for idx, q in enumerate(sheet["questions"], 1):
        print(f"  {idx}. {q['text']}")

    print("\n[3/6] 肤质判定（示例答案）")
    skin = json.loads(run_tool("classify_skin_type", {"tight": "yes", "redness": "sometimes", "oil": "yes"}))
    print(f"  → {skin['skin_type']}  标签={skin['labels']}  scores={skin['scores']}")

    print(f"\n[4/6] 按风格拉内容卡片：{style_tag}")
    cards = json.loads(run_tool("fetch_creator_cards", {"style_tag": style_tag, "limit": 2}))
    print(f"  来源={cards['source']}  条数={cards['count']}  降级={cards['degraded']}")
    for card in cards["cards"]:
        print(f"  · [{card['platform']}] {card['creator']} — {card['title']}")
        print(f"    跳转: {card['original_url']}")

    first_id = cards["cards"][0]["id"] if cards["cards"] else ""
    if first_id:
        print("\n[5/6] 妆教拆解")
        breakdown = json.loads(run_tool("get_tutorial_breakdown", {"card_id": first_id}))
        if breakdown.get("ok"):
            tut = breakdown["tutorial"]
            print(f"  {tut.get('title')}（{tut.get('duration', '')}）")
            for step in tut.get("steps", []):
                print(f"    - {step}")

    print("\n[6/6] 合规体检 + 社群入口")
    check = json.loads(run_tool("compliance_check", {"text": "这套方案能彻底根治痘痘，7天见效。"}))
    print(f"  status={check['status']}（命中 {len(check['hits'])} 处，示例为故意违规）")
    qr = json.loads(run_tool("get_community_qr", {}))
    print(f"  社群入口：{qr['note']}")
    print("\n✅ 全链路走通，无异常抛出。")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="妆镜 Agent（Step 5 骨架）")
    parser.add_argument("--demo", action="store_true", help="离线全链路演示，不需要模型密钥")
    parser.add_argument("--image", default="", help="素颜照路径（demo/交互用）")
    parser.add_argument("--style", default="清透裸妆", help="风格标签")
    parser.add_argument("--provider", default="", help="视觉 provider（qwen_vl/doubao_vision/glm_4v/mock）")
    parser.add_argument("--offline", action="store_true", help="强制全离线（视觉+内容都走 mock）")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    if args.offline:
        from .config import DEMO

        DEMO["offline"] = True
        VISION["provider"] = "mock"
    if args.provider:
        VISION["provider"] = args.provider

    if args.demo:
        run_offline_demo(args.image or "demo.jpg", args.style)
        return 0

    print(f"妆镜 Agent 就绪。文本段模型={TEXT['model']}，视觉 provider={VISION['provider']}")
    print("输入 'q' 退出。\n")

    history: list[dict] = []
    while True:
        try:
            query = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query in {"q", "quit", "exit", ""}:
            break
        try:
            print(agent_loop(query, history))
        except Exception as exc:  # noqa: BLE001
            LOG.error("循环失败: %s", exc)
            print(f"错误：{exc}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
