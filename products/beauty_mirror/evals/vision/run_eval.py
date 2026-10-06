"""视觉段评测：可见问题召回率对比（Qwen-VL / 豆包 Vision / GLM-4V / mock）。

按 PRD 第三轮修订的验收口径：
  - 只测「可见问题召回率」，**不测肤质分类**（肤质已改为问卷规则）；
  - 闸门：micro recall ≥ 0.80 才算通过。

用法：
    cd products
    python -m beauty_mirror.evals.vision.run_eval --manifest beauty_mirror/evals/vision/manifest.example.jsonl
    python -m beauty_mirror.evals.vision.run_eval --providers mock            # 离线 smoke test
    python -m beauty_mirror.evals.vision.run_eval --providers qwen_vl,doubao_vision,glm_4v
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import VISION
from ...domain import ISSUE_KEYS
from ...providers import get_vision_provider, list_vision_providers

GATE = 0.80


@dataclass
class Sample:
    sample_id: str
    image: Path
    expected: set[str]
    notes: str = ""


@dataclass
class ProviderReport:
    provider: str
    total: int = 0
    skipped: int = 0
    tp: int = 0
    expected_total: int = 0
    predicted_total: int = 0
    per_sample_recall: list[float] = field(default_factory=list)
    per_issue_hit: dict[str, int] = field(default_factory=lambda: {k: 0 for k in ISSUE_KEYS})
    per_issue_total: dict[str, int] = field(default_factory=lambda: {k: 0 for k in ISSUE_KEYS})
    latencies: list[int] = field(default_factory=list)
    degraded: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def micro_recall(self) -> float:
        return self.tp / self.expected_total if self.expected_total else 0.0

    @property
    def macro_recall(self) -> float:
        return sum(self.per_sample_recall) / len(self.per_sample_recall) if self.per_sample_recall else 0.0

    @property
    def precision(self) -> float:
        return self.tp / self.predicted_total if self.predicted_total else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0.0

    @property
    def passed(self) -> bool:
        return self.micro_recall >= GATE


def load_manifest(path: Path) -> list[Sample]:
    samples: list[Sample] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"manifest 第 {line_no} 行不是合法 JSON：{exc}") from exc
        image = Path(raw["image"])
        if not image.is_absolute():
            image = (path.parent / image).resolve()
        expected = {k for k in (raw.get("expected_issues") or []) if k in ISSUE_KEYS}
        unknown = set(raw.get("expected_issues") or []) - set(ISSUE_KEYS)
        if unknown:
            raise SystemExit(
                f"manifest 第 {line_no} 行有未知问题 key: {sorted(unknown)}；"
                f"可用：{list(ISSUE_KEYS)}"
            )
        samples.append(
            Sample(
                sample_id=str(raw.get("id", f"line{line_no}")),
                image=image,
                expected=expected,
                notes=str(raw.get("notes", "")),
            )
        )
    return samples


def evaluate_provider(provider_name: str, samples: list[Sample], threshold: float) -> ProviderReport:
    report = ProviderReport(provider=provider_name)
    try:
        provider = get_vision_provider(provider_name)
    except KeyError as exc:
        report.errors.append(str(exc))
        return report

    for sample in samples:
        # mock provider 不需要真实图片文件（用于离线 smoke test）
        if provider_name != "mock" and not sample.image.exists():
            report.skipped += 1
            report.errors.append(f"{sample.sample_id}: 图片不存在 {sample.image}")
            continue

        result = provider.analyze(sample.image)
        if result.degraded and not result.detected:
            report.degraded += 1
            report.errors.append(f"{sample.sample_id}: {result.error}")

        predicted = {
            item["type"] for item in result.detected if item["confidence"] >= threshold
        }
        hit = sample.expected & predicted

        report.total += 1
        report.tp += len(hit)
        report.expected_total += len(sample.expected)
        report.predicted_total += len(predicted)
        report.latencies.append(result.latency_ms)
        if sample.expected:
            report.per_sample_recall.append(len(hit) / len(sample.expected))
        for key in sample.expected:
            report.per_issue_total[key] += 1
        for key in hit:
            report.per_issue_hit[key] += 1
    return report


def render_markdown(reports: list[ProviderReport], threshold: float, manifest: Path) -> str:
    lines = [
        "# 视觉段评测报告（可见问题召回率）",
        "",
        f"- manifest：`{manifest}`",
        f"- 置信度阈值：{threshold}",
        f"- 闸门：micro recall ≥ {GATE:.2f}",
        "",
        "| Provider | 样本 | 跳过 | micro Recall | macro Recall | Precision | 平均耗时 | 降级 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        verdict = "✅ PASS" if report.passed else "❌ FAIL"
        lines.append(
            f"| {report.provider} | {report.total} | {report.skipped} | "
            f"{report.micro_recall:.3f} | {report.macro_recall:.3f} | "
            f"{report.precision:.3f} | {report.avg_latency_ms:.0f}ms | "
            f"{report.degraded} | {verdict} |"
        )

    lines += ["", "## 分问题召回", "", "| 问题 | " + " | ".join(r.provider for r in reports) + " |"]
    lines.append("|---|" + "---|" * len(reports))
    for key in ISSUE_KEYS:
        cells = []
        for report in reports:
            total = report.per_issue_total[key]
            cells.append(f"{report.per_issue_hit[key]}/{total}" if total else "—")
        lines.append(f"| {key} | " + " | ".join(cells) + " |")

    lines += ["", "## 结论", ""]
    winners = [r.provider for r in reports if r.passed]
    if winners:
        best = max(reports, key=lambda r: (r.passed, r.micro_recall, -r.avg_latency_ms))
        lines.append(f"- 通过闸门：{', '.join(winners)}")
        lines.append(f"- 建议采用：**{best.provider}**（recall {best.micro_recall:.3f}，耗时 {best.avg_latency_ms:.0f}ms）")
    else:
        lines.append("- ⚠️ 无 provider 通过 0.80 闸门：先扩样本 / 调视觉提示词，再决定是否放宽阈值。")

    for report in reports:
        if report.errors:
            lines += ["", f"<details><summary>{report.provider} 的异常记录（{len(report.errors)}）</summary>", ""]
            lines += [f"- {err}" for err in report.errors[:20]]
            lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="视觉段可见问题召回率评测")
    parser.add_argument(
        "--manifest",
        default=str(Path(__file__).resolve().parent / "manifest.example.jsonl"),
    )
    parser.add_argument("--providers", default=",".join(list_vision_providers()))
    parser.add_argument("--threshold", type=float, default=VISION["min_confidence"])
    parser.add_argument("--out", default="", help="报告输出路径（md）；同时写同名 .json")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

    manifest = Path(args.manifest).expanduser().resolve()
    if not manifest.exists():
        raise SystemExit(f"manifest 不存在：{manifest}")

    samples = load_manifest(manifest)
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    reports = [evaluate_provider(p, samples, args.threshold) for p in providers]

    payload: dict[str, Any] = {
        "manifest": str(manifest),
        "threshold": args.threshold,
        "gate": GATE,
        "samples": len(samples),
        "providers": [
            {
                "provider": r.provider,
                "samples": r.total,
                "skipped": r.skipped,
                "micro_recall": round(r.micro_recall, 4),
                "macro_recall": round(r.macro_recall, 4),
                "precision": round(r.precision, 4),
                "avg_latency_ms": round(r.avg_latency_ms, 1),
                "degraded": r.degraded,
                "passed": r.passed,
            }
            for r in reports
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        report_md = render_markdown(reports, args.threshold, manifest)
        print(report_md)
        if args.out:
            out = Path(args.out).expanduser()
            out.write_text(report_md, encoding="utf-8")
            out.with_suffix(".json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"报告已写入：{out} 与 {out.with_suffix('.json')}")

    return 0 if any(r.passed for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
