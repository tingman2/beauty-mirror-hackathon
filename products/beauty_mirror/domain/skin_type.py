"""肤质判定：3 道极简问卷 + 确定性规则。

设计取舍（来自 PRD 第三轮修订）：
  - 肤质分类从视觉段**移除**（会议室光线下太容易翻车，且评分线难达）。
  - 改成让用户自己答 3 道题，规则**确定性**计算，零模型成本、零幻觉、结果可复现。

规则是刻意做"粗"的：demo 用，不追求医学精度。
"""

from __future__ import annotations

from typing import Any

# 每题：id / 文案 / 选项。选项的 score 直接累加到对应维度。
QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "tight",
        "text": "洗完脸什么都不涂，5 分钟后会觉得紧绷吗？",
        "options": [
            {"key": "yes", "label": "会，明显紧绷", "score": {"dry": 2}},
            {"key": "sometimes", "label": "偶尔会", "score": {"dry": 1}},
            {"key": "no", "label": "不会", "score": {}},
        ],
    },
    {
        "id": "redness",
        "text": "换季或换新护肤品时，容易泛红 / 刺痛吗？",
        "options": [
            {"key": "often", "label": "经常", "score": {"sensitive": 2}},
            {"key": "sometimes", "label": "偶尔", "score": {"sensitive": 1}},
            {"key": "no", "label": "基本不会", "score": {}},
        ],
    },
    {
        "id": "oil",
        "text": "中午前后，T 区（额头 / 鼻子）会明显出油吗？",
        "options": [
            {"key": "yes", "label": "会，明显泛油", "score": {"oily": 2}},
            {"key": "sometimes", "label": "有一点", "score": {"oily": 1}},
            {"key": "no", "label": "基本不出油", "score": {}},
        ],
    },
]

_OPTION_INDEX = {
    q["id"]: {opt["key"]: opt for opt in q["options"]} for q in QUESTIONS
}
QUESTION_IDS = tuple(q["id"] for q in QUESTIONS)


def is_complete(answers: dict[str, str]) -> bool:
    return all(answers.get(qid) in _OPTION_INDEX[qid] for qid in QUESTION_IDS)


def classify(answers: dict[str, str]) -> dict[str, Any]:
    """把 3 个答案映射成肤质结论。

    answers 形如 {"tight": "yes", "redness": "no", "oil": "sometimes"}。
    未知 key 一律忽略（宽容解析，不抛异常）。
    """
    scores = {"dry": 0, "oily": 0, "sensitive": 0}
    for qid in QUESTION_IDS:
        opt = _OPTION_INDEX[qid].get(answers.get(qid, ""))
        if not opt:
            continue
        for dim, value in (opt.get("score") or {}).items():
            scores[dim] = scores.get(dim, 0) + int(value)

    dry, oily, sensitive = scores["dry"], scores["oily"], scores["sensitive"]

    if dry >= 1 and oily >= 1:
        base = "混合性偏干" if dry > oily else ("混合性偏油" if oily > dry else "混合性")
    elif dry >= 1:
        base = "干性"
    elif oily >= 1:
        base = "油性"
    else:
        base = "中性"

    labels = [base]
    if sensitive >= 2:
        labels.append("敏感")
    elif sensitive == 1:
        labels.append("轻度敏感")

    answered = sum(1 for qid in QUESTION_IDS if answers.get(qid) in _OPTION_INDEX[qid])
    return {
        "skin_type": base,
        "sensitive": sensitive >= 2,
        "labels": labels,
        "scores": scores,
        "answered": answered,
        "confidence": "high" if answered == len(QUESTION_IDS) else "low",
        "note": "问卷规则判定，非医学诊断；敏感倾向明显建议就医确认。",
    }


def question_sheet() -> dict[str, Any]:
    """给 CLI/Web 渲染的问卷结构。"""
    return {"questions": QUESTIONS}
