# 「妆镜」beauty_mirror — Step 5 骨架层 + Web 原型

> 来自 `docs/prd-beauty-mirror.md` 的 Step 5。骨架（可运行闭环）与
> **产品化 Web 界面（传图页 / 报告页 / 跟练页）** 已交付；
> **稳定性与可观测性（隐私确认 / 超时重试 / 访问日志 / 可用性探测）已补齐**，视觉准确率评测待补。

一句话：素颜照 + 3 道问卷 → 看得见的皮肤问题 + 肤质结论 → 护肤/美妆方案 → 按风格拉博主妆教 → 跟练。

## Web 界面（产品化）

评委可在浏览器里走完全流程，无需命令行：

```sh
cd products
python -m beauty_mirror.web.server --open          # 自动开浏览器，默认 :8765
python -m beauty_mirror.web.server --offline       # 全离线：视觉走 mock，现场最稳
python -m beauty_mirror.web.server --provider qwen_vl
```

或直接双击 **`启动妆镜Web.command`**（Mac，自动建环境 + 装依赖 + 开浏览器）。

> 🎤 **现场演示与评委问答**：见 [`docs/demo-script-beauty-mirror.md`](../../docs/demo-script-beauty-mirror.md)（演示脚本 + 已知限制与依据 + 预设 Q&A + 应急预案）。

三个页面：

| 页面 | 路由 | 做什么 |
|---|---|---|
| 传图页 | `/` | 拖拽上传素颜照 → 后台并行预热视觉 → 回答 3 道问卷 |
| 报告页 | `/report?sid=` | 可见问题（带置信度）+ 肤质结论 + 护肤方案 + 美妆要点 + 合规自查 |
| 跟练页 | `/follow?sid=` | 5 个风格标签 → 博主卡片（跳转原平台）→ 妆教分步拆解 → **前置镜头跟练模式** |

### 妆教跟练（B：陪练型）

跟练页分成两块，不再把“看博主”和“练”混在一起：

| 区块 | 内容 |
|---|---|
| **平台跟练脚本** | 平台编排的结构化步骤（非博主原文）+「开始跟练」 |
| **博主作品（风格参考）** | 真实博主卡片（封面/作者）+「用作参考」设为目标妆效 +「去原平台」 |

全屏跟练（**原博主妆教图 / 前置镜头**；生成后额外多一栏 **AI 灵感图**，1–3 栏自适应）：

- 左：**原博主妆教图**（所选博主封面，标注“版权归原作者”）
- 右：**前置镜头**（`getUserMedia`，按需开启；被拒/无设备则退化）
- 分步大字 + `第 n / N 步` + 计时；键盘 `←/→` 翻步、`Esc` 退出；画面**不上传、不录制**
- `getUserMedia` 需 **localhost 或 HTTPS**；局域网 IP + http 下会降级为纯步骤

> **AR 实时试妆（B 方案）已试做并取消**：用 MediaPipe 本地人脸网格 + 妆层叠加实现过，但**妆层对位/浓度不佳（实测“像鬼”）**，已移除（含 22MB 本地模型）。
> 结论：**自研 AR 妆容层要调到位成本很高**，不如生成图；若要做实时试妆，建议接**专业美妆 SDK**（美图 / YouCam）。

> 图片都走 **服务端代理 `/api/img`**（小红书图床对浏览器时好时坏）→ 服务端拓一次 + 本地缓存，白名单限定域名（防 SSRF）。

**跟练完成弹窗**：走完最后一步弹出「跟练完成 🎉」+ 用时 + **社群二维码**。
二维码把图片放到 **`web/static/community_qr.png`**（或 `.jpg`）即自动显示；没有则显示“待上传”占位，不白屏。跟进页的「加入社群」卡也用同一张图。

