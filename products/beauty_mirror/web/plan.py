"""确定性方案生成：把「问卷肤质 + 可见问题」翻译成护肤方案 + 美妆要点。

设计取舍（Web 原型阶段）：
  - 文案由**规则**拼装，与肤质/可见问题一一对应：可复现、零成本、不依赖文本模型，
    保证评委现场无论怎么点都不会白屏（对齐 PRD「兜底永远有输出」）；
  - 文本段模型（agent_loop）作为后续增强层，通过 maybe_llm_summary 挂载，
    默认关闭（WEB_LLM_NARRATIVE=true 才启用）；
  - 所有输出必须能通过 tools.compliance_check（医疗红线/绝对化/效果承诺）。

注意：本文件的文案**刻意避开**合规词表里的字眼（如"彻底/第一/根治/治疗"），
改文案后请跑 tests/test_web_contract.py，里面有合规自检。
"""

from __future__ import annotations

import logging
import os
from typing import Any

LOG = logging.getLogger("beauty_mirror.web.plan")

DISCLAIMER = (
    "本结果基于照片的可见特征与问卷自评，非医学判断，效果因人而异；"
    "如有持续泛红、脓疱、疼痛或短期内明显变化，请尽早就医确认。"
)

# 结论依据与不确定性：明确区分"来自问卷"与"来自照片估计"，避免无依据的确定语气
BASIS_NOTE = (
    "肤质结论只来自 3 道问卷，不由照片推断；可见问题来自照片视觉估计，存在误差，"
    "请结合各项置信度与自身体感综合判断，不要当成医学结论。"
)

SEVERITY_LABEL = {"mild": "轻度", "moderate": "中度", "severe": "明显"}


def severity_label(value: str) -> str:
    return SEVERITY_LABEL.get(str(value), "轻度")


# ---------------------------------------------------------------------------
# 一、按肤质族给出的基础护理（清洁 / 保湿 / 防晒）
# ---------------------------------------------------------------------------

_BASE_BY_FAMILY: dict[str, list[dict[str, Any]]] = {
    "dry": [
        {
            "title": "清洁",
            "advice": "早晚各一次。晨间可用清水或极温和洁面，晚间用氨基酸系洁面，"
                      "避开皂基与高泡产品，水温接近体温即可。",
            "tags": ["氨基酸洁面", "温水", "不过度清洁"],
        },
        {
            "title": "保湿",
            "advice": "洁面后 3 分钟内开始保湿。优先选含神经酰胺、角鲨烷、甘油、"
                      "透明质酸的产品，干得明显时叠一层面霜或面部油锁水。",
            "tags": ["神经酰胺", "角鲨烷", "锁水"],
        },
        {
            "title": "防晒",
            "advice": "每天使用。选带保湿成分的乳霜型防晒，SPF30+ / PA+++ 起步；"
                      "阴天与室内靠窗同样需要。",
            "tags": ["SPF30+", "乳霜型", "每天"],
        },
    ],
    "oily": [
        {
            "title": "清洁",
            "advice": "早晚各一次温和洁面。中午出油明显时用清水冲洗或吸油纸按压，"
                      "不要靠频繁洗脸去油，以免越洗越油。",
            "tags": ["温和洁面", "吸油纸", "不过度"],
        },
        {
            "title": "保湿",
            "advice": "选清爽凝胶或凝露质地，含烟酰胺、锌类或透明质酸。"
                      "出油不等于不需要保湿，缺水同样会加重出油观感。",
            "tags": ["凝露", "烟酰胺", "轻薄"],
        },
        {
            "title": "防晒",
            "advice": "选清爽型或摇摇乐质地，SPF30+ / PA+++ 起步。"
                      "油皮更要防晒，紫外线会影响出油与痘印外观。",
            "tags": ["清爽型", "SPF30+", "摇摇乐"],
        },
    ],
    "mixed": [
        {
            "title": "清洁",
            "advice": "分区清洁：T 区重点打圈，颊部轻柔带过。"
                      "整脸用同一支温和洁面即可，不需要为 T 区单独加一支强控油。",
            "tags": ["分区清洁", "温和"],
        },
        {
            "title": "保湿",
            "advice": "分区保湿：T 区用清爽质地，颊部用滋润质地。"
                      "同一张脸可以叠两种保湿产品，不必迁就其中一边。",
            "tags": ["分区保湿", "T区清爽", "颊部滋润"],
        },
        {
            "title": "防晒",
            "advice": "全脸防晒，T 区优先选清爽型。"
                      "容易搓泥时改成少量多次点涂，再轻轻拍开。",
            "tags": ["全脸", "清爽型", "点涂"],
        },
    ],
    "normal": [
        {
            "title": "清洁",
            "advice": "早晚温和洁面即可，不追求强去角质。"
                      "若洗完有紧绷感，说明洁面偏强，需要换更温和的。",
            "tags": ["温和洁面", "不紧绷"],
        },
        {
            "title": "保湿",
            "advice": "基础保湿，含甘油或透明质酸即可。"
                      "换季时按当下感受加减，不必固定一套。",
            "tags": ["基础保湿", "换季调整"],
        },
        {
            "title": "防晒",
            "advice": "每天使用，SPF30+ / PA+++ 起步。"
                      "这是护肤里投入产出比最高的一步。",
            "tags": ["SPF30+", "每天"],
        },
    ],
}

