# 视觉段评测说明

按 PRD 第三轮修订：**只测可见问题召回率，不测肤质分类**（肤质已改为 3 道问卷规则）。

> 📋 **后续人工标注评测方案**（评测标准 / 标注流程 / 照片授权与隐私处理）见
> [`ANNOTATION-EVAL-PLAN.md`](ANNOTATION-EVAL-PLAN.md)。本文只讲离线评测框架怎么跑。
> ⚠️ 当前 7 张 Wikimedia 试跑、合成图、mock smoke 都**不构成准确率结论**。

## 闸门

- 指标：**micro recall ≥ 0.80**
- 置信度阈值：默认 `VISION_MIN_CONFIDENCE`（0.35）
- 没有 provider 过线 → 先扩样本、调提示词，再考虑是否放宽阈值

## Manifest 格式（JSONL，一行一个样本）

```json
{"id": "s01", "image": "fixtures/foo.png", "expected_issues": ["acne_marks", "redness"], "notes": "自然光/室内光"}
```

`expected_issues` 只能取闭集：`acne_marks` / `redness` / `t_zone_oil` / `pores` / `dark_circles`。
`image` 相对路径按 manifest 所在目录解析。

## 跑评测

```sh
cd products
# 离线 smoke test（不需要图片与密钥，读 fixtures/*.mock.json）
python -m beauty_mirror.evals.vision.run_eval --providers mock

# 三家候选对比
python -m beauty_mirror.evals.vision.run_eval \
  --providers qwen_vl,doubao_vision,glm_4v \
  --out beauty_mirror/evals/vision/report.md
```

输出：Markdown 报告（含分问题召回表 + 采用建议）与同名 `.json`。

## 扩到 50 张的标注规范

1. **授权与隐私**：只用本人同意 / 已授权的照片；评测集**不入库、不进训练**，跑完即删或加密存放。
2. **光线分层**：自然光、室内顶光、暖光、屏幕光各 10 张左右——会议室光线下翻车是已知风险，要专门覆盖。
3. **每张判定的问题**：只标"肉眼可见"的问题；有争议的不标（宁缺勿滥）。
4. **标注粒度**：粗标到"问题类别"即可，不需要框位置；本评测只看召回，不看重定位。
5. **留 10% 难样本**：浓妆、重滤镜、逆光、多人合影——用来暴露真实失败率。

## 已知盲区（别被高分骗）

- 浓妆 / 滤镜下视觉段天然不可靠 → 产品侧要靠"问卷肤质"顶上，并对用户明示置信度。
- 小样本（<3）的指标没有统计意义，示例 manifest 只用来验证流程。
