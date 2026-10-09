// 基于质谱峰的蛋白质匹配软件 V1.0
// Copyright (c) 2026 张葛阳
// 本软件为独立开发，未使用第三方开源代码
/* ============================================================
   基于质谱峰的蛋白质匹配软件 - 本地 Web UI 前端逻辑
   后端: web_app.py (Flask)
   ============================================================ */
"use strict";

(function () {
  // ---------- 全局状态 ----------
  var META = null;
  var POLL_TIMER = null;
  var LAST_LOG_TOTAL = 0;
  var LAST_STATUS = null;
  var RESULT_LOADED = {};          // step-key -> true（已渲染过）
  var LAST_OUTPUT_DIR = null;      // 最近一次运行的输出目录
  var RUN_HISTORY_KEY = "dsh_protein_run_history";
  var DIR_PICK = { target: null, current: "", history: [] };

  // ---------- DOM ----------
  var $ = function (id) { return document.getElementById(id); };

  // ---------- 工具 ----------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  // 通用消息弹窗：遮罩 + 标题 + 正文 + 操作按钮。
  // 按钮 click 回调签名 (btn, close)：返回 false 表示点击后不关闭弹窗。
  function showMessageModal(opts) {
    opts = opts || {};
    var overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9999;display:flex;align-items:center;justify-content:center;";
    var box = document.createElement("div");
    box.style.cssText = "position:relative;background:#fff;border-radius:10px;max-width:540px;width:92%;padding:18px 20px;box-shadow:0 6px 24px rgba(0,0,0,.25);box-sizing:border-box;";
    var closeBtn = document.createElement("button");
    closeBtn.textContent = "×";
    closeBtn.title = "关闭";
    closeBtn.style.cssText = "position:absolute;top:10px;right:12px;border:none;background:transparent;font-size:18px;cursor:pointer;color:#999;";
    var title = document.createElement("div");
    title.style.cssText = "font-size:15px;font-weight:600;margin-bottom:10px;padding-right:24px;";
    title.textContent = opts.title || "提示";
    var body = document.createElement("div");
    body.style.cssText = "white-space:pre-wrap;font-size:13px;line-height:1.7;color:#333;word-break:break-word;";
    body.textContent = opts.body || "";
    var actions = document.createElement("div");
    actions.style.cssText = "margin-top:16px;text-align:right;";
    function close() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }
    var btns = opts.buttons && opts.buttons.length ? opts.buttons : [{ label: "知道了", primary: true }];
    btns.forEach(function (b) {
      var btn = document.createElement("button");
      btn.className = "btn mini" + (b.primary ? " primary" : "");
      btn.textContent = b.label;
      btn.style.marginLeft = "8px";
      btn.addEventListener("click", function () {
        if (b.click) { var keep = b.click(btn, close); if (keep === false) return; }
        close();
      });
      actions.appendChild(btn);
    });
    closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    box.appendChild(closeBtn);
    box.appendChild(title);
    box.appendChild(body);
    box.appendChild(actions);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  }
  // 复制文本到剪贴板；成功后在按钮上短暂显示「已复制」
  function copyText(text, btn) {
    var done = function () {
      if (btn) {
        btn.textContent = "已复制";
        btn.disabled = true;
        setTimeout(function () { btn.textContent = "复制安装命令"; btn.disabled = false; }, 1500);
      }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () { legacyCopy(text); done(); });
    } else { legacyCopy(text); done(); }
  }
  function legacyCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }
  function fmtNum(v) {
    if (v === "" || v == null) return "—";
    var n = Number(v);
    if (isNaN(n)) return String(v);
    if (Number.isInteger(n)) return n.toLocaleString("en-US");
    return String(Number(n.toFixed(4)));
  }
  function nowTime() {
    var d = new Date();
    function p(x) { return (x < 10 ? "0" : "") + x; }
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  // ---------- 导航 ----------
  var PAGE_NAMES = {
    pipeline: "肽段匹配", step2: "蛋白爬虫", step3: "分段匹配",
    step4: "数据分析", runs: "运行记录", monitor: "运行监控",
    anomaly: "异常检测", "result-analysis": "结果分析",
    "ai-code": "代码审查", qa: "问答", "dsh-config": "DSH 模型配置",
    settings: "系统设置"
  };
  var navItems = document.querySelectorAll(".nav-item");
  navItems.forEach(function (el) {
    el.addEventListener("click", function () {
      var page = el.getAttribute("data-page");
      if (page === "step2") { showPage("pipeline"); focusStep(2); return; }
      if (page === "step3") { showPage("pipeline"); focusStep(3); return; }
      if (page === "step4") { showPage("pipeline"); focusStep(4); return; }
      showPage(page);
    });
  });
  ["go-pipeline-step2", "go-pipeline-step3", "go-pipeline-step4"].forEach(function (id, idx) {
    var b = $(id);
    if (b) b.addEventListener("click", function () { showPage("pipeline"); focusStep(idx + 2); });
  });
  var goMon = $("go-monitor-pipeline");
  if (goMon) goMon.addEventListener("click", function () { showPage("pipeline"); });

  function showPage(page) {
    navItems.forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-page") === page);
    });
    document.querySelectorAll(".page").forEach(function (p) { p.classList.add("hidden"); });
    var target = $("page-" + page);
    if (target) target.classList.remove("hidden");
    if (page === "runs") refreshRuns();
    if (page === "monitor") refreshMonitor();
    if (page === "anomaly") refreshAnomaly();
    if (page === "result-analysis") refreshResultAnalysis();
    if (page === "dsh-config") refreshDshConfig();
    if (page === "settings") loadSettings();
  }
  function focusStep(n) {
    var cards = document.querySelectorAll(".step-card");
    if (cards.length >= n) {
      cards[n - 1].scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      pushAnalysisMsg("", "Step " + n + " 产物尚未生成，请先在主工作区运行四步流水线。");
    }
  }

  // ---------- 顶栏/状态 ----------
  function setTopStatus(online) {
    var st = $("top-status");
    st.classList.toggle("offline", !online);
    $("top-status-text").textContent = online ? "本地服务运行中" : "服务离线";
  }
  function setSb(text) { $("sb-ready").textContent = text; }
  function setSbLast(text) { $("sb-last").textContent = "最近任务：" + text; }

  // ---------- 分析消息区 ----------
  function pushAnalysisMsg(metaText, text, isErr) {
    var list = $("analysis-msg");
    if (!list) return;
    var div = document.createElement("div");
    div.className = "msg";
    var m = document.createElement("div");
    m.className = "meta" + (isErr ? " error" : "");
    m.textContent = metaText || nowTime();
    div.appendChild(m);
    div.appendChild(document.createTextNode(text));
    list.appendChild(div);
    var scroller = list.closest(".panel") || list;
    scroller.scrollTop = scroller.scrollHeight;
    return div;
  }

  // ---------- 初始化 meta ----------
  function initSelect(id, values, value) {
    var sel = $(id);
    if (!sel) return;
    sel.innerHTML = "";
    (values || []).forEach(function (v) {
      var o = document.createElement("option");
      o.value = v; o.textContent = v;
      sel.appendChild(o);
    });
    if (value != null) sel.value = String(value);
  }

  function loadMeta() {
    return fetch("/api/meta").then(function (r) { return r.json(); }).then(function (m) {
      META = m;
      var d = m.defaults || {};
      initSelect("enzyme", m.enzymes, d.enzyme);
      initSelect("cys_reagent", m.cys_options, d.cys_reagent);
      initSelect("ion_type", m.ion_options, d.ion_type);
      initSelect("mass_type", m.mass_types, d.mass_type);
      initSelect("sort_by", m.sort_options, d.sort_by);
      initSelect("min_mass", m.min_mass_options, d.min_mass);
      initSelect("max_mass", m.max_mass_options, d.max_mass);
      if (d.missed_cleavages != null) $("missed_cleavages").value = String(d.missed_cleavages);
      $("cys_acrylamide").checked = !!d.cys_acrylamide;
      $("met_oxidized").checked = !!d.met_oxidized;
      $("show_ptm").checked = d.show_ptm !== false;
      $("show_conflict").checked = !!d.show_conflict;
      $("show_variant").checked = !!d.show_variant;
      $("show_varsplice").checked = !!d.show_varsplice;
      if ($("tolerance_value")) $("tolerance_value").value = m.tolerance;
      if ($("tolerance_unit")) $("tolerance_unit").value = m.tolerance_unit || "ppm";
      // 输出目录默认指向工程内 runtime/output（用户可自行修改）
      if ($("output_folder") && !$("output_folder").value && m.default_output_dir) {
        $("output_folder").value = m.default_output_dir;
      }
      loadHistoryPreview();
    });
  }

  // ---------- 参数收集 ----------
  function collectParams() {
    var val = function (id) { var el = $(id); return el ? el.value : ""; };
    var chk = function (id) { var el = $(id); return el ? el.checked : false; };
    var p = {
      reference_file: val("reference_file").trim(),
      protein_folder: val("protein_folder").trim(),
      peptide_folder: val("peptide_folder").trim(),
      output_folder: val("output_folder").trim(),
      tolerance: val("tolerance_value"),
      tolerance_unit: val("tolerance_unit"),
      expasy: {
        enzyme: val("enzyme"),
        missed_cleavages: val("missed_cleavages"),
        cys_reagent: val("cys_reagent"),
        cys_acrylamide: chk("cys_acrylamide"),
        cys_treatment: val("cys_reagent"),
        met_oxidized: chk("met_oxidized"),
        ion_type: val("ion_type"),
        mass_type: val("mass_type"),
        min_mass: val("min_mass"),
        max_mass: val("max_mass"),
        sort_by: val("sort_by"),
        show_ptm: chk("show_ptm"),
        show_conflict: chk("show_conflict"),
        show_variant: chk("show_variant"),
        show_varsplice: chk("show_varsplice")
      }
    };
    return p;
  }

  // ---------- 开始/停止 ----------
  function setRunningUI(running) {
    $("btn-run").disabled = running;
    $("btn-stop").disabled = !running;
    if ($("btn-ai")) $("btn-ai").disabled = running;
    if ($("btn-ai-2")) $("btn-ai-2").disabled = running;
    if ($("btn-ai-code")) $("btn-ai-code").disabled = running;
    if ($("btn-qa-ai")) $("btn-qa-ai").disabled = running;
  }

  function clearResults() {
    var area = $("result-area");
    area.innerHTML = "";
    RESULT_LOADED = {};
    LAST_LOG_TOTAL = 0;
    $("log-box").innerHTML = "";
    var pfill = $("progress-fill");
    pfill.style.width = "0%";
    $("step-label").textContent = "启动中…";
  }

  function startRun() {
    var p = collectParams();
    var missing = [];
    if (!p.reference_file) missing.push("参考文件");
    if (!p.protein_folder) missing.push("蛋白文件夹");
    if (!p.peptide_folder) missing.push("肽段文件夹");
    if (!p.output_folder) missing.push("输出文件夹");
    if (missing.length) {
      pushAnalysisMsg(nowTime() + " · 校验失败", "请先填写：" + missing.join("、"), true);
      return;
    }
    var tol = Number(p.tolerance);
    if (!isFinite(tol) || tol <= 0) {
      pushAnalysisMsg(nowTime() + " · 校验失败", "容差必须为正数", true);
      return;
    }
    setRunningUI(true);
    clearResults();
    pushAnalysisMsg(nowTime() + " · 任务启动", "正在启动四步流水线（Step1 蛋白初筛 → Step2 肽段爬取 → Step3 肽段匹配 → Step4 蛋白分析）…");
    fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params: p })
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (res) {
      if (!res.ok || !res.j.ok) {
        var err = (res.j && res.j.error) || "启动失败";
        setRunningUI(false);
        pushAnalysisMsg(nowTime() + " · 错误", err, true);
        return;
      }
      LAST_OUTPUT_DIR = p.output_folder;
      rememberHistory(p.output_folder);
      setSb("运行中…");
      startPolling(true);
    }).catch(function (e) {
      setRunningUI(false);
      pushAnalysisMsg(nowTime() + " · 错误", "请求失败: " + e, true);
    });
  }

  function stopRun() {
    fetch("/api/run/stop", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function () { pushAnalysisMsg(nowTime() + " · 停止", "已请求停止，正在安全中断（尽力而为）…"); })
      .catch(function () { pushAnalysisMsg(nowTime() + " · 停止", "停止请求发送失败", true); });
  }

  // ---------- 轮询状态/日志 ----------
  function startPolling(runStart) {
    if (POLL_TIMER) clearInterval(POLL_TIMER);
    var idlePolls = 0;
    var doPoll = function () {
      fetch("/api/run/status?since=" + LAST_LOG_TOTAL)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var st = data.status || {};
          appendLogs(data.logs || []);
          LAST_LOG_TOTAL = data.log_total || 0;
          renderStatus(st);
          if (data.logs && data.logs.length) idlePolls = 0;
          if (!st.running && st.current_step === 5) {
            idlePolls++;
            if (idlePolls >= 4) {
              if (POLL_TIMER) clearInterval(POLL_TIMER);
              POLL_TIMER = null;
              setRunningUI(false);
            }
          } else {
            idlePolls = 0;
          }
          if (runStart && !POLL_TIMER) {
            // restart interval if previously stopped and a new run began
          }
        })
        .catch(function (e) { console.error("poll error", e); });
    };
    doPoll();
    POLL_TIMER = setInterval(doPoll, 1000);
  }

  function appendLogs(logs) {
    var box = $("log-box");
    if (!logs || !logs.length) return;
    var firstEmpty = box.querySelector(".log-empty");
    if (firstEmpty) firstEmpty.remove();
    logs.forEach(function (item) {
      var div = document.createElement("div");
      div.className = "log-line";
      var msg = item.msg || "";
      if (/^ERROR|Traceback|Error:/.test(msg)) div.className += " log-err";
      else if (/ALL STEPS COMPLETED|成功|完成|DONE/.test(msg)) div.className += " log-ok";
      else if (/warning|WARNING|失败|无法|Skip/.test(msg)) div.className += " log-warn";
      var ts = document.createElement("span");
      ts.className = "log-ts";
      ts.textContent = "[" + (item.ts || "") + "]";
      div.appendChild(ts);
      div.appendChild(document.createTextNode(msg));
      box.appendChild(div);
    });
    box.scrollTop = box.scrollHeight;
  }

  function renderStatus(st) {
    var pct = st.progress || 0;
    $("progress-fill").style.width = pct + "%";
    var label = $("step-label");
    if (st.running) {
      label.textContent = (st.current_step ? "Step " + st.current_step + "/4 · " + st.step_name : "启动中…") + " (" + pct + "%)";
      setSb("运行中 · " + (st.step_name || "…"));
      setSbLast(st.step_name || "…");
    } else if (st.current_step === 5) {
      label.textContent = st.error && st.error !== "Stopped by user" ? "流程出错" : "流程结束";
      setSb(st.error ? "出错" : "空闲");
      if (st.error) {
        label.textContent = "流程出错: " + st.error;
      }
    }
    // 逐步加载结果卡片
    var rf = st.result_files || {};
    var order = ["step1", "step2", "step3", "step4"];
    order.forEach(function (key) {
      if (rf[key] && !RESULT_LOADED[key]) {
        RESULT_LOADED[key] = true;
        loadResultCard(key, rf[key], st.step_stats && st.step_stats[key]);
      }
    });
    // 完成汇总
    if (!st.running && st.current_step === 5 && !window._finalMsgShown) {
      window._finalMsgShown = true;
      renderCompletionSummary(st);
    }
    if (st.running) window._finalMsgShown = false;
  }

  var STEP_META = {
    step1: { title: "Step 1 · 蛋白初筛", logic: "step1", file: "protein_match_result.xlsx" },
    step2: { title: "Step 2 · 肽段爬取", logic: "step2", file: "peptide_crawl_database.xlsx" },
    step3: { title: "Step 3 · 肽段匹配", logic: "step3", file: "peptide_match_result.xlsx" },
    step4: { title: "Step 4 · 蛋白分析", logic: "step4", file: "protein_analysis_result.xlsx" }
  };

  function loadResultCard(key, path, statText) {
    var area = $("result-area");
    var meta = STEP_META[key];
    var card = document.createElement("div");
    card.className = "step-card";
    card.id = "card-" + key;
    var head = document.createElement("div");
    head.className = "step-card-head";
    var nameSpan = document.createElement("span");
    nameSpan.textContent = meta.title;
    head.appendChild(nameSpan);
    var stat = document.createElement("span");
    stat.className = "tag note";
    stat.textContent = statText || "生成中…";
    head.appendChild(stat);
    var spacer = document.createElement("span");
    spacer.className = "spacer";
    head.appendChild(spacer);
    var reloadBtn = document.createElement("button");
    reloadBtn.className = "btn mini";
    reloadBtn.textContent = "刷新";
    reloadBtn.addEventListener("click", function () { refreshCard(key); });
    head.appendChild(reloadBtn);
    card.appendChild(head);
    var body = document.createElement("div");
    body.className = "step-card-body";
    var loading = document.createElement("div");
    loading.className = "log-empty";
    loading.style.padding = "14px";
    loading.textContent = "加载表格…";
    body.appendChild(loading);
    card.appendChild(body);
    var note = document.createElement("div");
    note.className = "step-note";
    note.textContent = "数据来源: " + path + "（最多展示 200 行）";
    card.appendChild(note);
    area.appendChild(card);
    fetchTable(key, path);
  }

  function fetchTable(key, path) {
    var meta = STEP_META[key];
    fetch("/api/table?file=" + encodeURIComponent(path) + "&status_logic=" + meta.logic)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok || !data.exists) {
          var bodyEl = $("card-" + key);
          if (bodyEl) {
            var b = bodyEl.querySelector(".step-card-body");
            b.innerHTML = "";
            var d = document.createElement("div");
            d.className = "log-empty";
            d.style.padding = "14px";
            d.textContent = "表格加载失败: " + ((data && (data.error || "文件不存在")) || "未知错误");
            b.appendChild(d);
          }
          return;
        }
        renderTable(key, data);
      })
      .catch(function (e) {
        var bodyEl = $("card-" + key);
        if (bodyEl) {
          var b = bodyEl.querySelector(".step-card-body");
          b.innerHTML = "";
          var d = document.createElement("div");
          d.className = "log-empty";
          d.style.padding = "14px";
          d.textContent = "表格加载失败: " + e;
          b.appendChild(d);
        }
      });
  }

  function refreshCard(key) {
    RESULT_LOADED[key] = false;
    var path = LAST_STATUS && LAST_STATUS.result_files ? LAST_STATUS.result_files[key] : null;
    if (!path) return;
    var card = $("card-" + key);
    if (!card) return;
    var body = card.querySelector(".step-card-body");
    body.innerHTML = "";
    var loading = document.createElement("div");
    loading.className = "log-empty";
    loading.style.padding = "14px";
    loading.textContent = "刷新中…";
    body.appendChild(loading);
    fetchTable(key, path);
  }

  function renderTable(key, data) {
    var bodyEl = document.querySelector("#card-" + key + " .step-card-body");
    if (!bodyEl) return;
    bodyEl.innerHTML = "";
    var wrap = document.createElement("div");
    wrap.className = "table-wrap";
    var table = document.createElement("table");
    var cols = data.columns || [];
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    cols.forEach(function (c) {
      var th = document.createElement("th");
      th.textContent = c;
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    (data.rows || []).forEach(function (row) {
      var tr = document.createElement("tr");
      cols.forEach(function (c) {
        var td = document.createElement("td");
        if (c === "状态" && row._status) {
          var tag = document.createElement("span");
          tag.className = "tag " + (row._status_level || "note");
          tag.textContent = row._status;
          td.appendChild(tag);
        } else {
          td.textContent = fmtNum(row[c]);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    bodyEl.appendChild(wrap);
    var count = document.createElement("div");
    count.className = "step-note";
    count.textContent = "共 " + fmtNum(data.total) + " 行" + (data.truncated ? "（仅显示前 200 行，完整数据见上方 Excel 文件）" : "");
    var card = $("card-" + key);
    if (card) {
      // replace old footer note if present
      var oldNote = card.querySelector(".step-card-body + .step-note");
      if (oldNote) card.removeChild(oldNote);
      var note = document.createElement("div");
      note.className = "step-note";
      note.textContent = "数据来源: " + data.path + "。" + count.textContent;
      card.appendChild(note);
    }
  }

  function renderCompletionSummary(st) {
    var msgs = [];
    var s = st.step_stats || {};
    if (st.error && st.error !== "Stopped by user") {
      pushAnalysisMsg(nowTime() + " · 流程出错", "任务未完成：\n" + st.error, true);
      setSbLast("出错");
      return;
    }
    if (st.error === "Stopped by user") {
      pushAnalysisMsg(nowTime() + " · 已停止", "任务已被用户停止，已生成的中间结果保留在输出目录。", true);
      return;
    }
    pushAnalysisMsg(nowTime() + " · 运行完成", [
      "四步流水线全部执行完成。",
      "Step1 " + (s.step1 || "—"),
      "Step2 " + (s.step2 || "—"),
      "Step3 " + (s.step3 || "—"),
      "Step4 " + (s.step4 || "—"),
      "输出目录：" + (st.output_dir || "—")
    ].join("\n"));
    setSb("完成");
    setSbLast("运行完成 " + (st.finished_at || ""));
    // 状态条副文本
    refreshMonitor();
  }

  // ---------- 打开输出 / AI ----------
  function openOutputDir() {
    var p = collectParams();
    var dir = (LAST_OUTPUT_DIR && $("output_folder").value === LAST_OUTPUT_DIR) ? LAST_OUTPUT_DIR : $("output_folder").value;
    if (!dir) dir = LAST_OUTPUT_DIR || p.output_folder;
    if (!dir) {
      pushAnalysisMsg(nowTime() + " · 提示", "请先选择输出文件夹。", true);
      return;
    }
    fetch("/api/open_output", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_dir: dir })
    }).then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) pushAnalysisMsg(nowTime() + " · 提示", j.error || "打开失败", true);
      });
  }

  function setAiBusy(busy) {
    ["btn-ai-code", "btn-qa-ai"].forEach(function (id) {
      var b = $(id);
      if (b) b.disabled = busy;
    });
  }

  function aiAnalysis(originLabel) {
    if ($("btn-ai-code") && $("btn-ai-code").disabled) return; // 防抖：启动中忽略重复点击
    setAiBusy(true);
    pushAnalysisMsg(nowTime() + " · AI 分析", "正在拉起 DSH Web，请稍候…");
    var outDir = ($("output_folder") ? $("output_folder").value : "").trim();
    fetch("/api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_dir: outDir })
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.ok) {
          pushAnalysisMsg(nowTime() + " · AI 分析", j.message || "DSH 已启动。");
          if (j.url) {
            pushAnalysisMsg(nowTime() + " · AI 分析", "正在浏览器打开 " + j.url);
            window.open(j.url, "_blank");
          }
          return;
        }
        var msg = j.message || "无法启动 DSH";
        if (j.code === "NO_NODE") {
          pushAnalysisMsg(nowTime() + " · AI 分析", msg, true);
          showMessageModal({
            title: "缺少 Node.js",
            body: msg,
            buttons: [
              { label: "打开 Node.js 下载页", primary: true, click: function () { window.open(j.open_url || "https://nodejs.org", "_blank"); } },
              { label: "知道了" }
            ]
          });
        } else if (j.code === "NO_DSH") {
          pushAnalysisMsg(nowTime() + " · AI 分析", msg, true);
          showMessageModal({
            title: "未检测到 DSH",
            body: msg + "\n\n点击「复制安装命令」后，打开一个新的「命令提示符」窗口粘贴执行，安装完成后再点一次「AI 分析」。",
            buttons: [
              { label: "复制安装命令", click: function (btn) { copyText("npm install -g @deepseek-ai/dsh", btn); return false; } },
              { label: "打开 DSH 模型配置页", primary: true, click: function () { showPage("dsh-config"); } },
              { label: "知道了" }
            ]
          });
        } else if (j.code === "LAUNCH_FAIL") {
          pushAnalysisMsg(nowTime() + " · AI 分析", msg, true);
          var detail = j.detail ? String(j.detail).slice(-800) : "";
          showMessageModal({
            title: "DSH 启动失败",
            body: msg + (detail ? "\n\n启动日志：\n" + detail : ""),
            buttons: [{ label: "知道了", primary: true }]
          });
          if (j.detail) {
            pushAnalysisMsg(nowTime() + " · 启动日志", detail, true);
          }
        } else {
          pushAnalysisMsg(nowTime() + " · AI 分析", msg, true);
          showMessageModal({
            title: "无法启动 DSH",
            body: msg,
            buttons: [{ label: "知道了", primary: true }]
          });
        }
      })
      .catch(function (e) { pushAnalysisMsg(nowTime() + " · AI 分析", "请求失败: " + e, true); })
      .then(function () { setAiBusy(false); });
  }

  // ---------- 目录树选择器 ----------
  function openDirPicker(targetFieldId) {
    var el = $("dir-modal");
    el.classList.remove("hidden");
    var isFileMode = (targetFieldId === "reference_file");
    $("dir-modal-title").textContent = (isFileMode ? "选择参考文件" : "选择文件夹") + " · 将回填至：" +
      ($("dir-modal").getAttribute("data-target-label") || "");
    DIR_PICK.target = targetFieldId;
    DIR_PICK.current = "";
    DIR_PICK.history = [];
    DIR_PICK.mode = isFileMode ? "file" : "folder";
    renderDirBreadcrumb();
    browseDir("");
  }
  function browseDir(path) {
    var list = $("dir-list");
    list.innerHTML = "<div class='dir-loading'>加载中…</div>";
    var url = "/api/browse?path=" + encodeURIComponent(path) +
      (DIR_PICK.mode === "file" ? "&files=1" : "");
    fetch(url).then(function (r) { return r.json(); })
      .then(function (data) {
        DIR_PICK.current = data.path || "";
        if (data.is_drive_view) DIR_PICK.history = [];
        renderDirList(data);
        renderDirBreadcrumb();
      })
      .catch(function (e) {
        list.innerHTML = "<div class='dir-loading'>加载失败: " + esc(e) + "</div>";
      });
  }
  function renderDirList(data) {
    var list = $("dir-list");
    list.innerHTML = "";
    if (data.is_drive_view) {
      var tip = document.createElement("div");
      tip.className = "dir-item";
      tip.style.color = "#999";
      tip.style.cursor = "default";
      tip.textContent = "选择磁盘以浏览";
      list.appendChild(tip);
    } else if (data.parent) {
      var up = document.createElement("div");
      up.className = "dir-item";
      up.innerHTML = "<span class='arrow'>▲</span> ..";
      up.addEventListener("click", function () {
        if (data.parent && data.parent.length > 1) {
          DIR_PICK.history.push(DIR_PICK.current);
          browseDir(data.parent);
        } else {
          browseDir("");
        }
      });
      list.appendChild(up);
    }
    (data.dirs || []).forEach(function (name) {
      var item = document.createElement("div");
      item.className = "dir-item";
      var folderPath = data.is_drive_view
        ? name
        : (DIR_PICK.current.replace(/[\\/]+$/, "") + "\\" + name);
      item.innerHTML = "<span class='arrow'>▶</span> " + esc(name);
      item.title = folderPath;
      item.addEventListener("click", function () {
        DIR_PICK.history.push(DIR_PICK.current);
        browseDir(folderPath);
      });
      list.appendChild(item);
    });
    var isFileMode = (DIR_PICK.mode === "file");
    (data.files || []).forEach(function (name) {
      var item = document.createElement("div");
      item.className = "dir-item file-item";
      var filePath = DIR_PICK.current.replace(/[\\/]+$/, "") + "\\" + name;
      item.innerHTML = "<span class='arrow'>•</span> " + esc(name);
      item.title = filePath;
      item.addEventListener("click", function () {
        var input = $(DIR_PICK.target);
        if (input) input.value = filePath;
        closeDirModal();
      });
      list.appendChild(item);
    });
    $("dir-current").textContent = DIR_PICK.current || "（未进入任何目录）";
    var ok = $("dir-modal-ok");
    if (isFileMode) {
      ok.disabled = true;
    } else {
      ok.disabled = !DIR_PICK.current || DIR_PICK.current === "";
      if (!ok.disabled) {
        ok.setAttribute("data-path", DIR_PICK.current);
      }
    }
  }
  function renderDirBreadcrumb() {
    var crumb = $("dir-crumb");
    crumb.innerHTML = "";
    var parts = [];
    var raw = DIR_PICK.current || "";
    if (raw) {
      parts = raw.split(/[\\/]/);
      if (parts[0] === "") parts.shift();
    } else {
      var home = document.createElement("span");
      home.className = "crumb";
      home.textContent = "我的电脑";
      home.addEventListener("click", function () { browseDir(""); });
      crumb.appendChild(home);
      return;
    }
    var acc = "";
    var home2 = document.createElement("span");
    home2.className = "crumb";
    home2.textContent = "我的电脑";
    home2.addEventListener("click", function () { browseDir(""); });
    crumb.appendChild(home2);
    crumb.appendChild(document.createTextNode(" / "));
    parts.forEach(function (seg, i) {
      if (!seg) return;
      acc += (i === 0 ? seg : "\\" + seg);
      var c = document.createElement("span");
      c.className = "crumb";
      c.textContent = seg;
      (function (p) {
        c.addEventListener("click", function () { browseDir(p); });
      })(acc);
      crumb.appendChild(c);
      if (i < parts.length - 1) crumb.appendChild(document.createTextNode(" / "));
    });
  }

  function bindDirPicker() {
    document.querySelectorAll(".browse-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest(".folder-row");
        if (!row) return;
        var targetId = row.getAttribute("data-target");
        var label = row.querySelector("label");
        $("dir-modal").setAttribute("data-target-label", label ? label.textContent : targetId);
        openDirPicker(targetId);
      });
    });
    $("dir-modal-close").addEventListener("click", closeDirModal);
    $("dir-modal-cancel").addEventListener("click", closeDirModal);
    $("dir-modal-ok").addEventListener("click", function () {
      var path = DIR_PICK.current;
      if (!path) return;
      var input = $(DIR_PICK.target);
      if (input) input.value = path;
      closeDirModal();
    });
    $("dir-modal").addEventListener("click", function (e) {
      if (e.target === $("dir-modal")) closeDirModal();
    });
  }
  function closeDirModal() { $("dir-modal").classList.add("hidden"); }

  // ---------- 运行记录 ----------
  function rememberHistory(outputDir) {
    try {
      var arr = JSON.parse(localStorage.getItem(RUN_HISTORY_KEY) || "[]");
      arr = arr.filter(function (x) { return x !== outputDir; });
      arr.unshift(outputDir);
      localStorage.setItem(RUN_HISTORY_KEY, JSON.stringify(arr.slice(0, 8)));
    } catch (e) { /* ignore */ }
  }
  function getHistory() {
    try { return JSON.parse(localStorage.getItem(RUN_HISTORY_KEY) || "[]"); }
    catch (e) { return []; }
  }
  function loadHistoryPreview() {
    var hist = getHistory();
    if (hist.length && hist[0]) {
      $("sb-last").textContent = "最近任务：已运行过（输出: " + hist[0] + "）";
    }
  }
  function loadRunRecord(outputDir) {
    if (!outputDir) return;
    return fetch("/api/runs?output_dir=" + encodeURIComponent(outputDir))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "读取失败");
        renderRunCard(data.run, data.output_dir);
      });
  }
  function renderRunCard(run, outDir) {
    var body = $("runs-body");
    body.innerHTML = "";
    if (!run) {
      var empty = document.createElement("div");
      empty.className = "runs-empty";
      empty.textContent = "输出目录中没有 run_log.json：" + outDir;
      body.appendChild(empty);
      return;
    }
    var card = document.createElement("div");
    card.className = "run-card";
    var head = document.createElement("div");
    head.className = "run-card-head";
    head.innerHTML = "<span>运行 " + esc(run.run_id || "—") + "</span>";
    var t0 = document.createElement("span");
    t0.className = "tag note";
    t0.textContent = "开始 " + esc(run.start_time || "");
    head.appendChild(t0);
    var openBtn = document.createElement("button");
    openBtn.className = "btn mini";
    openBtn.textContent = "打开输出目录";
    openBtn.style.marginLeft = "auto";
    openBtn.addEventListener("click", function () {
      fetch("/api/open_output", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ output_dir: outDir })
      });
    });
    head.appendChild(openBtn);
    card.appendChild(head);
    var cb = document.createElement("div");
    cb.className = "run-card-body";
    (run.steps || []).forEach(function (stp) {
      var row = document.createElement("div");
      row.className = "run-step-row";
      var nm = document.createElement("span");
      nm.className = "run-step-name";
      nm.textContent = "Step " + (stp.step || "?") + " · " + esc(stp.name || "");
      var tag = document.createElement("span");
      tag.className = "tag " + (stp.success === false ? "err" : "ok");
      tag.textContent = stp.success === false ? "失败" : "成功";
      var detail = document.createElement("span");
      detail.className = "run-step-detail";
      var outs = stp.output || {};
      var detailText = [];
      if (outs.matches != null) detailText.push("匹配 " + outs.matches + " 条");
      if (outs.records != null) detailText.push("记录 " + outs.records + " 条");
      if (outs.failed_count != null) detailText.push("失败 " + outs.failed_count + " 个");
      if (outs.proteins != null) detailText.push("蛋白 " + outs.proteins + " 个");
      if (stp.duration_sec != null) detailText.push((Number(stp.duration_sec) || 0).toFixed(1) + "s");
      detail.textContent = detailText.join(" / ");
      row.appendChild(nm); row.appendChild(tag); row.appendChild(detail);
      cb.appendChild(row);
    });
    var sum = run.summary || {};
    if (Object.keys(sum).length) {
      var grid = document.createElement("div");
      grid.className = "run-summary-grid";
      var items = [
        ["run_id", "Run ID"], ["total_proteins", "识别蛋白"], ["high_confidence", "高置信"],
        ["total_peptide_matches", "肽段匹配"]
      ];
      items.forEach(function (pair) {
        if (sum[pair[0]] == null) return;
        var it = document.createElement("div");
        it.className = "run-summary-item";
        it.innerHTML = "<div class='v'>" + esc(sum[pair[0]]) + "</div><div class='k'>" + pair[1] + "</div>";
        grid.appendChild(it);
      });
      cb.appendChild(grid);
    }
    card.appendChild(cb);
    body.appendChild(card);
  }
  function refreshRuns() {
    var hist = getHistory();
    var body = $("runs-body");
    if (!hist.length) {
      body.innerHTML = "<div class='runs-empty'>暂无运行记录。在主工作区运行一次任务后，此处会展示最近一次运行摘要。</div>";
      return;
    }
    // 尝试加载最近一次
    loadRunRecord(hist[0]).catch(function () {
      body.innerHTML = "<div class='runs-empty'>读取运行记录失败：" + esc(hist[0]) + "</div>";
    });
  }

  // ---------- 运行监控 / 异常 / 结果分析 ----------
  function refreshMonitor() {
    if (!LAST_STATUS) {
      fetch("/api/run/status?since=0").then(function (r) { return r.json(); })
        .then(function (d) {
          LAST_STATUS = d.status || {};
          paintMonitor(LAST_STATUS);
        }).catch(function () {});
      return;
    }
    paintMonitor(LAST_STATUS);
  }
  function paintMonitor(st) {
    var el = $("monitor-step");
    if (el) el.textContent = st.running ? ("Step " + st.current_step + " · " + st.step_name) : (st.current_step === 5 ? "已结束" : "空闲");
    var el2 = $("monitor-progress");
    if (el2) el2.textContent = (st.progress || 0) + "%";
  }
  function refreshAnomaly() {
    var hist = getHistory();
    if (!hist.length) {
      $("anomaly-body").innerHTML = "<p>暂无运行记录。</p>";
      return;
    }
    fetch("/api/runs?output_dir=" + encodeURIComponent(hist[0]))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var body = $("anomaly-body");
        if (!d.ok || !d.run) { body.innerHTML = "<p>暂无可用 run_log.json。</p>"; return; }
        var run = d.run;
        var html = "";
        var failedSteps = (run.steps || []).filter(function (s) { return s.success === false; });
        if (failedSteps.length) {
          html += "<p><span class='tag err'>异常</span> 以下步骤标记为失败：</p>";
          failedSteps.forEach(function (s) {
            html += "<p class='mono'>Step " + s.step + " · " + esc(s.name) + (s.error ? "：" + esc(s.error) : "") + "</p>";
          });
        } else {
          html += "<p><span class='tag ok'>正常</span> 所有步骤均成功。</p>";
        }
        // step2 failures
        var s2 = (run.steps || []).find(function (s) { return s.step === 2; });
        if (s2 && s2.output && s2.output.failed_count) {
          html += "<p><span class='tag warn'>警告</span> Step 2 有 " + s2.output.failed_count + " 个蛋白抓取失败（可点击 AI 分析进一步排查）。</p>";
        }
        var s4 = (run.steps || []).find(function (s) { return s.step === 4; });
        if (s4 && s4.output && s4.output.proteins === 0) {
          html += "<p><span class='tag err'>异常</span> Step 4 未识别出任何蛋白。</p>";
        }
        if (run.summary && run.summary.high_confidence === 0) {
          html += "<p><span class='tag note'>提示</span> 无高置信度蛋白，建议复核容差与酶切参数。</p>";
        }
        if (!html) html = "<p>未发现明显异常。</p>";
        body.innerHTML = html;
      }).catch(function () {});
  }
  function refreshResultAnalysis() {
    var hist = getHistory();
    if (!hist.length) {
      $("result-analysis-body").innerHTML = "<p>暂无运行记录。</p>";
      return;
    }
    fetch("/api/runs?output_dir=" + encodeURIComponent(hist[0]))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var body = $("result-analysis-body");
        if (!d.ok || !d.run) { body.innerHTML = "<p>暂无可用 run_log.json。</p>"; return; }
        var sum = d.run.summary || {};
        var s = (d.run.steps || [])[3] || {};
        var html = "<div class='run-summary-grid'>";
        var items = [
          ["run_id", "Run ID"], ["total_proteins", "识别蛋白数"], ["high_confidence", "高置信度"],
          ["total_peptide_matches", "肽段匹配数"]
        ];
        items.forEach(function (pair) {
          if (sum[pair[0]] == null) return;
          html += "<div class='run-summary-item'><div class='v'>" + esc(sum[pair[0]]) + "</div><div class='k'>" + pair[1] + "</div></div>";
        });
        html += "</div>";
        body.innerHTML = html + "<p>详细结果见 Step 4 产物 protein_analysis_result.xlsx 与 Step 3 的 peptide_match_result.xlsx。</p>";
      }).catch(function () {});
  }

  // ---------- DSH 模型配置（自定义 OpenAI 兼容网关 -> llm-pi-ai.providers）----------
  function dshMsg(text, isErr) {
    var box = $("dsh-msg");
    if (!box) return;
    var div = document.createElement("div");
    div.className = "msg";
    var m = document.createElement("div");
    m.className = "meta" + (isErr ? " error" : "");
    m.textContent = nowTime() + (isErr ? " · 出错" : " · DSH 模型配置");
    div.appendChild(m);
    div.appendChild(document.createTextNode(text));
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }
  function readDshModels() {
    return String($("dsh_models").value || "").split(/\r?\n/).map(function (s) { return s.trim(); }).filter(Boolean);
  }
  function setDshModels(ids) {
    $("dsh_models").value = (ids || []).join("\n");
  }
  function hasDshModel(id) {
    return readDshModels().indexOf(id) >= 0;
  }
  function addDshModel(id) {
    if (hasDshModel(id)) return;
    var cur = readDshModels(); cur.push(id); setDshModels(cur);
  }
  function removeDshModel(id) {
    setDshModels(readDshModels().filter(function (m) { return m !== id; }));
  }
  function dshRouteSlugGuess() {
    var dn = String($("dsh_display_name").value || "").trim().toLowerCase();
    return dn.replace(/[^a-z0-9]+/g, "-").replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");
  }
  function clearDshForm() {
    ["dsh_display_name", "dsh_route", "dsh_base_url", "dsh_api_key"].forEach(function (id) { $(id).value = ""; });
    $("dsh_models").value = "";
    $("dsh_set_default").checked = false;
    window._dshEditingHasKey = false;
    window._dshEditingRoute = "";
    var box = $("dsh-discover-box");
    if (box) box.classList.add("hidden");
    var dl = $("dsh-discover-list");
    if (dl) dl.innerHTML = "";
    dshMsg("已清空表单，可重新填写。");
  }
  function collectDshPayload() {
    var routeInput = String($("dsh_route").value || "").trim();
    if (!routeInput) routeInput = dshRouteSlugGuess();
    return {
      display_name: String($("dsh_display_name").value || "").trim(),
      route: routeInput,
      base_url: String($("dsh_base_url").value || "").trim(),
      api_key: String($("dsh_api_key").value || "").trim(),
      models: readDshModels(),
      set_default: $("dsh_set_default").checked
    };
  }
  function saveDshProvider() {
    var p = collectDshPayload();
    var errs = [];
    if (!p.route) errs.push("请填写显示名（自动生成路由键）或手动填路由键");
    if (!/^[a-z0-9-]+$/.test(p.route)) errs.push("路由键只允许小写字母/数字/连字符");
    if (!/^https?:\/\//.test(p.base_url)) errs.push("BaseURL 需以 http(s):// 开头");
    if (!p.models.length) errs.push("模型 ID 至少 1 个");
    if (!p.api_key && !window._dshEditingHasKey) errs.push("新增路由必须填写 API Key");
    if (errs.length) { dshMsg("校验失败：" + errs.join("；"), true); return; }
    var btn = $("dsh-btn-save");
    btn.disabled = true;
    fetch("/api/dsh/providers/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p)
    }).then(function (r) { return r.json(); }).then(function (d) {
      btn.disabled = false;
      if (d && d.ok) {
        dshMsg("保存成功：" + (d.route || p.route) + (p.set_default ? "，已设为默认模型：" + (d.defaultModel || "") : "") + "。settings 已热重载即时生效，无需重启 DSH Web。");
        // 密钥已写入 credentials，表单 Key 清空（再保存留空 = 保留旧 Key）
        $("dsh_api_key").value = "";
        window._dshEditingHasKey = true;
        window._dshEditingRoute = d.route || p.route;
        refreshDshConfig();
      } else {
        dshMsg((d && d.error) || "保存失败", true);
      }
    }).catch(function (e) {
      btn.disabled = false;
      dshMsg("保存失败：" + e, true);
    });
  }
  function discoverDshModels() {
    var base_url = String($("dsh_base_url").value || "").trim();
    var api_key = String($("dsh_api_key").value || "").trim();
    if (!base_url) { dshMsg("请先填写 BaseURL 再执行模型发现", true); return; }
    if (!api_key && !window._dshEditingHasKey) { dshMsg("模型发现需要 API Key（本路由已保存密钥时可直接用旧 Key 校验）", true); return; }
    var btn = $("dsh-btn-discover");
    btn.disabled = true;
    fetch("/api/dsh/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_url: base_url, api_key: api_key })
    }).then(function (r) { return r.json(); }).then(function (d) {
      btn.disabled = false;
      if (d && d.ok) {
        renderDshDiscover(d.models || []);
        dshMsg("连通成功，发现 " + (d.models || []).length + " 个候选模型，勾选后自动追加到上方模型列表。");
      } else {
        dshMsg((d && (d.message || d.error)) || "模型发现失败", true);
      }
    }).catch(function (e) {
      btn.disabled = false;
      dshMsg("模型发现请求失败：" + e, true);
    });
  }
  function renderDshDiscover(models) {
    var box = $("dsh-discover-list");
    if (!box) return;
    box.innerHTML = "";
    models.forEach(function (m) {
      var lab = document.createElement("label");
      lab.className = "dsh-model-chip";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = hasDshModel(m.id);
      cb.addEventListener("change", function () {
        if (cb.checked) addDshModel(m.id); else removeDshModel(m.id);
      });
      var span = document.createElement("span");
      span.textContent = m.id;
      lab.appendChild(cb);
      lab.appendChild(span);
      box.appendChild(lab);
    });
    $("dsh-discover-box").classList.remove("hidden");
  }
  function fillDshForm(prov) {
    $("dsh_display_name").value = prov.displayName || "";
    $("dsh_route").value = prov.route;
    $("dsh_base_url").value = prov.baseURL || "";
    $("dsh_api_key").value = "";
    setDshModels(prov.models || []);
    $("dsh_set_default").checked = !!prov.isDefault;
    window._dshEditingHasKey = !!prov.hasKey;
    window._dshEditingRoute = prov.route;
    dshMsg("已载入编辑模式：路由 " + prov.route + "（API Key 留空保存则保留旧密钥）。");
  }
  function deleteDshProvider(route) {
    if (!window.confirm("确定删除路由 " + route + "？将同时移除 settings.yaml 中该路由与 .credentials.yaml 中对应密钥引用（写入前自动备份）。")) return;
    fetch("/api/dsh/providers/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ route: route })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) {
        dshMsg("已删除路由 " + route + "（备份位于会话 temp 目录）。");
        if (window._dshEditingRoute === route) clearDshForm();
        refreshDshConfig();
      } else {
        dshMsg((d && d.error) || "删除失败", true);
      }
    }).catch(function (e) { dshMsg("删除失败：" + e, true); });
  }
  function setDshDefault(route, model) {
    if (!model) return;
    fetch("/api/dsh/providers/default", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ route: route, model: model })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) { dshMsg("已设为默认模型：" + route + " / " + model + "（agent-default-model 已更新，即时生效）。"); refreshDshConfig(); }
      else dshMsg((d && d.error) || "设置默认失败", true);
    }).catch(function (e) { dshMsg("设置默认失败：" + e, true); });
  }
  function restoreDshDefaults() {
    if (!window.confirm("确定恢复 DeepSeek 默认？将移除本板块新增的所有用户路由与其密钥引用；内置 DeepSeek 配置不受影响；若默认模型指向用户路由会回退 deepseek-official（写入前自动备份）。")) return;
    fetch("/api/dsh/providers/restore_default", { method: "POST" })
      .then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok) {
          dshMsg("已恢复 DeepSeek 默认：" + (d.default && d.default.provider ? d.default.provider + " / " + (d.default.model || "") : ""));
          clearDshForm();
          refreshDshConfig();
        } else { dshMsg((d && d.error) || "恢复失败", true); }
      }).catch(function (e) { dshMsg("恢复失败：" + e, true); });
  }
  function renderDshProviderList(list) {
    var wrap = $("dsh-provider-list");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!list.length) {
      var empty = document.createElement("div");
      empty.className = "runs-empty";
      empty.textContent = "暂无用户新增路由。内置 DeepSeek 默认配置不受影响，可在此添加 OpenAI 兼容网关供 DSH 使用。";
      wrap.appendChild(empty);
      return;
    }
    list.forEach(function (prov) {
      var card = document.createElement("div");
      card.className = "dsh-provider-card";
      // 头部
      var head = document.createElement("div");
      head.className = "dsh-p-head";
      var nm = document.createElement("span");
      nm.className = "dsh-p-name";
      nm.textContent = prov.displayName || prov.route;
      head.appendChild(nm);
      var tag = document.createElement("span");
      tag.className = "tag note";
      tag.textContent = prov.route;
      head.appendChild(tag);
      if (prov.isDefault) {
        var dt = document.createElement("span");
        dt.className = "tag ok";
        dt.textContent = "默认：" + (prov.defaultModel || "");
        head.appendChild(dt);
      }
      var spacer = document.createElement("span");
      spacer.className = "spacer";
      head.appendChild(spacer);
      var edBtn = document.createElement("button");
      edBtn.className = "btn mini";
      edBtn.textContent = "编辑";
      edBtn.addEventListener("click", function () { fillDshForm(prov); });
      head.appendChild(edBtn);
      var delBtn = document.createElement("button");
      delBtn.className = "btn mini danger";
      delBtn.textContent = "删除";
      delBtn.addEventListener("click", function () { deleteDshProvider(prov.route); });
      head.appendChild(delBtn);
      card.appendChild(head);
      // 详情
      var body = document.createElement("div");
      body.className = "dsh-p-body";
      var line = document.createElement("div");
      line.className = "dsh-p-line";
      line.textContent = "BaseURL: " + (prov.baseURL || "—") + "　|　api: " + (prov.api || "openai-completions") + "　|　" + (prov.apiKeyEnv || "") + (prov.hasKey ? "（已保存密钥）" : "（无密钥）");
      body.appendChild(line);
      var mline = document.createElement("div");
      mline.className = "dsh-p-models";
      (prov.models || []).forEach(function (m) {
        var chip = document.createElement("span");
        chip.className = "dsh-model-chip static";
        chip.textContent = m;
        mline.appendChild(chip);
      });
      body.appendChild(mline);
      // 设为默认
      var defRow = document.createElement("div");
      defRow.className = "dsh-p-defrow";
      if (prov.isDefault) {
        var okTip = document.createElement("span");
        okTip.className = "tag ok";
        okTip.textContent = "当前为默认模型，无需重复设置";
        defRow.appendChild(okTip);
      } else {
        var sel = document.createElement("select");
        sel.className = "dsh-p-defsel";
        (prov.models || []).forEach(function (m) {
          var o = document.createElement("option");
          o.value = m; o.textContent = m;
          sel.appendChild(o);
        });
        var defBtn = document.createElement("button");
        defBtn.className = "btn mini primary";
        defBtn.textContent = "设为默认模型";
        defBtn.addEventListener("click", function () { setDshDefault(prov.route, sel.value); });
        defRow.appendChild(sel);
        defRow.appendChild(defBtn);
      }
      body.appendChild(defRow);
      card.appendChild(body);
      wrap.appendChild(card);
    });
  }
  function refreshDshConfig() {
    fetch("/api/dsh/providers").then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { dshMsg("加载路由列表失败：" + (d.error || ""), true); return; }
        var sp = $("dsh-settings-path");
        if (sp) sp.textContent = d.settingsPath || "~/.dsh/settings.yaml";
        var cp = $("dsh-credentials-path");
        if (cp) cp.textContent = d.credentialsPath || "~/.dsh/.credentials.yaml";
        var dm = d.default || {};
        var cur = $("dsh-current-default");
        if (cur) cur.textContent = dm.provider ? (dm.provider + " / " + (dm.model || "")) : "—";
        renderDshProviderList(d.providers || []);
      }).catch(function (e) { dshMsg("加载路由列表请求失败：" + e, true); });
  }

  // ---------- 系统设置 ----------
  var SETTINGS_PATH_KEYS = ["runtime_dir", "data_dir", "output_dir", "logs_dir", "temp_dir", "sessions_dir", "backups_dir"];
  var SETTINGS_KEY_SHORT = {
    runtime_dir: "runtime", data_dir: "data", output_dir: "output", logs_dir: "logs",
    temp_dir: "temp", sessions_dir: "sessions", backups_dir: "backups"
  };

  function setSettingsMsg(text, isErr) {
    var box = $("set-msg");
    if (!box) return;
    var div = document.createElement("div");
    div.className = "msg";
    var m = document.createElement("div");
    m.className = "meta" + (isErr ? " error" : "");
    m.textContent = nowTime();
    div.appendChild(m);
    div.appendChild(document.createTextNode(text));
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  function loadSettings() {
    return fetch("/api/settings").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ok) { setSettingsMsg("读取设置失败", true); return; }
      var s = d.settings || {}, rt = d.runtime || {};
      if ($("set_web_port")) $("set_web_port").value = (s.web ? s.web.port : 0);
      if ($("set_open_browser")) $("set_open_browser").checked = !!(s.web && s.web.open_browser);
      if ($("set_dsh_port")) $("set_dsh_port").value = (s.dsh ? s.dsh.port : 3080);
      var sel = $("set_dsh_permission");
      if (sel) {
        sel.innerHTML = "";
        (d.permission_modes || []).forEach(function (mode) {
          var o = document.createElement("option");
          o.value = mode; o.textContent = mode;
          sel.appendChild(o);
        });
        sel.value = (s.dsh && s.dsh.permission_mode) || "workspace-write";
      }
      SETTINGS_PATH_KEYS.forEach(function (k) {
        var inp = $("set_" + k);
        if (inp) inp.placeholder = rt[SETTINGS_KEY_SHORT[k]] || "留空 = 默认位置";
        var hint = $("hint_" + k);
        if (hint) hint.textContent = "当前生效：" + (rt[SETTINGS_KEY_SHORT[k]] || "—");
      });
      if ($("set_runtime_root")) $("set_runtime_root").textContent = d.runtime_root || "—";
      if ($("set_config_path")) $("set_config_path").textContent = d.local_config_file || "—";
      if ($("set_python")) $("set_python").textContent = d.python || "—";
    }).catch(function () { setSettingsMsg("读取设置失败（服务未响应）", true); });
  }

  function saveSettings() {
    var payload = {
      web_port: $("set_web_port") ? $("set_web_port").value : "",
      open_browser: $("set_open_browser") ? $("set_open_browser").checked : true,
      dsh_port: $("set_dsh_port") ? $("set_dsh_port").value : "",
      dsh_permission_mode: $("set_dsh_permission") ? $("set_dsh_permission").value : ""
    };
    SETTINGS_PATH_KEYS.forEach(function (k) {
      var inp = $("set_" + k);
      if (inp) payload[k] = inp.value;
    });
    fetch("/api/settings/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().then(function (d) { return { httpOk: r.ok, d: d }; });
    }).then(function (res) {
      if (!res.httpOk || !res.d.ok) {
        setSettingsMsg("保存失败：" + ((res.d && (res.d.error || res.d.message)) || "未知错误"), true);
        return;
      }
      setSettingsMsg(res.d.message || "设置已保存");
      loadSettings();
    }).catch(function () { setSettingsMsg("保存失败（服务未响应）", true); });
  }

  // ---------- 事件绑定 ----------
  function bindEvents() {
    $("btn-run").addEventListener("click", startRun);
    $("btn-stop").addEventListener("click", stopRun);
    $("btn-open-output").addEventListener("click", openOutputDir);
    $("btn-ai").addEventListener("click", function () { aiAnalysis("主工作区"); });
    $("btn-ai-2").addEventListener("click", function () { aiAnalysis("分析面板"); });
    $("btn-ai-code").addEventListener("click", function () { aiAnalysis("代码审查"); });
    $("btn-qa-ai").addEventListener("click", function () { aiAnalysis("问答"); });
    // DSH 模型配置
    $("dsh-btn-save").addEventListener("click", saveDshProvider);
    $("dsh-btn-discover").addEventListener("click", discoverDshModels);
    $("dsh-btn-clear").addEventListener("click", clearDshForm);
    $("dsh-btn-refresh").addEventListener("click", refreshDshConfig);
    $("dsh-btn-restore").addEventListener("click", restoreDshDefaults);
    // 系统设置
    if ($("set-btn-save")) $("set-btn-save").addEventListener("click", saveSettings);
    if ($("set-btn-reload")) $("set-btn-reload").addEventListener("click", loadSettings);
    $("btn-shutdown").addEventListener("click", function () {
      if (!window.confirm("确定退出本地 Web 服务？页面将无法再访问，可用 run.bat 重新启动。")) return;
      fetch("/api/shutdown", { method: "POST" })
        .then(function () { setTopStatus(false); })
        .catch(function () {});
      setTimeout(function () { setTopStatus(false); }, 300);
    });
    $("btn-load-run-log").addEventListener("click", function () {
      // choose any folder to load its run_log.json
      $("dir-modal").setAttribute("data-target-label", "运行记录目录");
      DIR_PICK.mode = "runlog";
      $("dir-modal-title").textContent = "选择含 run_log.json 的输出目录";
      var old = DIR_PICK.target;
      DIR_PICK.target = null;
      $("dir-modal").classList.remove("hidden");
      renderDirBreadcrumb();
      browseDir("");
      // 保存回调
      window._runlogDirOk = function (path) {
        loadRunRecord(path);
        rememberHistory(path);
      };
      DIR_PICK.target = old;
    });
    // 目录选择确认时若 mode=runlog 走加载逻辑
    var realOk = $("dir-modal-ok");
    realOk.addEventListener("click", function () {});
    // unbind previous custom logic by using delegation below
  }

  // 重写确认按钮逻辑（支持 runlog 模式）
  function bindDirOk() {
    var ok = $("dir-modal-ok");
    var handler = function () {
      var path = DIR_PICK.current;
      if (!path) return;
      if (DIR_PICK.mode === "runlog") {
        if (window._runlogDirOk) window._runlogDirOk(path);
        DIR_PICK.mode = null;
        closeDirModal();
        showPage("runs");
        return;
      }
      var input = $(DIR_PICK.target);
      if (input) input.value = path;
      closeDirModal();
    };
    // remove previous inline binding (registered in bindDirPicker)
    var clone = ok.cloneNode(true);
    ok.parentNode.replaceChild(clone, ok);
    $("dir-modal-ok").addEventListener("click", handler);
  }

  // ---------- boot ----------
  function boot() {
    bindEvents();
    bindDirPicker();
    bindDirOk();
    loadMeta().then(function () {
      setTopStatus(true);
      // 若后端已有 run，启动轮询
      fetch("/api/run/status?since=0").then(function (r) { return r.json(); })
        .then(function (d) {
          LAST_STATUS = d.status || {};
          if (LAST_STATUS.running || (LAST_STATUS.result_files && Object.keys(LAST_STATUS.result_files).length)) {
            appendLogs(d.logs || []);
            LAST_LOG_TOTAL = d.log_total || 0;
            startPolling(true);
          }
          if (LAST_STATUS.running) setRunningUI(true);
        }).catch(function () { setTopStatus(false); });
    }).catch(function () { setTopStatus(false); });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