**「生成我的带妆效果图」（AI，辅助，仅作风格灵感）**：点按钮 → 二次确认 → **先用视觉模型把博主封面“读”成一段妆容描述（只取妆，不取脸）**，再用 `qwen-image-edit-plus` 在**你的单张照片**上按文字上妆（约 10–20s）。
- **依据什么**：① 你的照片（唯一人脸）；② **博主封面的妆容描述**（文字）；③ 风格关键词 + 平台教程步骤；④ 你的肤质与可见问题
- **为什么不直接传参考图**：实测把**两张人脸**一起喂会**身份混叠**（脸变、头发变长）——改为“只读妆容描述”，身份不漂移
- **实测**：`wanx2.1-imageedit(description_edit)` 几乎不上妆；`qwen-image-edit-plus` 能真上妆；**单图 + 妆容描述**能“跟随参考妆且保身份”（Wikimedia 公共版权人像验证）
- **风格配方**：5 个风格各有一套「底妆/眉/眼/腮红/唇/修容」配方（`data/style_tags.yaml`），以“**妆面配方（严格执行）**”写进提示词 → 风格还原度明显提升（实测「欧美浓颜」真的出了上挑眼线 + 截断眼影）
- **一次 3 张候选 + 模型初筛**：并发生成 3 张 → 用一个视觉模型当评委，打 **风格 / 身份 / 自然** 三项分并给出短评与推荐 → 你挑一张
  - **评分定位**：这是**偏好初筛，不是准确率**（对错要用标注集，好看只能比偏好）
- **隐私**：`base_image_url` 走 **base64**，**不把照片传到公网**；结果图只展示、不下载不落盘
- **降级**：无权限/超时/图太小（长边<512）→ 提示并回退到博主封面，不白屏
- **标注**：结果标“AI 生成，非真实照片，仅供风格参考”
- 博主封面统一走 **缩略参数**（实测原图 8MB → 缩略 106KB，解决加载不出来）

> 内容说明：方案 B 已清除第三方**正文**，卡片自带分步为空 → 步骤为**平台自撰框架**（非博主原文）；博主**封面图**保留作“参考妆效”，**原视频只能跳原平台、不能内嵌**（无转授权）。

> 痘印/毛孔未检出时，报告页会出现「**拍两颊特写补测**」入口。

HTTP 接口：`POST /api/upload` `POST /api/report` `POST /api/cards` `POST /api/tutorial`
`GET /api/report` `GET /api/styles` `GET /api/qr` `GET /api/health` `GET /api/status`。

### 稳定性与可观测性（本轮补齐）

| 能力 | 实现 | 位置 |
|---|---|---|
| 上传前隐私确认 | 前端勾选框门禁 + **服务端二次校验**（未同意返回 400） | `index.html` + `server.py::_post_upload` |
| 请求超时 + 自动重试 | 前端 `api()`：GET 2 次 / POST 1 次指数退避；仅重试网络/超时/429/5xx，4xx 直接报错 | `static/app.js` |
| 视觉预热不无限阻塞 | `thread.join(timeout=45s)`，超时降级返回并**显著标注** | `tools.py::detect_visible_issues` |
| 视觉调用可重试 | `VISION_MAX_ATTEMPTS`（默认 1；重试会放大超时，默认按 PRD 结论关掉） | `providers/vision.py` |
| 失败提示与重试入口 | Toast + 内联错误块 + 「重试」按钮（报告页 / 跟练页） | `static/app.js` |
| 结构化访问日志 | 只记方法 / 路由 / 状态 / 耗时；**不记请求体、密钥、照片内容** | `server.py::_access_log` |
| 第三方库日志降噪 | `httpx / httpcore / urllib3` 压到 WARNING（防 DEBUG 泄漏 Authorization 头） | `server.py::main` |
| 可用性探测 | `GET /api/status`：视觉段（配置）/ Apify（`users/me` 探测，不计费，5 分钟缓存）/ 内容缓存 | `web/availability.py` |
| 内容来源透明 | 卡片与跟练页标注 `实时抓取 / 本地缓存 / 预置示例(降级)` | `static/app.js` + `server.py` |
| 结论依据与不确定性 | 报告明确区分「问卷结论」与「照片估计」，照片结论带误差声明 | `web/plan.py::BASIS_NOTE` |
| 问卷可对照 | 报告逐题回显 3 个答案（`answers_display`） | `server.py` + `report.html` |

**故障注入验收（本轮）**：用假视觉端点制造「超时 / 非 JSON / 500 / 空返回」，报告均能生成
（降级为示例 + 显著提示），耗时 0.2–2.2s；上传错误路径（无隐私确认 / 坏 base64 / 不支持类型 /
超 10MB / 空数据）分别返回 400 / 413 且带可读错误。

**人工标注评测方案**：见 [`evals/vision/ANNOTATION-EVAL-PLAN.md`](evals/vision/ANNOTATION-EVAL-PLAN.md)
（评测标准 / 样本要求 / 标注流程 / 照片授权与隐私处理，**不自行收集真人照片**）。

