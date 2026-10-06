/* 妆镜 Web 原型 · 共享前端逻辑 */
(function () {
  "use strict";

  // ---------- 工具 ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function el(tag, attrs = {}, html = "") {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      else if (k === "dataset") Object.assign(node.dataset, v);
      else node.setAttribute(k, v);
    });
    if (html) node.innerHTML = html;
    return node;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // 带超时与重试的请求：网络抖动 / 超时 / 429 / 5xx 自动退避重试；4xx 直接报错
  async function api(path, body, opts = {}) {
    const method = body ? "POST" : "GET";
    const timeout = opts.timeout || (method === "POST" ? 90000 : 20000);
    const maxRetries = opts.retries == null ? (method === "POST" ? 1 : 2) : opts.retries;
    let lastError = null;

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      if (attempt > 0) await sleep(600 * Math.pow(2, attempt - 1));
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout);
      try {
        const res = await fetch(path, {
          method,
          headers: body ? { "Content-Type": "application/json" } : undefined,
          body: body ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        });
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (res.ok) return data;
        const message = (data && data.error) || ("请求失败（HTTP " + res.status + "）");
        const err = new Error(message);
        err.status = res.status;
        if (!RETRYABLE_STATUS.has(res.status)) { err.noRetry = true; throw err; }
        lastError = err;
      } catch (e) {
        if (e && e.noRetry) throw e;
        if (e && e.name === "AbortError") {
          lastError = new Error("请求超时（" + Math.round(timeout / 1000) + " 秒），请检查网络后重试");
        } else {
          lastError = e || new Error("网络异常");
        }
        lastError.retryable = true;
      } finally {
        clearTimeout(timer);
      }
    }
    throw lastError || new Error("请求失败");
  }

  function toast(message, ms = 2600) {
    let node = $(".toast");
    if (!node) {
      node = el("div", { class: "toast" });
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(node._t);
    node._t = setTimeout(() => node.classList.remove("show"), ms);
  }

  function sidFromUrl() {
    const sid = new URLSearchParams(location.search).get("sid");
    if (sid) { try { localStorage.setItem("bm_sid", sid); } catch (e) {} }
    return sid || (function () { try { return localStorage.getItem("bm_sid"); } catch (e) { return null; } })();
  }

  function fmtSize(bytes) {
    if (!bytes) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  function severityLabel(value) {
    return { mild: "轻度", moderate: "中度", severe: "明显" }[value] || "轻度";
  }

  function platformLabel(p) {
    return { xiaohongshu: "小红书", douyin: "抖音" }[p] || (p || "第三方平台");
  }

  // 外部图片统一走服务端代理（图床对浏览器时好时坏）
  function imgUrl(u) {
    if (!u) return u;
    if (u.startsWith("/") || u.startsWith("data:")) return u;
    return "/api/img?url=" + encodeURIComponent(u);
  }

  // 社群二维码：把图片放到 web/static/community_qr.png（或 .jpg）即自动显示；没有则占位
  function qrImgHtml() {
    return '<div class="qr-box">' +
      '<img src="/static/community_qr.png" alt="社群二维码" ' +
        'onerror="if(!this.dataset.tried){this.dataset.tried=1;this.src=\'/static/community_qr.jpg\';}' +
        'else{this.style.display=\'none\';var n=this.nextElementSibling;if(n)n.style.display=\'flex\';}">' +
      '<div class="qr-missing" style="display:none">二维码待上传<br>' +
        '<span class="src-note">把图片放到<br><code>web/static/community_qr.png</code></span></div>' +
      "</div>";
  }

  // 跟练完成弹窗（末尾放社群二维码）
  function showCompletion(info) {
    const secs = Math.max(0, Math.floor(info.seconds || 0));
    const mm = String(Math.floor(secs / 60)).padStart(2, "0");
    const ss = String(secs % 60).padStart(2, "0");
    const mask = el("div", { class: "done-mask", id: "done-mask" });
    mask.innerHTML =
      '<div class="done">' +
        "<h3>跟练完成 🎉</h3>" +
        '<p class="sub">' + esc(info.title || "本次跟练") + " · 用时 " + mm + ":" + ss + "</p>" +
        '<p class="done-hint">扫码加入社群，晒成果 / 提问 / 领取妆教清单</p>' +
        qrImgHtml() +
        '<div style="margin-top:16px"><button class="btn block" id="done-close" type="button">继续</button></div>' +
      "</div>";
    document.body.appendChild(mask);
    $("#done-close").addEventListener("click", () => mask.remove());
    mask.addEventListener("click", (e) => { if (e.target.id === "done-mask") mask.remove(); });
  }

  function setBusy(button, busy, textWhenBusy) {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.innerHTML;
      button.disabled = true;
      button.innerHTML = '<span class="spinner"></span> ' + (textWhenBusy || "处理中…");
    } else {
      button.disabled = false;
      if (button.dataset.label) button.innerHTML = button.dataset.label;
    }
  }

  const SOURCE_LABEL = {
    api: "实时抓取",
    cache: "本地缓存",
    cache_stale: "本地缓存（已过期）",
    mock: "预置示例",
    empty: "暂无内容",
  };
  function sourceLabel(source) { return SOURCE_LABEL[source] || (source || "未知来源"); }

  // 首页可用性卡：视觉段 / 第三方内容(Apify) / 内容缓存
  async function loadStatus(box) {
    if (!box) return;
    box.innerHTML = '<div class="loading-box" style="padding:20px"><span class="spinner dark"></span><span>检测可用性…</span></div>';
    try {
      const status = await api("/api/status", null, { timeout: 15000 });
      box.innerHTML = statusHtml(status);
    } catch (err) {
      box.innerHTML = '<div class="callout warn"><span class="ic">⚠️</span><div>状态检测失败：' + esc(err.message) + "</div></div>";
    }
  }

  function statusHtml(status) {
    const vision = status.vision || {};
    const apify = status.apify || {};
    const cache = status.cache || {};
    const dot = (ok) => '<span class="dot ' + (ok ? "ok" : "warn") + '"></span>';
    const styles = cache.styles || {};
    const cacheLine = Object.keys(styles).length
      ? Object.entries(styles).map(([name, n]) => esc(name) + " × " + n).join("、")
      : "无缓存";
    const apifyText = !apify.token_configured
      ? "未配置，内容使用缓存/示例"
      : (apify.preview_limited
          ? "凭证有效，但为 FREE 预览额度，无法全量刷新"
          : (apify.usable ? "凭证有效" : esc(apify.reason || "不可用")));
    return (
      '<div class="status-row">' + dot(vision.ready) + '<b>视觉段</b><span>' +
        (vision.ready ? esc(vision.label || vision.provider) + " 已就绪" : "降级为示例结果（非真实检测）") + "</span></div>" +
      '<div class="status-row">' + dot(apify.token_configured && apify.usable && !apify.preview_limited) +
        '<b>第三方内容</b><span>' + apifyText + "</span></div>" +
      '<div class="status-row">' + dot(true) + '<b>内容缓存</b><span>' + cacheLine +
        (cache.text_stripped ? "（已按方案 B 清除文案，仅留跳转链）" : "") + "</span></div>"
    );
  }

  // ---------- 传图页 ----------
  function initUploadPage() {
    let currentSid = null;
    const dropzone = $("#dropzone");
    const fileInput = $("#file-input");
    const preview = $("#preview");
    const previewImg = $("#preview-img");
    const previewName = $("#preview-name");
    const previewSize = $("#preview-size");
    const qCard = $("#questionnaire-card");
    const qBox = $("#questionnaire");
    const submit = $("#btn-generate");
    const consent = $("#consent");

    // 未勾选隐私确认前，不接收照片
    function syncConsent() {
      const ok = !!(consent && consent.checked);
      dropzone.classList.toggle("disabled", !ok);
      dropzone.setAttribute("aria-disabled", ok ? "false" : "true");
    }
    if (consent) consent.addEventListener("change", syncConsent);
    syncConsent();
    loadStatus($("#status-card"));

    function readFile(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("读取文件失败"));
        reader.readAsDataURL(file);
      });
    }

    // 读原始像素尺寸（用于报告中的“分辨率偏低→易漏检”提醒）
    function readSize(file) {
      return new Promise((resolve) => {
        const img = new Image();
        img.onload = () => resolve({ width: img.naturalWidth || 0, height: img.naturalHeight || 0 });
        img.onerror = () => resolve({ width: 0, height: 0 });
        img.src = URL.createObjectURL(file);
      });
    }

    async function handleFile(file) {
      if (!file) return;
      if (consent && !consent.checked) { toast("请先阅读并勾选隐私说明"); return; }
      if (!/^image\//.test(file.type)) { toast("请选择图片文件"); return; }
      if (file.size > 10 * 1024 * 1024) { toast("图片超过 10MB，请压缩后重试"); return; }

      previewImg.src = URL.createObjectURL(file);
      previewName.textContent = file.name;
      previewSize.textContent = fmtSize(file.size) + " · 正在上传…";
      dropzone.classList.add("hidden");
      preview.classList.remove("hidden");
      qCard.classList.add("hidden");

      try {
        const dataUrl = await readFile(file);
        const size = await readSize(file);
        const result = await api("/api/upload", {
          filename: file.name,
          mime: file.type,
          data_base64: dataUrl,
          consent: true,
          width: size.width,
          height: size.height,
        }, { timeout: 60000, retries: 1 });
        currentSid = result.sid;
        try { localStorage.setItem("bm_sid", currentSid); } catch (e) {}
        previewSize.textContent = fmtSize(file.size) + " · 已上传";
        renderQuestionnaire(result.questionnaire || []);
        qCard.classList.remove("hidden");
        qCard.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (err) {
        toast(err.message || "上传失败");
        reset();
      }
    }

    function reset() {
      currentSid = null;
      preview.classList.add("hidden");
      qCard.classList.add("hidden");
      dropzone.classList.remove("hidden");
      fileInput.value = "";
    }

    function renderQuestionnaire(questions) {
      qBox.innerHTML = "";
      questions.forEach((q) => {
        const block = el("div", { class: "q" });
        block.appendChild(el("p", { class: "q-text" }, esc(q.text)));
        const opts = el("div", { class: "opts" });
        (q.options || []).forEach((opt) => {
          const label = el("label", { class: "opt", dataset: { qid: q.id, key: opt.key } });
          const input = el("input", { type: "radio", name: q.id, value: opt.key });
          label.appendChild(input);
          label.appendChild(document.createTextNode(opt.label));
          label.addEventListener("click", () => {
            $$(".opt", opts).forEach((n) => n.classList.remove("checked"));
            label.classList.add("checked");
          });
          opts.appendChild(label);
        });
        block.appendChild(opts);
        qBox.appendChild(block);
      });
    }

    function collectAnswers() {
      const answers = {};
      $$(".opt.checked").forEach((node) => { answers[node.dataset.qid] = node.dataset.key; });
      return answers;
    }

    dropzone.addEventListener("click", () => {
      if (consent && !consent.checked) { toast("请先阅读并勾选隐私说明"); return; }
      fileInput.click();
    });
    dropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); dropzone.click(); }
    });
    dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag");
      if (e.dataTransfer.files && e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files && fileInput.files[0]) handleFile(fileInput.files[0]);
    });
    const repick = $("#btn-repick");
    if (repick) repick.addEventListener("click", reset);

    submit.addEventListener("click", async () => {
      const answers = collectAnswers();
      if (!currentSid) { toast("请先上传照片"); return; }
      if (Object.keys(answers).length < 3) { toast("请把 3 道题都选一下"); return; }

      const original = submit.innerHTML;
      submit.disabled = true;
      submit.innerHTML = '<span class="spinner"></span> 正在分析面部可见问题…';
      try {
        await api("/api/report", { sid: currentSid, answers }, { timeout: 120000, retries: 1 });
        location.href = "/report?sid=" + encodeURIComponent(currentSid);
      } catch (err) {
        toast(err.message || "生成报告失败");
        submit.disabled = false;
        submit.innerHTML = original;
      }
    });
  }

  // ---------- 报告页 ----------
  function issueBarHtml(issue) {
    const pct = Math.round((Number(issue.confidence) || 0) * 100);
    const areas = (issue.areas || []).length ? "部位：" + esc((issue.areas || []).join("、")) : "";
    return (
      '<div class="issue">' +
        '<div class="row"><span class="label">' + esc(issue.label) +
          ' <span class="chip">' + esc(severityLabel(issue.severity)) + "</span></span>" +
          '<span class="pct">置信度 ' + pct + "%</span></div>" +
        '<div class="bar"><i style="width:' + pct + '%"></i></div>' +
        (areas ? '<div class="areas">' + areas + "</div>" : "") +
      "</div>"
    );
  }

  function sectionHtml(section) {
    const tags = (section.tags || []).map((t) => '<span class="chip">' + esc(t) + "</span>").join("");
    return (
      '<div class="section">' +
        '<p class="s-title">' + esc(section.title) + "</p>" +
        '<p class="s-body">' + esc(section.advice) + "</p>" +
        (tags ? '<div class="tags">' + tags + "</div>" : "") +
      "</div>"
    );
  }

  function initReportPage() {
    const sid = sidFromUrl();
    const loading = $("#loading");
    const content = $("#content");
    const errorBox = $("#error-box");

    if (!sid) { location.replace("/"); return; }

    api("/api/report?sid=" + encodeURIComponent(sid), null, { timeout: 30000, retries: 2 })
      .then((report) => {
        loading.classList.add("hidden");
        if (!report || report.ok === false) {
          showError(report && report.error ? report.error : "报告未就绪", true);
          return;
        }
        if (report.ready === false) {
          showError("报告尚未生成，请回到首页重新填写。", false);
          return;
        }
        render(report);
        content.classList.remove("hidden");
      })
      .catch((err) => { loading.classList.add("hidden"); showError(err.message || "加载失败", true); });

    function showError(message, retryable) {
      errorBox.innerHTML =
        '<div class="callout danger"><span class="ic">⚠️</span><div>' + esc(message) +
        '<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">' +
          (retryable ? '<button class="btn ghost" type="button" id="btn-retry">重试</button>' : "") +
          '<a class="btn ghost" href="/">← 回到首页重新开始</a>' +
        "</div></div></div>";
      errorBox.classList.remove("hidden");
      const retry = $("#btn-retry");
      if (retry) retry.addEventListener("click", () => location.reload());
    }

    function render(report) {
      const vision = report.vision || {};
      const skin = report.skin || {};
      const plan = report.plan || {};
      const compliance = report.compliance || {};
      const issues = vision.issues || [];

      // 头部
      $("#face").src = report.image_url + "?t=" + Date.now();
      $("#headline").textContent = plan.headline || "检测完成";
      $("#skin-summary").textContent = plan.skin_summary || "";
      const skinChips = (skin.labels || []).map((l) => '<span class="chip primary">' + esc(l) + "</span>").join(" ");
      $("#skin-chips").innerHTML = skinChips;

      // 结论依据与不确定性（明确区分问卷 vs 照片估计）
      $("#basis").textContent = plan.basis || "";
      const answers = skin.answers_display || [];
      $("#answers-list").innerHTML = answers.length
        ? answers.map((a) => '<div class="ans-row"><span class="ans-q">' + esc(a.question) +
            '</span><span class="ans-a">' + esc(a.answer) + "</span></div>").join("")
        : '<div class="empty">未记录问卷答案</div>';

      // 视觉状态提示
      const notices = [];
      if (vision.needs_retake) {
        notices.push(['danger', "📷", vision.hint || "照片质量不足，建议在自然光下正对镜头重拍一张无妆素颜照。"]);
      }
      if (vision.fallback_used) {
        notices.push(['warn', "⚠️", vision.hint || "真实视觉服务不可用，已用本地示例结果垫底，请勿当作真实检测结论。"]);
      } else if (vision.degraded) {
        notices.push(['warn', "⚠️", vision.hint || "视觉检测已降级，结果以问卷结论为主。"]);
      }
      $("#vision-notices").innerHTML = notices
        .map((n) => '<div class="callout ' + n[0] + '" style="margin-bottom:10px"><span class="ic">' + n[1] + '</span><div>' + esc(n[2]) + "</div></div>")
        .join("");

      // 可见问题
      const provider = vision.provider || "-";
      $("#issue-provider").textContent = "视觉 provider：" + provider;
      $("#issues").innerHTML = issues.length
        ? issues.map(issueBarHtml).join("")
        : '<div class="empty">本次照片未检出明显可见问题。若与自身感受不符，可在自然光下重拍再试。</div>';

      // 护肤方案
      $("#plan-sections").innerHTML = (plan.sections || []).map(sectionHtml).join("") || '<div class="empty">暂无</div>';

      // 针对性护理
      $("#plan-target").innerHTML = (plan.target || []).length
        ? (plan.target || []).map(sectionHtml).join("")
        : '<div class="empty">本次没有检出需要单独处理的可见问题。</div>';

      // 美妆要点
      $("#makeup").innerHTML = (plan.makeup || []).length
        ? "<ul>" + (plan.makeup || []).map((t) => "<li>" + esc(t) + "</li>").join("") + "</ul>"
        : '<div class="empty">暂无</div>';

      // 冲突 / 提示
      const conflictBox = $("#conflicts");
      if ((plan.conflicts || []).length) {
        conflictBox.innerHTML = plan.conflicts
          .map((c) => '<div class="callout info" style="margin-bottom:10px"><span class="ic">🔎</span><div>' + esc(c) + "</div></div>")
          .join("");
        conflictBox.classList.remove("hidden");
      }

      // 检测局限（如实告知，避免把“未检出”当“不存在”）
      const limitations = plan.limitations || [];
      $("#limitations").innerHTML = limitations.length
        ? limitations.map((t) => '<div class="callout warn" style="margin-bottom:10px"><span class="ic">⚠️</span><div>' + esc(t) + "</div></div>").join("")
        : '<div class="empty">无</div>';

      // 痘印/毛孔未检出 → 引导补拍特写
      const detected = issues.map((i) => i.type);
      const retake = $("#retake-card");
      if (retake && (!detected.includes("acne_marks") || !detected.includes("pores"))) {
        retake.style.display = "";
      }

      // 合规自查
      const status = compliance.status || "unknown";
      $("#compliance").innerHTML =
        '<span class="badge ' + (status === "pass" ? "pass" : "rework") + '">' +
          (status === "pass" ? "✅ 合规自查通过" : "⚠️ 需人工复核") +
        "</span>" +
        '<p class="sub" style="margin:10px 0 0">' +
          "规则审核为第一道闸（医疗红线 / 绝对化用语 / 效果承诺），" +
          "命中 " + ((compliance.hits || []).length) + " 处。" +
        "</p>";

      $("#disclaimer").textContent = plan.disclaimer || "";

      // 跟练入口
      $("#btn-follow").href = "/follow?sid=" + encodeURIComponent(report.sid);
    }
  }

  // ---------- 跟练模式（前置镜头 + 分步 + 计时）----------
  function startFollowMode(info) {
    const steps = (info.steps || []).filter((s) => s && String(s).trim());
    if (!steps.length) { toast("这个妆教还没有分步数据"); return; }
    const existing = $("#follow-mode");
    if (existing) existing.remove();

    const overlay = el("div", { class: "follow-mode", id: "follow-mode" });
    overlay.innerHTML =
      '<div class="fm-top">' +
        '<div class="fm-meta"><span class="fm-progress" id="fm-progress"></span>' +
          '<span class="fm-title">' + esc(info.title || "跟练") + "</span></div>" +
        '<button class="fm-close" id="fm-close" type="button" aria-label="退出跟练">✕</button>' +
      "</div>" +
      '<div class="fm-stage">' +
        '<div class="fm-panes">' +
          (info.bloggerUrl ? '<figure class="fm-pane fm-blogger"><img id="fm-blogger-img" alt="原博主妆教"><figcaption id="fm-blogger-cap"></figcaption></figure>' : "") +
          (info.referenceUrl ? '<figure class="fm-pane fm-ai"><img id="fm-ai-img" alt="AI 效果"><figcaption>AI 效果（你 · 仅供风格参考）</figcaption></figure>' : "") +
          '<div class="fm-pane fm-cam">' +
            '<video id="fm-video" playsinline autoplay muted></video>' +
            '<div class="fm-cam-off" id="fm-cam-off">' +
              '<div class="fm-cam-icon">📷</div>' +
              '<div id="fm-cam-msg">点下方按钮开启前置镜头（画面仅本机显示，不上传、不录制）</div>' +
              '<button class="btn" id="fm-cam-toggle" type="button">开启前置镜头</button>' +
            "</div>" +
          "</div>" +
        "</div>" +
        '<div class="fm-timer" id="fm-timer">00:00</div>' +
      "</div>" +
      '<div class="fm-bottom">' +
        '<button class="fm-nav" id="fm-prev" type="button">上一步</button>' +
        '<div class="fm-step"><div class="fm-step-no" id="fm-step-no"></div>' +
          '<div class="fm-step-text" id="fm-step-text"></div></div>' +
        '<button class="fm-nav primary" id="fm-next" type="button">下一步</button>' +
      "</div>" +
      '<div class="fm-foot">' +
        '<button class="fm-link" id="fm-switch" type="button">切换前后镜头</button>' +
        (info.originalUrl ? '<a class="fm-link" href="' + esc(info.originalUrl) + '" target="_blank" rel="noopener">去原平台看原视频 ↗</a>' : "") +
        '<span class="fm-privacy">画面仅在本机显示，不上传、不录制</span>' +
      "</div>";
    document.body.appendChild(overlay);

    // 原博主妆教图 + AI 效果图（如果有）
    if (info.bloggerUrl) {
      const bImg = $("#fm-blogger-img");
      if (bImg) {
        bImg.src = imgUrl(info.bloggerUrl);
        bImg.onerror = () => { bImg.style.display = "none"; $("#fm-blogger-cap").textContent = "原博主图加载失败"; };
      }
      $("#fm-blogger-cap").textContent = "原博主妆教" + (info.bloggerCreator ? "：" + info.bloggerCreator : "（封面，版权归原作者）");
    }
    if (info.referenceUrl) {
      const aImg = $("#fm-ai-img");
      if (aImg) aImg.src = imgUrl(info.referenceUrl);
    }

    let index = 0;
    let facing = "user";
    let stream = null;
    let timer = null;
    const startedAt = Date.now();
    const video = $("#fm-video");
    const camOff = $("#fm-cam-off");

    function renderStep() {
      $("#fm-progress").textContent = "第 " + (index + 1) + " / " + steps.length + " 步";
      $("#fm-step-no").textContent = "STEP " + (index + 1);
      $("#fm-step-text").textContent = steps[index];
      $("#fm-prev").disabled = index === 0;
      $("#fm-next").textContent = index === steps.length - 1 ? "完成 ✓" : "下一步";
    }

    function tick() {
      const s = Math.floor((Date.now() - startedAt) / 1000);
      $("#fm-timer").textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    }

    function stopStream() {
      if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    }

    async function startCamera() {
      try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          throw new Error("此浏览器不支持摄像头");
        }
        stopStream();
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: facing, width: { ideal: 1280 } }, audio: false,
        });
        video.srcObject = stream;
        video.classList.toggle("mirror", facing === "user");
        await video.play().catch(() => {});
        camOff.classList.add("hidden");
      } catch (err) {
        camOff.classList.remove("hidden");
        $("#fm-cam-msg").textContent =
          "摄像头不可用：" + (err && err.message ? err.message : "未授权或无设备") + "（仍可按步骤跟练）";
      }
    }

    function go(delta) {
      const next = index + delta;
      if (next < 0) return;
      if (next >= steps.length) {
        const secs = Math.floor((Date.now() - startedAt) / 1000);
        close();
        showCompletion({ title: info.title, seconds: secs });
        return;
      }
      index = next;
      renderStep();
    }

    function onKey(e) {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
    }

    function close() {
      stopStream();
      clearInterval(timer);
      document.removeEventListener("keydown", onKey);
      overlay.remove();
    }

    $("#fm-close").addEventListener("click", close);
    $("#fm-prev").addEventListener("click", () => go(-1));
    $("#fm-next").addEventListener("click", () => go(1));
    $("#fm-switch").addEventListener("click", () => {
      facing = facing === "user" ? "environment" : "user";
      startCamera();
    });
    $("#fm-cam-toggle").addEventListener("click", startCamera);
    document.addEventListener("keydown", onKey);

    renderStep();
    tick();
    timer = setInterval(tick, 1000);
    // 摄像头按需开启：不自动弹权限，用户点「开启前置镜头」再调 getUserMedia
  }

  // ---------- 跟练页 ----------
  function initFollowPage() {
    const sid = sidFromUrl();
    if (!sid) { location.replace("/"); return; }
    let currentStyle = "";
    let currentStyleName = "";
    let currentCards = [];
    let reference = null;
    let selectedCover = null; // 所选博主封面（作为生成参考图）

    loadStyles();
    loadQr();
    loadSourceStatus();

    async function loadSourceStatus() {
      const box = $("#source-status");
      if (!box) return;
      try {
        const status = await api("/api/status", null, { timeout: 15000 });
        const apify = status.apify || {};
        const cache = status.cache || {};
        const apifyText = !apify.token_configured
          ? "未配置（内容使用缓存/示例）"
          : (apify.preview_limited ? "FREE 预览额度，不能全量刷新" : (apify.usable ? "可用" : "不可用"));
        const bits = [
          "Apify：" + apifyText,
          "缓存：" + (cache.total_cards || 0) + " 条 / " + Object.keys(cache.styles || {}).length + " 个风格",
        ];
        if (cache.text_stripped) bits.push("正文已按方案 B 清除，仅留跳转链");
        box.innerHTML = '<div class="callout info"><span class="ic">📡</span><div>' + bits.map(esc).join(" · ") + "</div></div>";
      } catch (err) {
        box.innerHTML = '<div class="callout warn"><span class="ic">⚠️</span><div>来源状态获取失败：' + esc(err.message) + "</div></div>";
      }
    }

    async function loadStyles() {
      try {
        const data = await api("/api/styles", null, { timeout: 20000 });
        const box = $("#style-chips");
        box.innerHTML = "";
        (data.styles || []).forEach((style) => {
          const chip = el("button", { class: "style-chip", type: "button" }, esc(style.name));
          chip.dataset.id = style.id;
          chip.addEventListener("click", () => {
            $$(".style-chip").forEach((n) => n.classList.remove("active"));
            chip.classList.add("active");
            loadCards(style.id || style.name, style.name);
          });
          box.appendChild(chip);
        });
        const first = (data.styles || [])[0];
        if (first) {
          $(".style-chip", box).classList.add("active");
          loadCards(first.id || first.name, first.name);
        }
      } catch (err) {
        $("#cards").innerHTML = '<div class="empty">风格标签加载失败：' + esc(err.message) + "</div>";
      }
    }

    async function loadCards(styleId, styleName) {
      currentStyle = styleId || styleName;
      currentStyleName = styleName || "";
      $("#cards").innerHTML = '<div class="loading-box"><span class="spinner dark"></span><span>正在获取「' + esc(styleName) + '」的博主内容…</span></div>';
      $("#cards-notice").classList.add("hidden");
      try {
        const data = await api("/api/cards", { sid: sid, style_tag: currentStyle, limit: 6 }, { timeout: 30000, retries: 2 });
        currentCards = data.cards || [];
        renderCards(data);
        renderScripts(data);
      } catch (err) {
        $("#cards").innerHTML = '<div class="empty">加载失败：' + esc(err.message) +
          '<div style="margin-top:10px"><button class="btn ghost" type="button" id="btn-cards-retry">重试</button></div></div>';
        const retry = $("#btn-cards-retry");
        if (retry) retry.addEventListener("click", () => loadCards(currentStyle, styleName));
      }
    }

    function renderCards(data) {
      const notice = $("#cards-notice");
      const chips = ['<span class="chip">来源：' + esc(sourceLabel(data.source)) + "</span>"];
      if (data.degraded) chips.push('<span class="chip amber">降级</span>');
      notice.innerHTML = '<div class="callout ' + (data.degraded ? "warn" : "info") + '"><span class="ic">' +
        (data.degraded ? "⚠️" : "🗂️") + '</span><div>' +
        (data.notice ? esc(data.notice) + " " : "") + chips.join(" ") + "</div></div>";
      notice.classList.remove("hidden");
      const box = $("#cards");
      if (!data.cards || !data.cards.length) {
        box.innerHTML = '<div class="empty">这个风格暂时没有内容，换一个试试。</div>';
        return;
      }
      box.innerHTML = "";
      data.cards.forEach((card) => {
        const node = el("div", { class: "creator-card" });
        const cover = card.cover_url
          ? '<img class="cover" src="' + esc(imgUrl(card.cover_url)) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
          : '<div class="cover"></div>';
        const avatar = card.creator_avatar ? '<img src="' + esc(imgUrl(card.creator_avatar)) + '" alt="">' : "";
        const like = card.like_count ? '<span class="chip">👍 ' + esc(card.like_count) + "</span>" : "";
        const stripped = card.text_stripped ? '<span class="chip amber">仅跳转原平台</span>' : "";
        const summary = card.summary
          ? '<div class="summary">' + esc(card.summary) + "</div>"
          : '<div class="summary src-note">为避免转授权问题，本页不转载正文，请前往原平台查看。</div>';
        node.innerHTML =
          cover +
          '<div class="body">' +
            '<div class="creator">' + avatar + "<span>" + esc(card.creator) + "</span>" +
              '<span class="chip">' + esc(platformLabel(card.platform)) + "</span></div>" +
            '<div class="title">' + esc(card.title || "（无标题）") + "</div>" +
            summary +
            '<div class="foot">' +
              '<div style="display:flex;gap:6px;flex-wrap:wrap">' + like + stripped + "</div>" +
              '<a class="jump" href="' + esc(card.original_url) + '" target="_blank" rel="noopener">去原平台 ↗</a>' +
            "</div>" +
            '<button class="btn ghost" type="button" data-ref="' + esc(card.id) + '">用作参考</button>' +
          "</div>";
        node.addEventListener("click", (e) => {
          if (e.target.closest("a")) return;
          if (e.target.closest("button[data-ref]") || e.target.closest(".cover")) setReference(card);
        });
        box.appendChild(node);
      });
      // 默认参考：第一张有封面的卡片
      if (!reference) {
        const withCover = data.cards.find((c) => c.cover_url) || data.cards[0];
        if (withCover) setReference(withCover, true);
      }
      highlightReference();
    }

    function setReference(card, silent) {
      if (!card) return;
      reference = {
        id: String(card.id),
        url: card.cover_url || "",
        creator: card.creator || "博主",
        originalUrl: card.original_url || "",
        kind: "cover",
        label: "",
      };
      selectedCover = { url: card.cover_url || "", creator: card.creator || "博主" };
      highlightReference();
      updateScriptRef();
      if (!silent) toast("已设为参考妆效：" + (card.creator || "博主"));
    }

    function highlightReference() {
      $$("#cards .creator-card").forEach((n) => {
        const btn = n.querySelector("button[data-ref]");
        const active = !!(reference && btn && btn.dataset.ref === reference.id);
        n.classList.toggle("is-ref", active);
        if (btn) btn.textContent = active ? "✓ 当前参考" : "用作参考";
      });
    }

    function renderScripts(data) {
      const tutorials = data.style_tutorials || [];
      const box = $("#scripts");
      if (!tutorials.length) {
        box.innerHTML = '<div class="empty">这个风格暂时没有跟练脚本。</div>';
        return;
      }
      box.innerHTML = "";
      tutorials.forEach((t) => {
        const row = el("div", { class: "script-row" });
        row.innerHTML =
          '<div class="script-main"><div class="script-title">' + esc(t.title || "跟练脚本") + "</div>" +
          '<div class="script-focus">' + (t.focus || []).map((f) => '<span class="chip">' + esc(f) + "</span>").join(" ") + "</div></div>" +
          '<button class="btn" type="button" data-script="' + esc(t.id) + '">开始跟练</button>';
        row.querySelector("button[data-script]").addEventListener("click", () => startScript(t));
        box.appendChild(row);
      });
      box.appendChild(el("div", { class: "script-ref", id: "script-ref" }));
      updateScriptRef();
    }

    function updateScriptRef() {
      const line = $("#script-ref");
      if (!line) return;
      const parts = ['<button class="btn ghost" type="button" id="btn-gen-look">✨ 生成我的带妆效果图</button>'];
      if (reference && reference.url) {
        const label = reference.kind === "generated"
          ? (reference.label || "AI 生成的你的带妆效果图（仅供风格参考）")
          : ("参考：" + (reference.creator || "博主") + "（封面，版权归原作者，仅风格参考）");
        parts.push('<img class="script-ref-thumb" src="' + esc(imgUrl(reference.url)) + '" alt="">');
        parts.push('<span class="script-ref-label">' + esc(label) + "</span>");
      } else {
        parts.push('<span class="src-note">未选择参考妆效。</span>');
      }
      line.innerHTML = parts.join(" ");
      const btn = $("#btn-gen-look");
      if (btn) btn.addEventListener("click", generateLook);
    }

    function renderCandidatePicker(data) {
      const line = $("#script-ref");
      if (!line) return;
      const cards = (data.candidates || []).map((c) => {
        const sc = c.score || {};
        const bits = [];
        if (sc.style != null) bits.push("风格 " + sc.style);
        if (sc.identity != null) bits.push("身份 " + sc.identity);
        if (sc.natural != null) bits.push("自然 " + sc.natural);
        const rec = data.recommended === c.index ? '<span class="cand-badge">推荐</span>' : "";
        return '<button class="cand" type="button" data-cand="' + c.index + '">' +
          '<img src="' + esc(imgUrl(c.image_url)) + '" alt="">' + rec +
          '<span class="cand-score">' + esc(bits.join(" · ") || "未评分") + "</span>" +
          (c.note ? '<span class="cand-note">' + esc(c.note) + "</span>" : "") +
          "</button>";
      }).join("");
      line.innerHTML = '<div class="cand-title">挑一张作为参考妆效（AI 生成，仅供参考）：</div>' +
        '<div class="cand-row">' + cards + "</div>" +
        '<button class="btn ghost" type="button" id="btn-gen-again">重新生成</button>';
      $$(".cand", line).forEach((btn) => {
        btn.addEventListener("click", () => {
          const idx = Number(btn.dataset.cand);
          const c = (data.candidates || []).find((x) => x.index === idx) || (data.candidates || [])[0];
          if (!c) return;
          reference = {
            kind: "generated",
            url: c.image_url,
            creator: reference && reference.creator,
            label: "AI 生成的你的带妆效果图（仅供风格参考）",
            originalUrl: (reference && reference.originalUrl) || "",
          };
          toast("已选为参考妆效");
          updateScriptRef();
        });
      });
      const again = $("#btn-gen-again");
      if (again) again.addEventListener("click", generateLook);
    }

    async function generateLook() {
      const btn = $("#btn-gen-look");
      const agreed = window.confirm(
        "生成会用你上传的照片，并读取所选博主封面的妆容描述（只读妆容、不复制对方的脸），" +
        "交给通义万相生成一张 AI 上妆效果图（仅用于本页参考，不公开分享）。\n" +
        "结果由 AI 生成，非真实照片，可能不准确。照片需长边 ≥512px。继续？"
      );
      if (!agreed) return;
      const original = btn ? btn.innerHTML : "";
      if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 生成中…（约 10-30 秒）'; }
      try {
        const data = await api("/api/beautify", {
          sid: sid, style_tag: currentStyle,
          reference_url: selectedCover ? selectedCover.url : "",
        }, { timeout: 150000, retries: 0 });
        if (!data || !data.ok) {
          toast((data && data.error) || "生成失败，已保留博主封面");
        } else if (data.candidates && data.candidates.length) {
          renderCandidatePicker(data);
          toast(data.used_reference ? "已按博主妆容生成 3 张候选，挑一张" : "已生成 3 张候选，挑一张");
        } else {
          reference = {
            kind: "generated",
            url: data.image_url,
            creator: reference && reference.creator,
            label: data.label || "AI 生成的你的带妆效果图（仅供风格参考）",
            originalUrl: (reference && reference.originalUrl) || "",
          };
          updateScriptRef();
          toast("已生成你的带妆效果图");
        }
      } catch (err) {
        toast("生成失败：" + (err.message || ""));
      } finally {
        const b = $("#btn-gen-look");
        if (b) { b.disabled = false; b.innerHTML = original; }
      }
    }

    async function startScript(tut) {
      try {
        const data = await api("/api/tutorial", {
          sid: sid, tutorial_id: tut.id, style_tag: currentStyle,
        }, { timeout: 30000, retries: 1 });
        const t = data.tutorial || {};
        startFollowMode({
          title: t.title || tut.title,
          steps: t.steps || [],
          creator: reference && reference.creator,
          bloggerUrl: selectedCover ? selectedCover.url : "",
          bloggerCreator: selectedCover ? selectedCover.creator : "",
          referenceUrl: (reference && reference.kind === "generated") ? reference.url : "",
          originalUrl: (reference && reference.originalUrl) || "",
        });
      } catch (err) {
        toast("获取跟练脚本失败：" + err.message);
      }
    }

    $("#modal-close").addEventListener("click", () => $("#modal").classList.remove("show"));
    $("#modal").addEventListener("click", (e) => {
      if (e.target.id === "modal") $("#modal").classList.remove("show");
    });

    async function loadQr() {
      const box = $("#qr");
      if (!box) return;
      let note = "扫码加入社群，交流护肤与跟练问题。";
      try {
        const data = await api("/api/qr", null, { timeout: 15000 });
        if (data && data.note) note = data.note;
      } catch (err) { /* 用默认文案 */ }
      box.innerHTML = qrImgHtml() + '<p class="src-note" style="text-align:center;margin-top:10px">' + esc(note) + "</p>";
    }
  }

  // ---------- 分派 ----------
  document.addEventListener("DOMContentLoaded", () => {
    const page = document.body.dataset.page;
    if (page === "upload") initUploadPage();
    else if (page === "report") initReportPage();
    else if (page === "follow") initFollowPage();
  });
})();
