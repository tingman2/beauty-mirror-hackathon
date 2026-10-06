# 妆镜 · Beauty Mirror

黑客松项目：肌肤观察、护理建议、美妆风格分区与博主妆教跟练。

## 本地启动

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r products/beauty_mirror/requirements.txt
cd products
python -m beauty_mirror.web.server --open
```

访问 http://localhost:8765/ 。无模型凭证时可使用 `--offline` 演示。
模型配置请参考 `products/beauty_mirror/.env.example`，复制成 `.env` 后在本机填写。

## 页面

- 首页：亚洲人像、虹彩流光、弧形画廊、三排妆教分区、连续 3D 旋转展厅。
- 工作台：`/static/workbench.html`，照片上传与三道肤感问卷。
- 妆教分类：`/static/category.html?style=all`，五类风格的博主、缓存作品与搜索筛选。
- 报告与跟练：`/report?sid=…`、`/follow?sid=…`。

更多能力、来源与限制见 `products/beauty_mirror/README.md`。
本仓库不包含本地密钥、虚拟环境或用户上传照片。