**已核实的第三方现状**：Apify 凭证有效（账号 `pistachio_fanlight`），但**仍为 FREE 预览额度**
（`preview_limited=true`），无法全量刷新；内容走本地缓存（4 风格 × 20 条，已按方案 B 脱敏），
无缓存风格（如「日系元气」）自动降级为预置示例并明确标注。

**关键取舍**：

| 取舍 | 原因 |
|---|---|
| 纯标准库 `ThreadingHTTPServer`，零新增依赖 | 与 CLI 共用领域工具；现场换机器不折腾 |
| 方案文案走**确定性规则**（`web/plan.py`） | 文本段 key 缺失也能出完整报告，不白屏、可复现、可测 |
| 上传走 base64 JSON、不用 multipart | 避开 Python 3.13 移除 `cgi` 的坑，前端一行 `FileReader` 搞定 |
| 照片只进系统临时目录、会话结束即删 | 对齐 PRD「照片不持久化」 |
| 妆教第三方无分步时用**自撰通用框架**兜底 | 跟练页永不空，且不冒充博主原文 |
| 方案文案必须过 `compliance_check` | `tests/test_web_contract.py` 守着，写错就红 |

## 快速开始

```sh
cd products/beauty_mirror

# 离线演示 / 评测：`openai` 是懒加载的，只要根目录环境（dotenv+pyyaml）即可跑
pip install python-dotenv pyyaml        # 若已用根目录 setup.sh 装过，可跳过

# 真实视觉调用才需要装这个
pip install -r requirements.txt         # 含 openai（三候选 provider 的共用依赖）

cp .env.example .env          # 填密钥；不填也能跑离线演示

# 1) 离线全链路演示（不需要任何密钥、不会白屏）
cd .. && python -m beauty_mirror.agent --demo --image /path/to/face.jpg

# 2) 交互模式
cd .. && python -m beauty_mirror.agent

# 3) 视觉段评测（离线 smoke test）
cd .. && python -m beauty_mirror.evals.vision.run_eval --providers mock
```

## 架构（两段式）

```
照片 ─► 视觉段（OpenAI 兼容多模态，三候选）
          │  只输出"可见问题 + 置信度"，闭集 5 类
          │  qwen_vl / doubao_vision / glm_4v / mock
          ▼
       fetch_creator_cards
          │
          ├─ 1. data/content_cache.json（真实抓取副本）← demo 主力
          ├─ 2. Apify 实时抓取（仅 CONTENT_LIVE=true）
          ├─ 3. 过期缓存（宁可旧不可无）
          └─ 4. data/mock_creators.json（最后兜底）
          ▼
       文本段（agent loop 主模型，Anthropic-compatible）
          │  问卷肤质 + 视觉问题 双因子 → 方案 / 妆教拆解 / 合规自查
          ▼
       输出（含 original_url 跳转 + 社群入口）
```

> ⚠️ **为什么内容要走缓存**：Apify Actor 是**秒级到分钟级**重操作（同步接头上限 300s，
> 建 Api 文档 https://docs.apify.com/api/v2），放不进用户同步链路。
> 先 `scripts/refresh_content_cache.py` 预热，用户流程只读本地缓存——快、免费、断网可用，
> **而且内容是真实的**（比手写 mock 强得多）。

**关键取舍**：

| 取舍 | 原因 |
|---|---|
| 肤质改 3 道问卷 + **确定性规则** | 零模型成本、零幻觉、结果可复现 |
| 合规审核**用规则不用模型自审** | 不能让同一个模型批自己（`tools.compliance_check`） |
| 内容 API **永远降级不报错** | demo 现场不能白屏（`providers/content_api.py`） |
| 内容**缓存优先，不实时抓** | Actor 秒级~分钟级且有成本；缓存让 demo 既真又稳、断网可用 |
| 视觉与内容都用 **OpenAI 兼容 / 可缓存** 的适配层 | 换供应商、断网、超时都不改业务代码 |
| 视觉段**不做肤质分类**，只做可见问题检测 | 会议室光线下分类必翻车；检测更容易达标（闸门 = 召回 ≥80%） |
| 视觉调用**后台预热**，与问卷并行 | 豆包实测中位 18s；用户答问卷 6s 可并行掩盖（实测 19.1s vs 串行 25.1s） |

## 目录