_SENSITIVE_EXTRA = {
    "title": "屏障与耐受",
    "advice": "先修护再谈功效：以神经酰胺、泛醇、积雪草、β-葡聚糖为主，"
              "暂缓高浓度酸类与强功效产品；新品先在耳后小面积试用 2-3 天再上脸。",
    "tags": ["修护屏障", "泛醇", "积雪草", "先试用"],
}


def _family(skin_type: str) -> str:
    body = skin_type or ""
    if "混" in body:
        return "mixed"
    if body.startswith("干"):
        return "dry"
    if body.startswith("油"):
        return "oily"
    return "normal"


# ---------------------------------------------------------------------------
# 二、按可见问题给出的针对性护理
# ---------------------------------------------------------------------------

_ISSUE_ADVICE: dict[str, dict[str, str]] = {
    "acne_marks": {
        "advice": "痘印处避免抠挤，日常加一步含烟酰胺或泛醇的修护类产品，"
                  "帮助改善外观；新痘印注意防晒以减少色素沉着。",
        "tags": ["烟酰胺", "不抠挤", "防晒"],
    },
    "redness": {
        "advice": "以舒缓修护为主：积雪草、泛醇、神经酰胺；温水洁面，"
                  "减少摩擦与去角质频次，暂缓高浓度酸类。",
        "tags": ["舒缓", "神经酰胺", "少摩擦"],
    },
    "t_zone_oil": {
        "advice": "T 区改用清爽控油质地，出油时用吸油纸按压而非反复洗脸；"
                  "保湿步骤不能省，否则出油观感会更明显。",
        "tags": ["控油", "吸油纸", "保湿"],
    },
    "pores": {
        "advice": "毛孔外观与出油、光老化相关：做好温和清洁、规律保湿与每日防晒；"
                  "含水杨酸或烟酰胺的产品可从低频开始尝试。",
        "tags": ["温和清洁", "防晒", "低频尝试"],
    },
    "dark_circles": {
        "advice": "优先安排充足睡眠与用眼休息；眼周加强保湿与防晒，"
                  "含咖啡因或维生素 K 的眼部产品可作为辅助。",
        "tags": ["睡眠", "眼周保湿", "防晒"],
    },
}

_ISSUE_DEFAULT = {"advice": "保持温和清洁与规律保湿，观察 2-4 周的变化趋势。", "tags": ["观察趋势"]}


# ---------------------------------------------------------------------------
# 三、按可见问题给出的美妆要点
# ---------------------------------------------------------------------------

_MAKEUP_BY_ISSUE: dict[str, str] = {
    "acne_marks": "底妆少量多次，痘印处用遮瑕点涂后轻拍开边缘，避免大面积厚涂导致斑驳。",
    "redness": "泛红区域可用绿色系妆前或遮瑕做局部中和，粉底选贴合肤色的暖调，避免一味选白。",
    "t_zone_oil": "妆前 T 区做控油打底，散粉只压 T 区与鼻翼，颊部保留自然光泽。",
    "pores": "修饰毛孔的妆前乳只涂鼻翼与颊部内侧，顺着毛孔方向按压，不要全脸铺满。",
    "dark_circles": "眼下用偏橘调遮瑕中和暗沉，少量多次薄叠，避免一次涂厚卡纹。",
}

_MAKEUP_BY_FAMILY: dict[str, str] = {
    "dry": "妆前保湿按压到半吸收再上底妆，能明显减少卡粉与浮粉。",
    "oily": "底妆优先选持妆型，定妆重点放在 T 区，随身带吸油纸而非粉饼反复补。",
    "mixed": "妆前分区：T 区控油打底，颊部保湿打底，再上底妆会更服帖。",
    "normal": "维持常规底妆顺序即可，定妆按当天出油情况加减。",
}


def _makeup_tips(skin: dict[str, Any], issues: list[str]) -> list[str]:
    tips: list[str] = []
    for key in issues:
        text = _MAKEUP_BY_ISSUE.get(key)
        if text and text not in tips:
            tips.append(text)
    family_tip = _MAKEUP_BY_FAMILY.get(_family(skin.get("skin_type", "")))
    if family_tip:
        tips.append(family_tip)
    if skin.get("sensitive") or "敏感" in "".join(skin.get("labels") or []):
        tips.append("避开强酒精与强香精的定妆产品，上妆工具保持清洁并定期更换。")
    return tips


