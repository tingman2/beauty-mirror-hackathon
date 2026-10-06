"""「妆镜」—— AI 肤质顾问 + 博主妆教匹配。

Step 5 第一层（骨架）：
  - 两段式模型架构：视觉段（检测可见问题） + 文本段（方案/拆解/审核）
  - 第三方内容 API + 本地 mock 兜底（demo 不白屏）
  - 三候选视觉 provider：qwen_vl / doubao_vision / glm_4v（+ mock 离线）
"""

__version__ = "0.1.0"