```
beauty_mirror/
  agent.py                     # 主循环 + 工具注册表 + 离线演示
  config.py                    # 全部配置（env 优先，密钥外置）
  tools.py                     # 领域工具：检测/问卷/内容/合规/社群
  web/                         # Web 原型：server.py（HTTP）+ plan.py（确定性方案）+ sessions.py
  web/static/                  # 三页前端：index / report / follow（纯原生，无 CDN）
  domain/__init__.py           # 可见问题词表 + 风格标签加载
  domain/skin_type.py          # 3 道问卷 → 肤质的确定性规则
  providers/vision.py          # 视觉段：三候选 + mock
  providers/apify_client.py    # Apify Actor 调用（429 退避、成本估算）
  providers/content_api.py     # 缓存优先 → Apify → 预置兜底 + FIELD_MAP
  data/style_tags.yaml         # 5 个风格标签 × 2 个妆教（草案，等你定稿）
  data/mock_creators.json      # 5 个博主预置卡片（最后兜底）
  data/content_cache.json      # Apify 抓取的真实内容副本（预热生成，不入库）
  scripts/refresh_content_cache.py  # 预热缓存
  evals/vision/                # 视觉段召回率评测 + 80% 闸门
  evals/vision/results/        # 真实试跑结论（GLM-4V 不可用）
```

## 工具清单

| 工具 | 作用 | 依赖 |
|---|---|---|
| `detect_visible_issues` | 可见问题检测（闭集 5 类 + 置信度） | 视觉 provider |
| `get_questionnaire` | 返回 3 道肤质问卷 | 无 |
| `classify_skin_type` | 问卷答案 → 肤质（纯规则） | 无 |
| `fetch_creator_cards` | 按风格拉博主卡片 | 内容 API（失败→mock） |
| `get_tutorial_breakdown` | 取妆教分步拆解 | 会话缓存 |
| `compliance_check` | 医疗红线/绝对化/效果承诺体检 | 规则 |
| `get_community_qr` | 社群入口（形态待定） | 无 |

## 内容链路：Apify

| 项 | 实测/文档值 |
|---|---|
| 接口 | `POST /v2/acts/{actorId}/run-sync-get-dataset-items?token=…` |
| 认证 | `?token=` 或 `Authorization: Bearer` |
| 限流 | 全局 250,000 req/min；Run Actor **400 req/s**；超限 **429** → 指数退避（客户端已内置） |
| 错误体 | `{"error":{"type":"...","message":"..."}}` |
| 分页 | `limit` / `isTruncated` / `exclusiveStartKey` / `nextExclusiveStartKey` |
| 同步上限 | 300s（所以**不能**放用户链路） |
| 计费 | $0.00005 启动 + **$0.00499/条**（≈ ¥0.036/条） |
| 选定 Actor | `zen-studio~rednote-search-scraper`（6.3 万次运行，`keywords[]` 入参）—— **实测可用** |
| 备选 Actor | `socialdatax~socialdatax-xhs-data-api`（17 万次，`operation=search_notes`）—— 已接入，但免费预览额度先耗尽 |
| 抖音 | `zen-studio~douyin-profile-scraper`（已登记，待启用） |
| 全量预热成本 | 5 标签 × 20 条 = 100 条 ≈ **$0.50（¥3.6）** |
| 已实测花费 | ≈ **$0.45**（80 条真实卡片 + 探测） |

### ❗ 实测发现的硬限制：FREE 计划只有预览额度

Apify 账号实测为 **FREE 计划**（`maxMonthlyUsageUsd: 10`）。两个小红书 actor 都带预览阀：

- SocialDataX：抓了 ~60 条后开始**持续返回 0 条**（run 状态仍 SUCCEEDED，`usageUsd` 仅 $0.00005）
- zen-studio：返回体里直接写了 `free_preview_notice`: **"limited to 2 results from the first keyword, upgrade for full search"**

**结论**：要稳定拿到 5 标签 × 20 条，需要**升级 Apify 套餐或充值**。当前已用 4 个标签 × 20 条真实数据烤入缓存，demo 够用。

### 多 actor 适配（已完成）

不同 actor 的入参与字段完全不同，已收敛到 `providers/content_api.py` 的 `ActorDialect`：

| 方言 | 入参 | 关键字段 |
|---|---|---|
| zen-studio | `keywords[]` / `maxResults` / `sortType` | `author.nickname`、`engagement.liked_count`、`images[0].url` |
| SocialDataX | `operation` / `keyword` / `max_items` | `author_name`、`like_count`、`cover_image_url` |

**换 actor 只改 `.env` 的 `APIFY_ACTOR_ID`**，业务代码零改动。未注册的 actor 会用通用字段映射兜底。