# ---------------------------------------------------------------------------
# 四、冲突检测（问卷 vs 视觉）
# ---------------------------------------------------------------------------


def _conflicts(skin: dict[str, Any], issues: list[dict[str, Any]]) -> list[str]:
    """问卷 vs 照片的不一致：如实说明，不用确定性语气掩盖。"""
    keys = {item.get("type") for item in issues}
    family = _family(skin.get("skin_type", ""))
    answers = skin.get("answers") or {}
    out: list[str] = []
    if family == "dry" and "t_zone_oil" in keys:
        out.append(
            "问卷判为干性，但照片显示 T 区油光，可能是外油内干或混合性；"
            "建议按分区护理调整，而不是全脸加厚保湿。"
        )
    if family == "oily" and not keys:
        out.append(
            "问卷判为油性，但照片未见明显可见问题；"
            "保持温和清洁即可，不建议为『预防』而叠加强控油产品。"
        )
    if answers.get("oil") in ("yes", "sometimes") and "t_zone_oil" not in keys:
        out.append(
            "问卷显示 T 区会出油，但照片未检出 T 区油光——油光受光线与角度影响很大，"
            "且视觉检测会漏检，请以自身体感为准。"
        )
    if answers.get("oil") == "no" and "t_zone_oil" in keys:
        out.append(
            "问卷说 T 区基本不出油，但照片检出油光，建议结合当时的光线与实际出油情况判断。"
        )
    if answers.get("redness") in ("often", "sometimes") and "redness" not in keys:
        out.append(
            "问卷提示容易泛红，但照片未检出泛红，可能受光线或拍摄角度影响。"
        )
    if skin.get("sensitive") and "redness" in keys:
        out.append(
            "问卷提示敏感倾向，照片也显示泛红，两者一致；"
            "建议优先修护屏障，暂缓强功效产品。"
        )
    return out


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def build_plan(skin: dict[str, Any], vision: dict[str, Any]) -> dict[str, Any]:
    """把肤质结论与可见问题拼成结构化方案。永远返回完整结构。

    兼容两种入参：`issues`（已归类）与工具原始输出的 `issues_labeled`。
    """
    issues = list(vision.get("issues") or vision.get("issues_labeled") or [])
    issue_keys = [item.get("type", "") for item in issues]
    labels = list(skin.get("labels") or [])
    skin_type = str(skin.get("skin_type") or "待确认")

    sections = [dict(item) for item in _BASE_BY_FAMILY.get(_family(skin_type), _BASE_BY_FAMILY["normal"])]
    if skin.get("sensitive") or any("敏感" in str(x) for x in labels):
        sections.append(dict(_SENSITIVE_EXTRA))

    target = []
    for item in issues:
        key = item.get("type", "")
        advice = _ISSUE_ADVICE.get(key, _ISSUE_DEFAULT)
        target.append(
            {
                "title": item.get("label") or key,
                "advice": advice["advice"],
                "tags": advice["tags"],
                "confidence": item.get("confidence", 0),
                "severity": severity_label(item.get("severity", "")),
                "areas": item.get("areas") or [],
            }
        )

    if issues:
        headline = f"{skin_type} · 可见问题 {len(issues)} 项"
        skin_summary = (
            f"问卷判定为{skin_type}"
            + (f"（{'/'.join(labels)}）" if labels else "")
            + f"；照片检出 {len(issues)} 项可见问题："
            + "、".join(item.get("label", "") for item in issues)
            + "。"
        )
    else:
        headline = f"{skin_type} · 未见明显可见问题"
        skin_summary = (
            f"问卷判定为{skin_type}"
            + (f"（{'/'.join(labels)}）" if labels else "")
            + "；本次照片未检出明显可见问题。"
        )

    if vision.get("degraded") or vision.get("fallback_used"):
        skin_summary += "（视觉结果已降级，以上以问卷结论为主。）"

    plan = {
        "headline": headline,
        "skin_summary": skin_summary,
        "basis": BASIS_NOTE,
        "sections": sections,
        "target": target,
        "makeup": _makeup_tips(skin, issue_keys),
        "conflicts": _conflicts(skin, issues),
        "disclaimer": DISCLAIMER,
        "generated_by": "rule",
    }
    return plan