> 防坑：预热脚本内置**防覆盖保护**——新抓条数少于已有缓存时不会覆盖（否则 2 条预览数据会冲掉 20 条完整数据），除非显式 `--force`。

预热：

```sh
cd products
python -m beauty_mirror.scripts.refresh_content_cache --dry-run   # 先看花多少钱
python -m beauty_mirror.scripts.refresh_content_cache             # 真拉
```

## 待接入（等你给料）

1. **全量预热（升级 Apify 后）** → `APIFY_TOKEN` 已配置，但账号**仍为 FREE 预览额度**（2026-10-06 复查
   `GET /v2/users/me` 返回 `plan=FREE`、`maxMonthlyUsageUsd=10`）。升级后先跑 `--dry-run` 再全量预热，
   跑完自动校准 `FIELD_MAP`。
2. **视觉段 key 已到位**：Qwen-VL（DashScope）实测 **1.6s**（豆包中位 18s 超标），已设为默认 provider。
   真实皮肤准确率仍需用**真人素颜照**验证（现无真实样张，合成图会被 Qwen 正确判为「非真实皮肤照片」）。
3. **风格标签定稿** → 替换 `data/style_tags.yaml`，代码不动。
4. **群二维码形态** → `tools.get_community_qr` 接企业微信活码（静态码 7 天失效）。
5. **评测样本 50 张** → 按 `evals/vision/README.md` 标注规范补 `fixtures/`。

## ⚠️ 已核实的风险

| 风险 | 证据 | 影响 |
|---|---|---|
| GLM-4V 会拿"图太暗"回避，或被逗后编造一模一样的结果 | 7 张亮照 5 张被判太暗；逐项强制后 7 张返回同一答案 | **不得用 GLM** |
| 国产多模态会**拦截真实人像** | 一张 Wikimedia 人像返回智谱 `code 1301` 内容审核 | 必须有换模型/重试降级 |
| 豆包：旧 key 无权限、新 key 才行 | 旧 key 调 `doubao-seed-evolving` 返 403；新 key 返 200 | 按账号开权限，不是换模型名 |
| 豆包唯一可用模型 **延迟超标** | `doubao-seed-evolving` 中位 **18s**，P95 目标为 15s | 已实现**视觉预热**（见下）缓解；根治需 Qwen-VL |
| SDK 默认重试把超时放了 3 倍 | `timeout=30` 实测跑到 **91s** | 已设 `VISION_SDK_MAX_RETRIES=0` |
| Apify FREE 计划只有**预览额度** | `free_preview_notice` + 抓 ~60 条后持续返回 0；**2026-10-06 复查 `users/me` 仍为 `plan=FREE`** | 未升级前内容走缓存/示例，已在 UI 与日志明确标注 |
| 抓取内容的**转授权与展示条款** | Apify 是第三方抓取，非官方授权 | ⚠️ **卡合同**：没转授权就只能放跳转链，不存文案 |
| **Qwen-VL 在真人照片上漏检严重** | 1 张真实素颜照（616×687）：真值 T区出油+两颊痘印，模型**均未检出**，且**间歇误报泛红**（同一图 3 次结果不一致） | 单张不可靠；已补“问卷 vs 照片不一致”与“检测局限”提示；需 50 张标注集定论 |

## 尚未做

- **已产品化**：Web 传图页 / 报告页 / 跟练页（`web/`）✅
- **已补稳定性**：隐私确认（前后端双校）、超时重试、失败提示与重试按钮、结构化访问日志、
  可用性探测（`/api/status`）、预热超时降级、内容来源透明 ✅
- 待补：**真实照片的视觉准确率验证（需 50 张标注样张）**、日志落盘/轮转、用户档案、
  指标采集（跟练完成率 / D1·D7 留存 / 入群率）、Apify 升级后的 5 风格全量预热。

## 自检

```sh
cd products
python -m unittest beauty_mirror.tests.test_offline_contract -v   # 13 条离线契约
python -m unittest beauty_mirror.tests.test_web_contract -v       # 15 条 Web 契约（含合规/可用性/超时降级）
python -m unittest beauty_mirror.tests.test_web_http -v           # 5 条真实 HTTP 集成（含隐私门禁）
python -m beauty_mirror.agent --demo --offline                    # 全链路不白屏
python -m beauty_mirror.web.server --open                         # Web 界面（默认 :8765）
curl -s http://127.0.0.1:8765/api/status | python -m json.tool     # 可用性（视觉/Apify/缓存）
python -m beauty_mirror.scripts.refresh_content_cache --dry-run   # 看预热花费
```