def plan_text(plan: dict[str, Any]) -> str:
    """把方案摊平成纯文本，供 compliance_check 体检。"""
    parts: list[str] = [plan.get("headline", ""), plan.get("skin_summary", "")]
    parts.append(plan.get("basis", ""))
    for section in plan.get("sections", []):
        parts.append(f"{section.get('title', '')}：{section.get('advice', '')}")
    for item in plan.get("target", []):
        parts.append(f"{item.get('title', '')}：{item.get('advice', '')}")
    parts.extend(plan.get("makeup", []))
    parts.extend(plan.get("conflicts", []))
    parts.extend(plan.get("limitations", []))
    parts.append(plan.get("disclaimer", ""))
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# 五、妆教拆解兜底：第三方卡片没有分步时，用我们自撰的通用步骤
# ---------------------------------------------------------------------------

# 这些步骤是我们自己写的通用框架（非第三方文案），可安全展示
FOCUS_STEPS: dict[str, list[str]] = {
    "妆前": [
        "按肤质做局部打底：易干部位保湿，T 区控油",
        "等 30-60 秒到半吸收再上底妆，避免搓泥",
    ],
    "底妆": [
        "粉底挤少量，用指腹或湿海绵从面中向外拍开",
        "需要遮的地方只叠第二层，不要一次涂厚",
    ],
    "遮瑕": [
        "底妆之后，在瑕疵处点少量遮瑕",
        "用指腹轻拍边缘过渡，不要来回推",
    ],
    "定妆": [
        "散粉只压 T 区与鼻翼，颊部按需带过",
        "用刷子抖掉多余粉再上脸，避免结块",
    ],
    "眉形": [
        "先定眉尾落点，再补眉峰",
        "眉头用刷子晕开，不要画实",
    ],
    "睫毛": [
        "先夹翘再刷，刷一层即可",
        "下睫毛用刷头余量带过",
    ],
    "眼线": [
        "从眼中开始向眼尾拉，最后补眼头",
        "手抖时用棉签修边缘，别反复描",
    ],
    "眼影": [
        "浅色铺眼窝，深色只压眼尾三角",
        "边界用干净刷子晕开，避免显脏",
    ],
    "修容": [
        "先定骨相位置，再少量多次上色",
        "下颌与发际线从外向内扫",
    ],
    "高光": [
        "只点在颧骨上方与鼻梁中段",
        "用手指轻拍，不要大面积扫开",
    ],
    "腮红": [
        "位置比颜色更重要：笑肌最高点向外晕",
        "少量多次，宁浅勿重",
    ],
    "唇妆": [
        "先用润唇打底，再上颜色",
        "边缘用手指模糊，自然不刻意",
    ],
    "卧蚕": [
        "在眼下笑肌上方用浅色提亮",
        "用细刷蘸少量阴影勾下缘",
    ],
    "唇色": [
        "选贴近自身唇色的色号先试",
        "薄涂叠加，比一次涂满更好控制",
    ],
}


def tutorial_fallback(style_name: str, tutorial: dict[str, Any]) -> dict[str, Any]:
    """当第三方卡片没有分步数据时，用 focus 生成通用步骤（我们自撰）。"""
    focus = [str(x) for x in (tutorial.get("focus") or [])]
    steps: list[str] = []
    for item in focus:
        for step in FOCUS_STEPS.get(item, [f"{item}：少量多次，注意与整体妆面衔接"]):
            if step not in steps:
                steps.append(step)
    if not steps:
        steps = ["先从底妆与定妆练起，再叠加眼妆与唇妆", "每步少量多次，做完照镜子再决定是否加强"]
    return {
        "id": tutorial.get("id", ""),
        "title": tutorial.get("title") or f"{style_name} 通用跟练步骤",
        "duration": tutorial.get("duration") or f"约 {max(3, len(steps) * 2)} 分钟",
        "focus": focus,
        "steps": steps,
        "source": "blueprint",  # 步骤为平台自撰框架，非第三方文案
    }


# ---------------------------------------------------------------------------
# 六、可选：用文本段模型润色（默认关闭，缺 key 自动跳过）
# ---------------------------------------------------------------------------


def maybe_llm_summary(plan: dict[str, Any]) -> str | None:
    """可选增强：让文本段模型写一段更自然的开场白。默认关闭。

    开启条件：WEB_LLM_NARRATIVE=true 且配置了 ANTHROPIC_API_KEY。
    任何失败都返回 None，调用方回退到规则文案。
    """
    if os.getenv("WEB_LLM_NARRATIVE", "").strip().lower() != "true":
        return None
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        from ..config import TEXT
        from anthropic import Anthropic

        client = Anthropic(base_url=TEXT["base_url"])
        prompt = (
            "你是护肤顾问。用 3 句中文、口吻专业克制地总结下面的检测结果与建议，"
            "不做医学判断、不承诺效果、不用绝对化用语。\n\n"
            f"{plan_text(plan)}"
        )
        response = client.messages.create(
            model=TEXT["model"],
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
            timeout=TEXT["timeout_seconds"],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - 增强层失败不影响主流程
        LOG.warning("LLM 润色失败，回退规则文案: %s", exc)
        return None
