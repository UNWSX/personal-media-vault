/* ============ 影音库 · 本地网页版 ============ */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  const TYPE_META = {
    movie:    { label: "电影", icon: "🎬", tint: "#5b4b8a" },
    teleplay: { label: "剧集", icon: "📺", tint: "#2f5a78" },
    game:     { label: "游戏", icon: "🎮", tint: "#2f6b52" },
    book:     { label: "图书", icon: "📚", tint: "#7a5a2c" },
    music:    { label: "音乐", icon: "🎵", tint: "#7a3358" },
  };
  const meta = (t) => TYPE_META[t] || { label: t || "其他", icon: "📄", tint: "#444" };

  // 游戏平台：把旧数据里的 PC / PS4 / Nintendo Switch / XSX 归一成几个大类
  // 版本后缀：有意义，不能当简繁重复砍掉
  const TITLE_SUFFIX = ["复刻版", "重制版", "重置版", "皇家版", "终极版", "完全版", "决定版",
    "导演剪辑版", "導演剪輯版", "剧场版", "劇場版", "特别版", "特別版", "周年纪念版",
    "高清版", "豪华版", "豪華版", "黄金版", "年度版", "试玩版", "重制", "复刻"];

  // '十三机兵防卫圈 十三機兵防衛圏' -> '十三机兵防卫圈'；'xx 复刻版' 保持不动
  // ---------------- 标题自然排序（认识中文数字） ----------------
  const CN_DIGIT = { 零: 0, 〇: 0, 一: 1, 壹: 1, 二: 2, 贰: 2, 两: 2, 三: 3, 叁: 3,
    四: 4, 肆: 4, 五: 5, 伍: 5, 六: 6, 陆: 6, 七: 7, 柒: 7, 八: 8, 捌: 8, 九: 9, 玖: 9 };
  const CN_UNIT = { 十: 10, 拾: 10, 百: 100, 佰: 100, 千: 1000, 仟: 1000 };

  // 十三 -> 13, 二十 -> 20, 一百零三 -> 103
  function cnToNum(s) {
    let total = 0, section = 0, num = 0;
    for (const ch of s) {
      if (CN_DIGIT[ch] !== undefined) {
        num = CN_DIGIT[ch];
      } else if (CN_UNIT[ch] !== undefined) {
        if (num === 0) num = 1;
        section += num * CN_UNIT[ch];
        num = 0;
      }
    }
    return total + section + num;
  }

  const TITLE_COLLATOR = new Intl.Collator("zh-Hans-CN", { sensitivity: "base" });

  // 把标题切成「文字 / 数字」片段，数字含中文数字
  // 只有这些位置的中文数字才当数字：第X、X季、X部、X集……
  // 其它（三体、九龙城寨、十字军）按拼音排
  const NUM_UNIT = "季部集章卷期话代作册篇版回";
  const NUM_PAD = 8;

  // 生成排序键：数字补零到固定宽度，这样「2」和「 复刻版」的空格能保持自然顺序
  function sortKey(s) {
    const re = new RegExp(
      "(\\d+)" +
      "|第([零〇一二三四五六七八九十百千两]+)" +
      "|([零〇一二三四五六七八九十百千两]+)(?=[" + NUM_UNIT + "])",
      "g"
    );
    let out = "";
    let last = 0, m;
    while ((m = re.exec(s)) !== null) {
      out += s.slice(last, m.index);
      let num, prefix = "";
      if (m[1] !== undefined) {
        num = parseInt(m[1], 10);
        // 紧跟在汉字后面的数字（如「漫威蜘蛛侠2」）是续作编号，
        // 用高码位当分隔符，让它排在「 复刻版」「：导演剪辑版」这类变体之后
        const prev = m.index > 0 ? s[m.index - 1] : "";
        if (prev && /[\u4e00-\u9fff]/.test(prev) && prev !== "第") prefix = "\uFFFF";
      } else if (m[2] !== undefined) {
        num = cnToNum(m[2]);
        prefix = "第";
      } else {
        num = cnToNum(m[3]);
      }
      out += prefix + String(num).padStart(NUM_PAD, "0");
      last = m.index + m[0].length;
    }
    out += s.slice(last);
    return out;
  }

  function naturalCompare(a, b) {
    const ka = sortKey(a || "");
    const kb = sortKey(b || "");
    const c = TITLE_COLLATOR.compare(ka, kb);
    return c !== 0 ? c : TITLE_COLLATOR.compare(a || "", b || "");
  }

  const CJK_RX = /[\u4e00-\u9fff]/;
  const KANA_RX = /[\u3040-\u30ff\u31f0-\u31ff]/;
  const LATIN_CORE_RX = /^[A-Za-z0-9:'\-\.,&!?()]+$/;
  const PUNCT_RX = /[\s·・：:．、\-—–_]+/g;

  const coreOf = (s) => String(s || "").replace(PUNCT_RX, "");

  function isLatinOnly(s) {
    const c = coreOf(s);
    return !!c && LATIN_CORE_RX.test(c);
  }

  function overlapRatio(head, tail) {
    const h = coreOf(head);
    const x = coreOf(tail);
    if (!x) return 0;
    const shared = new Set([...x].filter((ch) => h.includes(ch)));
    return shared.size / x.length;
  }

  // 剥离中文标题后面的英文原名 / 假名 / 繁体异体；纯外文标题原样返回
  function cleanTitle(raw) {
    if (!raw) return raw || "";
    let s = String(raw).trim();
    if (!CJK_RX.test(s)) return s;

    for (const sep of ["/", "|", "｜", "／"]) {
      if (!s.includes(sep)) continue;
      const segs = s.split(sep).map((x) => x.trim()).filter(Boolean);
      if (segs.length && CJK_RX.test(segs[0])) {
        const keep = [segs[0]];
        for (const x of segs.slice(1)) {
          if (CJK_RX.test(x) && !KANA_RX.test(x)) keep.push(x);
        }
        s = keep.join(" ");
      }
      break;
    }

    const tokens = s.split(/\s+/).filter(Boolean);
    if (tokens.length < 2) return s;

    const kept = [tokens[0]];
    for (const tok of tokens.slice(1)) {
      const core = coreOf(tok);
      if (!core) { kept.push(tok); continue; }
      if (TITLE_SUFFIX.some((x) => tok.includes(x))) { kept.push(tok); continue; }
      if (KANA_RX.test(core)) break;
      if (isLatinOnly(core)) break;
      if (CJK_RX.test(core) && overlapRatio(kept.join(""), core) >= 0.3) break;
      kept.push(tok);
    }
    return kept.join(" ").trim() || s;
  }

  const PLATFORM_CHOICES = ["PC", "PS", "Switch", "Xbox", "手机"];
  function normalizePlatforms(list) {
    const out = [];
    (list || []).forEach((v) => {
      const s = String(v).toLowerCase();
      let k = null;
      if (/pc|mac|linux|steam/.test(s)) k = "PC";
      else if (/^ps|playstation/.test(s)) k = "PS";
      else if (/switch/.test(s)) k = "Switch";
      else if (/xbox|xsx|xone/.test(s)) k = "Xbox";
      else if (/iphone|ipad|android|ios|手机/.test(s)) k = "手机";
      if (k && !out.includes(k)) out.push(k);
    });
    return out;
  }

  const STORE = "mediavault.ui.v1";
  const WRITE_HEADERS = {
    "Content-Type": "application/json",
    "X-MediaVault-Client": "media-vault",
  };
  const {
    conflictGroup,
    dirtyKeysBetween,
    mergeLatestWithLocal,
    threeWayGroupDecision,
    networkSortScore,
    seriesSortKey,
  } = window.MediaVaultCompat;
  const SORT_OPTIONS = [
    { key: "title", label: "标题" },
    { key: "date", label: "发行 新→旧" },
    { key: "finish", label: "完成时间 新→旧" },
    { key: "myscore", label: "我的评分 高→低" },
    { key: "score", label: "网络评分 高→低" },
  ];
  const SORT_KEYS = SORT_OPTIONS.map((o) => o.key);
  const state = {
    q: "", types: [], tags: [], status: "all", score: "all",
    sort: "title", more: false, theme: "dark",
  };
  let ITEMS = [];
  let VAULT = "";
  let OBSIDIAN_MODE = true;
  let VIEW = [];
  let current = null;
  let commentTimer = null;
  const commentDrafts = new Map();
  const saveQueues = new Map();
  const saveFailures = new Map();
  const baseItems = new Map();
  const fieldConflicts = new Map();
  let lastFocusRefresh = 0;
  let doubanLogin = false;
  let detailWidthObserver = null;

  // 新增条目用
  let newType = "movie";
  let newStatus = false;
  let newImageData = null;

  // 远程图片也走本地代理（豆瓣图床需要 Referer，浏览器直连会被 403）
  // 游戏旧 score 固定为 Metacritic 0–100，其它固定为豆瓣 0–10。
  function normScore(it) {
    if (!it) return null;
    if (it.type === "game") {
      const value = it.metacriticScore != null ? it.metacriticScore : it.score;
      return value == null ? null : (value > 10 ? value / 10 : value);
    }
    const value = it.doubanScore != null ? it.doubanScore : it.score;
    return value == null ? null : value;
  }

  function scoreSources(it) {
    const out = [];
    if (it.type === "game") {
      const score = it.metacriticScore != null ? it.metacriticScore : it.score;
      if (score != null) {
        const metacritic = it.metacriticScore != null
          ? Number(score)
          : (Number(score) > 10 ? Number(score) : Number(score) * 10);
        out.push({
        key: "metacritic",
        label: "Metacritic",
        raw: metacritic,
        maximum: 100,
        value: `${Math.round(metacritic)} / 100`,
        });
      }
    } else {
      const score = it.doubanScore;
      if (score != null) out.push({
        key: "douban",
        label: "豆瓣",
        raw: Number(score),
        maximum: 10,
        value: `${Number(score).toFixed(1)} / 10`,
      });
    }
    if (["movie", "teleplay"].includes(it.type) && it.rottenTomatoesScore != null) {
      out.push({
        key: "rotten",
        label: "烂番茄",
        raw: Number(it.rottenTomatoesScore),
        maximum: 100,
        value: `${Math.round(it.rottenTomatoesScore)}%`,
      });
    }
    if (it.imdbScore != null) out.push({
      key: "imdb",
      label: "IMDb",
      raw: Number(it.imdbScore),
      maximum: 10,
      value: `${Number(it.imdbScore).toFixed(1)} / 10`,
    });
    if (it.type !== "game" && it.metacriticScore != null) out.push({
      key: "metacritic",
      label: "Metacritic",
      raw: Number(it.metacriticScore),
      maximum: 100,
      value: `${Math.round(it.metacriticScore)} / 100`,
    });
    if (it.bangumiScore != null) out.push({
      key: "bangumi",
      label: "Bangumi",
      raw: Number(it.bangumiScore),
      maximum: 10,
      value: `${Number(it.bangumiScore).toFixed(1)} / 10`,
    });
    return out;
  }

  function calloutWarningText(code) {
    const labels = {
      CALLOUT_NOT_FOUND: "未找到可识别的状态 Callout",
      CALLOUT_AMBIGUOUS: "存在多个状态 Callout，无法确定要更新哪一行",
      CALLOUT_TYPE_MISMATCH: "状态 Callout 的图标与媒体类型不一致",
      CALLOUT_VALUE_UNRECOGNIZED: "状态 Callout 含有额外说明或未知值",
      CALLOUT_STATUS_UNSUPPORTED: "该媒体类型没有对应的 Callout 状态",
    };
    return labels[code] || "状态 Callout 未同步";
  }

  function itemWarningMessages(it) {
    const messages = [];
    if (it.schemaFuture) messages.push("这篇笔记使用未来 schemaVersion，当前版本只读。");
    if (it.mediaIdDuplicate) messages.push("mediaId 与其他笔记重复，当前版本只读；请先人工处理重复 ID。");
    if (it.mediaId && !it.mediaIdValid) messages.push("mediaId 不是合法 UUIDv4，当前版本只读。");
    if (it.schemaVersionRaw != null && it.schemaVersionRaw !== ""
        && !it.schemaVersionValid && !it.schemaFuture && it.schemaVersion !== 1) {
      messages.push("schemaVersion 不是合法的整数版本。");
    }
    (it.ratingWarnings || []).forEach((warning) => messages.push("评分字段：" + warning));
    if (it.calloutWarning) messages.push(calloutWarningText(it.calloutWarning));
    return [...new Set(messages)];
  }

  const imgSrc = (it) => "/media?p=" + encodeURIComponent(it.image) + (it.mtime ? "&t=" + it.mtime : "");

  async function apiJSON(url, options) {
    const res = await fetch(url, options);
    let data;
    try {
      data = await res.json();
    } catch (err) {
      throw new Error(`服务器返回了无法解析的响应（HTTP ${res.status}）`);
    }
    if (!res.ok || !data || data.ok === false) {
      const error = new Error((data && data.error) || `请求失败（HTTP ${res.status}）`);
      error.status = res.status;
      error.data = data || {};
      throw error;
    }
    return data;
  }

  async function refreshLibrary(notify) {
    const data = await apiJSON("/api/library");
    ITEMS = data.items || [];
    VAULT = data.vault || VAULT;
    OBSIDIAN_MODE = data.obsidian !== false;
    doubanLogin = !!data.doubanLogin;
    if (!commentDrafts.size && !saveQueues.size && !fieldConflicts.size) saveFailures.clear();
    if (current) {
      const latest = ITEMS.find((it) => it.path === current.path);
      if (latest) {
        Object.assign(current, latest);
      } else {
        closeDrawer(false);
      }
    }
    render();
    if (data.warnings && data.warnings.length) {
      toast(`有 ${data.warnings.length} 篇笔记解析失败，已跳过写入`);
    }
    if (notify) toast(`已重新读取：${ITEMS.length} 部`);
  }

  function watchRatingJob(path) {
    let attempts = 0;
    const poll = async () => {
      attempts += 1;
      try {
        const data = await apiJSON(
          `/api/ratings?path=${encodeURIComponent(path)}`
        );
        const job = (data.jobs || []).find((entry) => entry.path === path);
        if (job && job.status === "completed") {
          await refreshLibrary(false);
          const latest = ITEMS.find((item) => item.path === path);
          if (
            latest
            && current
            && current.path === path
            && !commentDrafts.has(path)
          ) {
            openDrawer(latest);
          }
          return;
        }
      } catch (_) {
        // The persistent queue remains available even if this UI closes.
      }
      if (attempts < 12) setTimeout(poll, 3000);
    };
    setTimeout(poll, 1000);
  }

  // 把主题同步给窗口外壳（WinForms 标题栏要跟着变）
  function syncWindowTheme() {
    try {
      const wv = window.chrome && window.chrome.webview;
      if (wv && wv.postMessage) wv.postMessage({ type: "theme", value: state.theme });
    } catch (e) { /* 浏览器里打开时忽略 */ }
  }

  function bindShellLifecycle() {
    try {
      const wv = window.chrome && window.chrome.webview;
      if (!wv || typeof wv.addEventListener !== "function") return;
      wv.addEventListener("message", async (event) => {
        const message = event && event.data;
        if (!message) return;
        if (message.type === "doubanSessionChanged") {
          await loadDoubanSession();
          if (message.saved) {
            toast("豆瓣登录状态已保存");
            if (($("#dbQ").value || "").trim()) runSearch();
          }
          return;
        }
        if (message.type !== "prepareClose") return;
        let result;
        try {
          result = await flushAllPending();
        } catch (err) {
          result = { ok: false, pending: commentDrafts.size, error: err.message };
        }
        wv.postMessage({
          type: "closeReady",
          requestId: message.requestId,
          ok: !!result.ok,
          pending: result.pending || 0,
          failures: result.failures || 0,
          error: result.error || "",
        });
      });
    } catch (e) { /* 普通浏览器没有桌面外壳消息通道 */ }
  }

  window.MediaVaultApp = { flushAllPending };

  /* ---------------- 启动 ---------------- */
  bindShellLifecycle();
  bindWebServiceLifecycle();
  init();

  function bindWebServiceLifecycle() {
    const ping = () => {
      fetch("/api/web-activity", { cache: "no-store" }).catch(() => {});
    };
    ping();
    setInterval(ping, 5000);
    window.addEventListener("pageshow", ping);
    window.addEventListener("pagehide", () => {
      const body = new Blob(["{}"], { type: "application/json" });
      navigator.sendBeacon("/api/web-detach", body);
    });
  }

  async function init() {
    loadUI();
    // 窗口外壳会用 ?theme=light|dark 下发主题，优先级高于本地存储
    try {
      const fromShell = new URLSearchParams(location.search).get("theme");
      if (fromShell === "light" || fromShell === "dark") state.theme = fromShell;
    } catch (e) { /* ignore */ }
    document.documentElement.dataset.theme = state.theme;
    syncWindowTheme();
    $("#q").value = state.q;

    try {
      const data = await apiJSON("/api/library");
      ITEMS = data.items || [];
      VAULT = data.vault || "";
      OBSIDIAN_MODE = data.obsidian !== false;
      doubanLogin = !!data.doubanLogin;
      if (data.warnings && data.warnings.length) {
        toast(`有 ${data.warnings.length} 篇笔记解析失败，已跳过写入`);
      }
    } catch (err) {
      $("#grid").innerHTML = `<div class="empty"><div class="big">⚠️</div>加载失败：${esc(err.message)}</div>`;
      return;
    }

    buildTypeRow();
    buildTagChips();
    bind();
    updateSortLabel();
    sync();
    openFromHash();
  }

  // 支持 #item=路径 直接打开详情，#new[?q=关键词] 打开添加窗口（方便分享/调试）
  function openFromHash() {
    const hash = location.hash || "";
    if (hash.startsWith("#new")) {
      openModal();
      const params = new URLSearchParams(hash.split("?")[1] || "");
      const q = params.get("q");
      const t = params.get("type");
      if (t) setDbType(t);
      if (q) {
        $("#dbQ").value = q;
        runSearch();
      }
      return;
    }
    // 支持 #sort=finish 之类直接切排序（也方便截图/分享）
    if (hash.startsWith("#sort=")) {
      const k = hash.slice(6);
      if (SORT_KEYS.includes(k)) { state.sort = k; sync(); }
      return;
    }
    const m = /^#item=(.+)$/.exec(hash);
    if (!m) return;
    const p = decodeURIComponent(m[1]);
    const it = ITEMS.find((x) => x.path === p);
    if (it) openDrawer(it);
  }

  /* ---------------- UI 持久化 ---------------- */
  function loadUI() {
    try {
      const raw = localStorage.getItem(STORE);
      if (!raw) return;
      const s = JSON.parse(raw);
      if (Array.isArray(s.types)) state.types = s.types;
      if (Array.isArray(s.tags)) state.tags = s.tags;
      if (typeof s.q === "string") state.q = s.q;
      if (["all", "incomplete", "completed"].includes(s.status)) state.status = s.status;
      else if (["done", "completed"].includes(s.status)) state.status = "completed";
      else if (["todo", "not_started", "in_progress", "incomplete"].includes(s.status)) {
        state.status = "incomplete";
      }
      if (s.score) state.score = s.score;
      if (s.sort) state.sort = SORT_KEYS.includes(s.sort) ? s.sort : "title";
      if (s.more) state.more = !!s.more;
      if (s.theme === "light" || s.theme === "dark") state.theme = s.theme;
    } catch (e) { /* 忽略 */ }
  }
  function saveUI() {
    try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* 忽略 */ }
  }

  /* ---------------- 筛选控件 ---------------- */
  function buildTypeRow() {
    const row = $("#typeRow");
    row.querySelectorAll(".chip").forEach((n) => n.remove());

    const counts = {};
    ITEMS.forEach((it) => { counts[it.type] = (counts[it.type] || 0) + 1; });

    const all = el("button", "chip" + (state.types.length ? "" : " on"));
    all.innerHTML = `全部<span class="n">${ITEMS.length}</span>`;
    all.onclick = () => { state.types = []; sync(); };
    row.appendChild(all);

    Object.keys(counts)
      .sort((a, b) => counts[b] - counts[a])
      .forEach((t) => {
        const m = meta(t);
        const b = el("button", "chip" + (state.types.includes(t) ? " on" : ""));
        b.dataset.type = t;
        b.innerHTML = `<span class="ico">${m.icon}</span>${esc(m.label)}<span class="n">${counts[t]}</span>`;
        b.onclick = () => {
          state.types = state.types.includes(t)
            ? state.types.filter((x) => x !== t)
            : state.types.concat(t);
          sync();
        };
        row.appendChild(b);
      });
  }

  function buildTagChips() {
    const freq = new Map();
    ITEMS.forEach((it) => it.tags.forEach((t) => freq.set(t, (freq.get(t) || 0) + 1)));
    const top = [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 16);

    const host = $("#tagChips");
    host.innerHTML = "";
    top.forEach(([t, n]) => {
      const b = el("button", "chip" + (state.tags.includes(t) ? " on" : ""));
      b.style.height = "26px";
      b.style.fontSize = "12px";
      b.innerHTML = `${esc(t)}<span class="n">${n}</span>`;
      b.onclick = () => {
        state.tags = state.tags.includes(t) ? state.tags.filter((x) => x !== t) : state.tags.concat(t);
        sync();
      };
      host.appendChild(b);
      host.appendChild(document.createTextNode(" "));
    });
  }

  /* ---------------- 排序下拉（自绘，风格统一） ---------------- */
  function renderSortPanel() {
    const panel = $("#sortPanel");
    panel.innerHTML = "";
    SORT_OPTIONS.forEach((o) => {
      const b = el("button", "drop-item" + (o.key === state.sort ? " on" : ""));
      b.type = "button";
      b.appendChild(el("span", null, o.label));
      b.appendChild(el("span", "tick", "✓"));
      b.onclick = () => {
        state.sort = o.key;
        closeSort();
        sync();
      };
      panel.appendChild(b);
    });
  }

  function updateSortLabel() {
    const opt = SORT_OPTIONS.find((o) => o.key === state.sort) || SORT_OPTIONS[0];
    $("#sortLabel").textContent = opt.label;
    renderSortPanel();
  }

  function closeSort() {
    $("#sortPanel").hidden = true;
    $("#sortBtn").classList.remove("on");
    $("#sortBtn").setAttribute("aria-expanded", "false");
  }

  function toggleSort() {
    if ($("#sortPanel").hidden) {
      $("#sortPanel").hidden = false;
      $("#sortBtn").classList.add("on");
      $("#sortBtn").setAttribute("aria-expanded", "true");
    } else {
      closeSort();
    }
  }

  function sync() {
    saveUI();
    buildTypeRow();
    buildTagChips();
    document.querySelectorAll("#statusSeg button").forEach((b) => {
      b.classList.toggle("on", b.dataset.v === state.status);
    });
    document.querySelectorAll("#scoreSeg button").forEach((b) => {
      b.classList.toggle("on", b.dataset.v === state.score);
    });
    updateSortLabel();
    $("#moreRow").style.display = state.more ? "" : "none";
    $("#moreBtn").textContent = state.more ? "收起筛选 ▴" : "更多筛选 ▾";
    render();
  }

  /* ---------------- 事件 ---------------- */
  function bind() {
    let t = null;
    $("#q").addEventListener("input", (e) => {
      clearTimeout(t);
      state.q = e.target.value;
      t = setTimeout(() => { saveUI(); render(); }, 120);
    });

    $("#statusSeg").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      state.status = b.dataset.v;
      sync();
    });

    $("#scoreSeg").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      state.score = b.dataset.v;
      sync();
    });

    $("#sortBtn").addEventListener("click", (e) => { e.stopPropagation(); toggleSort(); });
    document.addEventListener("click", (e) => { if (!$("#sortDrop").contains(e.target)) closeSort(); });
    $("#moreBtn").addEventListener("click", () => { state.more = !state.more; sync(); });

    /* 回到顶部：放在卡片区右侧的留白里，不压封面 */
    const toTop = $("#toTop");
    const gridWrap = document.querySelector(".grid") || document.querySelector(".container");
    const placeToTop = () => {
      if (!gridWrap) return;
      const gridRight = gridWrap.getBoundingClientRect().right;
      const gutter = Math.round(window.innerWidth - gridRight);
      toTop.style.left = "auto";
      // 按钮宽 42px，右侧再留 30px 空隙，离最后一列卡片远一点
      toTop.style.right = Math.max(24, gutter - 42 - 30) + "px";
    };
    const rail = $("#pageScrollRail");
    const thumb = $("#pageScrollThumb");
    const scrollMath = window.MediaVaultScroll;
    const railCardOffset = -17;
    const placePageScrollRail = () => {
      if (!rail) return;
      const filterBar = document.querySelector(".filters");
      const firstCard = document.querySelector(".grid .card");
      const filterBottom = filterBar ? filterBar.getBoundingClientRect().bottom + 4 : 0;
      const cardDocumentTop = firstCard
        ? window.scrollY + firstCard.getBoundingClientRect().top + railCardOffset
        : 0;
      rail.style.top = Math.ceil(Math.max(filterBottom, cardDocumentTop)) + "px";
    };
    const updatePageScroll = () => {
      toTop.classList.toggle("show", window.scrollY > 420);
      if (!rail || !thumb || !scrollMath) return;
      const viewport = window.innerHeight;
      const total = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
      const metric = scrollMath.metrics(total, viewport, rail.clientHeight);
      const offset = scrollMath.thumbOffset(window.scrollY, metric);
      thumb.style.height = metric.thumbHeight + "px";
      thumb.style.transform = `translateY(${offset}px)`;
      rail.classList.toggle("show", metric.maxScroll > 2);
      rail.setAttribute("aria-valuemax", String(Math.round(metric.maxScroll)));
      rail.setAttribute("aria-valuenow", String(Math.round(window.scrollY)));
      rail.setAttribute("aria-valuetext", `${Math.round(window.scrollY)} / ${Math.round(metric.maxScroll)}`);
    };
    const onScroll = updatePageScroll;
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", () => {
      placeToTop();
      placePageScrollRail();
      updatePageScroll();
    });
    toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
    if (rail && thumb && scrollMath) {
      let dragPointer = null;
      let dragStartY = 0;
      let dragStartScroll = 0;
      const currentMetric = () => scrollMath.metrics(
        Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
        window.innerHeight,
        rail.clientHeight,
      );
      const setFromPointer = (clientY, metric) => {
        const top = rail.getBoundingClientRect().top;
        window.scrollTo(0, scrollMath.scrollTopForPointer(clientY, top, metric));
      };
      rail.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        const metric = currentMetric();
        if (metric.maxScroll <= 0) return;
        event.preventDefault();
        rail.focus({ preventScroll: true });
        const thumbRect = thumb.getBoundingClientRect();
        if (event.clientY < thumbRect.top || event.clientY > thumbRect.bottom) {
          setFromPointer(event.clientY, metric);
        }
        dragPointer = event.pointerId;
        dragStartY = event.clientY;
        dragStartScroll = window.scrollY;
        rail.classList.add("dragging");
        rail.setPointerCapture(event.pointerId);
      });
      rail.addEventListener("pointermove", (event) => {
        if (dragPointer !== event.pointerId) return;
        const metric = currentMetric();
        if (metric.travel <= 0) return;
        const delta = event.clientY - dragStartY;
        const next = dragStartScroll + delta / metric.travel * metric.maxScroll;
        window.scrollTo(0, scrollMath.clamp(next, 0, metric.maxScroll));
      });
      const stopDragging = (event) => {
        if (dragPointer !== event.pointerId) return;
        dragPointer = null;
        rail.classList.remove("dragging");
        try { rail.releasePointerCapture(event.pointerId); } catch (_) { /* already released */ }
      };
      rail.addEventListener("pointerup", stopDragging);
      rail.addEventListener("pointercancel", stopDragging);
      rail.addEventListener("wheel", (event) => {
        if (currentMetric().maxScroll <= 0) return;
        event.preventDefault();
        window.scrollBy({ top: event.deltaY, behavior: "auto" });
      }, { passive: false });
      rail.addEventListener("keydown", (event) => {
        const metric = currentMetric();
        const step = Math.max(48, Math.round(window.innerHeight * 0.2));
        let top = null;
        if (event.key === "ArrowDown") top = window.scrollY + step;
        else if (event.key === "ArrowUp") top = window.scrollY - step;
        else if (event.key === "PageDown") top = window.scrollY + window.innerHeight * 0.85;
        else if (event.key === "PageUp") top = window.scrollY - window.innerHeight * 0.85;
        else if (event.key === "Home") top = 0;
        else if (event.key === "End") top = metric.maxScroll;
        if (top === null) return;
        event.preventDefault();
        window.scrollTo(0, scrollMath.clamp(top, 0, metric.maxScroll));
      });
    }
    placeToTop();
    requestAnimationFrame(() => {
      placePageScrollRail();
      updatePageScroll();
    });

    $("#themeBtn").addEventListener("click", () => {
      state.theme = state.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = state.theme;
      syncWindowTheme();
      saveUI();
    });

    $("#refreshBtn").addEventListener("click", async () => {
      try {
        await refreshLibrary(true);
      } catch (err) {
        toast("刷新失败：" + err.message);
      }
    });

    $("#backupBtn").addEventListener("click", async () => {
      const btn = $("#backupBtn");
      btn.disabled = true;
      try {
        const data = await apiJSON("/api/backup", {
          method: "POST",
          headers: WRITE_HEADERS,
          body: JSON.stringify({ label: "manual" }),
        });
        toast(`备份完成：${data.count} 篇`);
      } catch (err) {
        toast("备份失败：" + err.message);
      } finally {
        btn.disabled = false;
      }
    });

    window.addEventListener("focus", async () => {
      const now = Date.now();
      if (now - lastFocusRefresh < 1500 || saveQueues.size) return;
      if ($("#drawer").classList.contains("open") || $("#modal").classList.contains("open")) return;
      lastFocusRefresh = now;
      try {
        await refreshLibrary(false);
      } catch (err) {
        console.warn("自动刷新失败", err);
      }
    });

    $("#randomBtn").addEventListener("click", () => {
      const pool = (view().filter((it) => !it.status).length
        ? view().filter((it) => !it.status)
        : view().length ? view() : ITEMS);
      if (!pool.length) return;
      openDrawer(pool[Math.floor(Math.random() * pool.length)]);
    });

    $("#grid").addEventListener("click", (e) => {
      const card = e.target.closest(".card");
      if (!card) return;
      const item = ITEMS.find((it) => it.path === card.dataset.path);
      if (item) openDrawer(item);
    });

    $("#closeDrawer").addEventListener("click", closeDrawer);
    $("#scrim").addEventListener("click", closeDrawer);

    document.addEventListener("keydown", (e) => {
      const inField = /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.target.isContentEditable;
      if (e.key === "/" && !inField) {
        e.preventDefault();
        $("#q").focus();
        $("#q").select();
      } else if (e.key === "Escape") {
        closeSort();
        if ($("#modal").classList.contains("open")) closeModal();
        else if ($("#drawer").classList.contains("open")) closeDrawer();
        else if (state.q) { state.q = ""; $("#q").value = ""; saveUI(); render(); }
      }
    });

    /* ---- 添加媒体 ---- */
    $("#addBtn").addEventListener("click", openModal);
    $("#modalClose").addEventListener("click", closeModal);
    $("#newCancel").addEventListener("click", closeModal);
    $("#modalScrim").addEventListener("click", closeModal);
    $("#newSubmit").addEventListener("click", submitNew);
    $("#toggleManual").addEventListener("click", () => toggleManual());

    $("#diagBtn").addEventListener("click", async () => {
      $("#dbHint").textContent = "正在自检（真的去问一次豆瓣）…";
      $("#dbHint").className = "db-hint";
      try {
        const d = await (await fetch("/api/diag")).json();
        if (d.ok) {
          $("#dbHint").textContent =
            `豆瓣连接正常：${d.items} 条结果 / ${d.bytes} 字节 / Cookie ${d.hasCookie ? "有" : "无"}`;
          $("#dbHint").className = "db-hint";
        } else {
          const sample = d.sample ? "｜返回：" + d.sample.slice(0, 80) : "";
          $("#dbHint").textContent = `自检失败：${d.error || d.parseError || "未知错误"}${sample}`;
          $("#dbHint").className = "db-hint err";
        }
      } catch (err) {
        $("#dbHint").textContent = "自检请求失败：" + err.message;
        $("#dbHint").className = "db-hint err";
      }
    });

    $("#dbLoginBtn").addEventListener("click", () => openDoubanLogin());
    $("#dbImportBtn").addEventListener("click", () => $("#dbPluginFile").click());
    $("#dbPluginFile").addEventListener("change", importDoubanPluginSession);

    $("#dbType").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (b) setDbType(b.dataset.v);
    });
    $("#dbQ").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); runSearch(); }
    });
    $("#dbQ").addEventListener("input", () => {
      const currentInput = ($("#dbQ").value || "").trim();
      if (currentInput === dbLastInput) return;
      dbLastInput = currentInput;
      lastRenderedSearch = { key: "", items: [] };
      $("#dbResults").innerHTML = "";
      searchSequence += 1;
      if (searchController) searchController.abort();
      searchController = null;
      clearTimeout(dbSearchTimer);
      dbSearchTimer = setTimeout(() => {
        dbSearchTimer = null;
        if (($("#dbQ").value || "").trim().length >= 2) runSearch();
      }, 500);
    });

    $("#newType").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (b) setNewType(b.dataset.v);
    });
    $("#newStatus").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (b) setNewStatus(b.dataset.v === "done");
    });
    $("#newTitle").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submitNew(); }
    });

    const dz = $("#dropZone");
    dz.addEventListener("click", () => $("#fileInput").click());
    $("#fileInput").addEventListener("change", (e) => readImageFile(e.target.files && e.target.files[0]));
    ["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => {
      e.preventDefault();
      dz.classList.add("over");
    }));
    ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => {
      e.preventDefault();
      dz.classList.remove("over");
    }));
    dz.addEventListener("drop", (e) => {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      readImageFile(f);
    });
    document.addEventListener("paste", (e) => {
      if (!$("#modal").classList.contains("open")) return;
      const items = (e.clipboardData && e.clipboardData.items) || [];
      for (const item of items) {
        if (item.type && item.type.startsWith("image/")) {
          e.preventDefault();
          readImageFile(item.getAsFile());
          break;
        }
      }
    });
    $("#newImgUrl").addEventListener("input", (e) => {
      const v = e.target.value.trim();
      if (!v) { if (!newImageData) showPreview(""); return; }
      newImageData = null;
      showPreview(v);
    });
  }

  /* ---------------- 过滤 + 排序 ---------------- */
  function view() {
    const q = state.q.trim().toLowerCase();
    const terms = q ? q.split(/\s+/).filter(Boolean) : [];
    let out = ITEMS.filter((it) => {
      if (state.types.length && !state.types.includes(it.type)) return false;
      if (state.tags.length && !state.tags.every((t) => it.tags.includes(t))) return false;
      if (state.status === "incomplete" && it.statusGroup === "completed") return false;
      if (state.status === "completed" && it.statusGroup !== "completed") return false;
      if (state.score !== "all") {
        if (state.score === "0") { if (it.score != null) return false; }
        else {
          const ns = normScore(it);
          if (ns == null || ns < Number(state.score)) return false;
        }
      }
      return true;
    });
    if (terms.length) {
      const titleMatches = out.filter((it) => {
        const titleHay = [it.title, it.path, it.typeLabel].join(" ").toLowerCase();
        return terms.every((word) => titleHay.includes(word));
      });
      // 标题命中时只展示标题结果，避免剧情简介提到某角色却出现在搜索中。
      out = titleMatches.length ? titleMatches : out.filter((it) => {
        const hay = [it.title, it.path, it.desc, it.excerpt, it.comment,
          it.creator.join(" "), it.tags.join(" "), it.typeLabel].join(" ").toLowerCase();
        return terms.every((word) => hay.includes(word));
      });
    }

    const byTitle = (a, b) => naturalCompare(cleanTitle(a.title), cleanTitle(b.title));
    // 没有完成时间的，统一按 2026-06-26 参与排序
    // 未填完成时间的永远排在有日期的后面
    const finishOf = (it) => it.finishTime || "";
    if (state.sort === "myscore") out.sort((a, b) => (b.myScore ?? -1) - (a.myScore ?? -1) || (normScore(b) ?? -1) - (normScore(a) ?? -1) || byTitle(a, b));
    else if (state.sort === "finish") {
      out.sort((a, b) => {
        const fa = finishOf(a), fb = finishOf(b);
        if (fa !== fb) {
          if (!fa) return 1;
          if (!fb) return -1;
          return fb.localeCompare(fa);
        }
        return byTitle(a, b);
      });
    }
    else if (state.sort === "score") out.sort((a, b) => (networkSortScore(b) ?? -1) - (networkSortScore(a) ?? -1) || byTitle(a, b));
    else if (state.sort === "date") out.sort((a, b) => (b.date || "").localeCompare(a.date || "") || byTitle(a, b));
    else {
      // 同一系列按发行时间从旧到新排列，再按标题自然排序。
      out.sort((a, b) => {
        const group = naturalCompare(seriesSortKey(a), seriesSortKey(b));
        if (group !== 0) return group;
        const dateOrder = (a.date || "9999-99-99").localeCompare(b.date || "9999-99-99");
        return dateOrder || byTitle(a, b);
      });
    }

    VIEW = out;
    return out;
  }

  /* ---------------- 渲染 ---------------- */
  function render() {
    const list = view();

    // 顶部统计
    const watched = ITEMS.filter((it) => it.statusGroup === "completed").length;
    const statBits = [
      `共 <b>${ITEMS.length}</b> 部`,
      `已完成 <b>${watched}</b>`,
      `未完成 <b>${ITEMS.length - watched}</b>`,
    ];
    $("#stats").innerHTML = statBits.join('<span class="dot"> · </span>');

    $("#countLabel").textContent = list.length === ITEMS.length
      ? `${ITEMS.length} 部`
      : `${list.length} / ${ITEMS.length}`;

    const grid = $("#grid");
    if (!list.length) {
      grid.innerHTML = `<div class="empty"><div class="big">🎞️</div>没有符合条件的条目<br/>
        <button class="link-btn" id="resetAll" style="margin-top:10px">清空全部筛选</button></div>`;
      const r = $("#resetAll");
      if (r) r.onclick = () => {
        state.q = ""; state.types = []; state.tags = []; state.status = "all"; state.score = "all";
        $("#q").value = "";
        sync();
      };
      requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
      return;
    }

    const frag = document.createDocumentFragment();
    list.forEach((it, i) => frag.appendChild(card(it, i)));
    grid.innerHTML = "";
    grid.appendChild(frag);
    requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  }

  function card(it, index) {
    const eager = index < 12; // 首屏直接加载，避免空白
    const m = meta(it.type);
    const node = el("article", "card");
    node.dataset.path = it.path;

    const poster = el("div", "poster");
    poster.style.setProperty("--tint", m.tint);

    if (it.image) {
      const img = document.createElement("img");
      img.loading = eager ? "eager" : "lazy";
      img.alt = it.title;
      // 先挂事件再给 src：缓存命中时 load 会在赋值瞬间触发
      img.onload = () => { img.classList.add("loaded"); poster.classList.add("loaded"); };
      img.onerror = () => {
        img.remove();
        poster.classList.add("loaded");
        poster.appendChild(fallback(it, m));
      };
      img.src = imgSrc(it);
      if (img.complete && img.naturalWidth > 0) {
        img.classList.add("loaded");
        poster.classList.add("loaded");
      }
      poster.appendChild(img);
    } else {
      poster.classList.add("loaded");
      poster.appendChild(fallback(it, m));
    }

    // 只显示个人评分；没打过分就留空
    if (it.myScore != null) {
      const score = el("span", "score-badge", it.myScore.toFixed(1));
      score.title = `我的评分 ${it.myScore.toFixed(1)}`;
      poster.appendChild(score);
    }

    if (it.statusGroup === "completed") poster.appendChild(el("span", "watched-dot"));

    const hover = el("div", "hover");
    hover.appendChild(el("div", "h-title", cleanTitle(it.title)));
    const sb = [];
    if (it.myScore != null) sb.push("我的 " + it.myScore.toFixed(1));
    scoreSources(it).slice(0, 2).forEach((source) => {
      sb.push(`${source.label} ${source.value.replace(" / ", "/")}`);
    });
    if (sb.length) hover.appendChild(el("div", "h-meta", sb.join(" · ")));
    const bits = [];
    if (it.year) bits.push(it.year);
    if (it.creator.length) bits.push(it.creator.join(" / "));
    if (bits.length) hover.appendChild(el("div", "h-meta", bits.join(" · ")));
    if (it.tags.length) hover.appendChild(el("div", "h-meta", it.tags.slice(0, 3).join(" · ")));
    const snippet = it.desc || it.excerpt || it.comment;
    if (snippet) hover.appendChild(el("div", "h-meta", snippet.slice(0, 60) + (snippet.length > 60 ? "…" : "")));
    poster.appendChild(hover);

    node.appendChild(poster);

    const info = el("div", "info");
    info.appendChild(el("div", "t", cleanTitle(it.title)));
    const sub = [it.year, it.creator[0]].filter(Boolean).join(" · ") || it.typeLabel;
    info.appendChild(el("div", "m", sub));
    node.appendChild(info);

    return node;
  }

  function fallback(it, m) {
    const f = el("div", "fallback");
    f.style.background = `linear-gradient(150deg, ${m.tint}, #14161d)`;
    f.appendChild(document.createTextNode((it.title || "?").trim().charAt(0)));
    f.appendChild(el("small", null, m.label));
    return f;
  }

  /* ---------------- 详情抽屉 ---------------- */
  function openDrawer(it) {
    if (current && current.path !== it.path) flushComment();
    if (detailWidthObserver) {
      detailWidthObserver.disconnect();
      detailWidthObserver = null;
    }
    current = it;
    if (!baseItems.has(it.path)) baseItems.set(it.path, cloneItem(it));
    renderConflictBar();
    const m = meta(it.type);
    const body = $("#drawerBody");
    body.innerHTML = "";

    const top = el("div", "d-top");
    const dp = el("div", "d-poster");
    if (it.image) {
      const img = document.createElement("img");
      img.alt = it.title;
      img.onerror = () => { img.remove(); dp.appendChild(fallback(it, m)); };
      img.src = imgSrc(it);
      dp.appendChild(img);
    } else {
      dp.appendChild(fallback(it, m));
    }
    top.appendChild(dp);

    const right = el("div", "d-main");
    right.appendChild(el("div", "d-title", cleanTitle(it.title)));
    const sub = [it.year, m.label].concat(it.creator).filter(Boolean).join(" · ");
    right.appendChild(el("div", "d-sub", sub));
    if (it.tags.length) {
      const tags = el("div", "d-tags");
      it.tags.forEach((t) => tags.appendChild(el("span", null, t)));
      right.appendChild(tags);
    }
    top.appendChild(right);
    body.appendChild(top);

    const intro = it.desc || it.excerpt;
    if (intro) body.appendChild(el("div", "d-desc", intro));

    const warnings = itemWarningMessages(it);
    if (warnings.length) {
      const warning = el("div", "compat-warning");
      warnings.forEach((message) => warning.appendChild(el("div", null, message)));
      body.appendChild(warning);
    }

    const grid = el("div", "d-grid");
    body.appendChild(grid);

    /* 外部评分：来源固定、只读，仍由导入流程自动抓取 */
    const scoreField = el("div", "field score-field");
    const sources = scoreSources(it);
    const primary = sources.find((source) => source.key === "douban" || source.key === "metacritic");
    const rotten = sources.find((source) => source.key === "rotten");
    const imdb = sources.find((source) => source.key === "imdb");
    const metacritic = sources.find((source) => source.key === "metacritic");
    const bangumi = sources.find((source) => source.key === "bangumi");
    const scoreSummary = el("div", "score-summary");
    scoreSummary.dataset.count = String(sources.length);
    const appendScoreCard = (source) => {
      const card = el("div", "score-card");
      card.appendChild(el("label", null, source.label));
      const line = el("div", "score-value");
      line.appendChild(el("b", null, source.value.split(" / ")[0].replace("%", "")));
      line.appendChild(el("span", null, source.key === "rotten" ? "%" : " / " + source.maximum));
      card.appendChild(line);
      return card;
    };
    const scoreCards = [];
    if (primary) scoreCards.push(appendScoreCard(primary));
    if (rotten) scoreCards.push(appendScoreCard(rotten));
    if (imdb) scoreCards.push(appendScoreCard(imdb));
    if (metacritic && metacritic !== primary) {
      scoreCards.push(appendScoreCard(metacritic));
    }
    if (bangumi) scoreCards.push(appendScoreCard(bangumi));
    if (scoreCards.length === 5) {
      const topRow = el("div", "score-row");
      const bottomRow = el("div", "score-row");
      scoreCards.slice(0, 3).forEach((card) => topRow.appendChild(card));
      scoreCards.slice(3).forEach((card) => bottomRow.appendChild(card));
      scoreSummary.append(topRow, bottomRow);
    } else {
      scoreCards.forEach((card) => scoreSummary.appendChild(card));
    }
    scoreField.appendChild(scoreSummary);
    grid.appendChild(scoreField);

    /* 我的评分（可点星星修改） */
    const myField = el("div", "field my-score-field");
    myField.appendChild(el("label", null, "我的评分"));
    const myWrap = el("div", "stars");
    const num = el("span", "num");
    renderStars(myWrap, () => it.myScore, (value) => setMyScore(it, value, myWrap, num));
    myWrap.appendChild(num);
    updateNum(num, it.myScore);
    myField.appendChild(myWrap);
    grid.appendChild(myField);

    /* 状态：恢复 1.0 二元开关 */
    const statusWord = it.type === "game" ? ["已通关", "未通关", "游玩状态"]
      : it.type === "book" ? ["已读完", "未读完", "阅读状态"]
        : it.type === "music" ? ["已听完", "未听完", "收听状态"]
          : ["已看完", "未看完", "观看状态"];
    const statusField = el("div", "field status-field");
    statusField.appendChild(el("label", null, statusWord[2]));
    const sw = el("div", "switch status-choice" + (it.status ? " on" : ""));
    sw.id = "statusToggle";
    sw.tabIndex = 0;
    sw.setAttribute("role", "switch");
    sw.setAttribute("aria-checked", it.status ? "true" : "false");
    sw.setAttribute("aria-label", statusWord[2]);
    sw.setAttribute("aria-busy", "false");
    sw.innerHTML = `
      <span class="status-option" data-status="false">${statusWord[1]}</span>
      <span class="status-option" data-status="true">${statusWord[0]}</span>
    `;
    const syncStatusChoice = () => {
      sw.classList.toggle("on", it.status);
      sw.setAttribute("aria-checked", it.status ? "true" : "false");
      sw.querySelectorAll(".status-option").forEach((option) => {
        option.classList.toggle("on", (option.dataset.status === "true") === it.status);
      });
    };
    syncStatusChoice();
    const toggleStatus = async () => {
      if (sw.getAttribute("aria-busy") === "true") return;
      const before = it.status;
      const beforeProgress = it.progressStatus;
      sw.setAttribute("aria-busy", "true");
      it.status = !it.status;
      syncStatusChoice();
      const result = await save(it, { status: it.status });
      if (!result.ok && !result.conflict) {
        it.status = before;
        it.progressStatus = beforeProgress;
        syncStatusChoice();
      }
      sw.setAttribute("aria-busy", "false");
      render();
    };
    sw.onclick = toggleStatus;
    sw.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleStatus();
      }
    });
    statusField.appendChild(sw);
    grid.appendChild(statusField);

    /* 完成时间 */
    const finishField = el("div", "field finish-field");
    finishField.appendChild(el("label", null, "完成时间"));
    const finishInput = document.createElement("input");
    finishInput.type = "date";
    finishInput.className = "text";
    finishInput.value = it.finishTime || "";
    finishInput.addEventListener("change", async () => {
      const before = it.finishTime;
      it.finishTime = finishInput.value;
      const result = await save(it, { finishTime: finishInput.value });
      if (!result.ok && !result.conflict) {
        it.finishTime = before;
        finishInput.value = before || "";
      } else if (result.conflict) {
        finishInput.value = it.finishTime || "";
      }
    });
    finishField.appendChild(finishInput);
    grid.appendChild(finishField);

    const alignFinishFieldWidth = () => {
      if (!myWrap.isConnected || !finishField.isConnected) return;
      if (window.matchMedia("(max-width: 680px)").matches) {
        finishField.style.removeProperty("width");
        return;
      }
      const rowWidth = myWrap.getBoundingClientRect().width;
      finishField.style.width = Math.max(150, Math.ceil(rowWidth + 2)) + "px";
    };
    const scheduleFinishFieldAlignment = () => {
      requestAnimationFrame(() => {
        alignFinishFieldWidth();
        requestAnimationFrame(alignFinishFieldWidth);
      });
    };
    scheduleFinishFieldAlignment();
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(scheduleFinishFieldAlignment);
    }
    window.setTimeout(scheduleFinishFieldAlignment, 100);
    if (typeof ResizeObserver !== "undefined") {
      detailWidthObserver = new ResizeObserver(alignFinishFieldWidth);
      detailWidthObserver.observe(myWrap);
      if (myWrap.lastElementChild) detailWidthObserver.observe(myWrap.lastElementChild);
    }

    /* 游戏专属：平台 / 购买日期 / 价格 */
    if (it.type === "game") {
      const pfField = el("div", "field wide");
      pfField.appendChild(el("label", null, "游玩平台"));
      const chips = el("div", "chips-row");
      let pf = normalizePlatforms(it.platform);
      const rebuild = () => {
        chips.innerHTML = "";
        PLATFORM_CHOICES.forEach((p) => {
          const b = el("button", "chip" + (pf.includes(p) ? " on" : ""), p);
          b.type = "button";
          b.style.height = "28px";
          b.style.fontSize = "12px";
          b.onclick = async () => {
            const before = pf.slice();
            pf = pf.includes(p) ? pf.filter((x) => x !== p) : pf.concat(p);
            it.platform = pf.slice();
            rebuild();
            const result = await save(it, { platform: it.platform });
            if (!result.ok && !result.conflict) {
              pf = before;
              it.platform = before.slice();
              rebuild();
            } else if (result.conflict) {
              pf = normalizePlatforms(it.platform);
              rebuild();
            }
          };
          chips.appendChild(b);
        });
      };
      rebuild();
      pfField.appendChild(chips);
      grid.appendChild(pfField);

      const buyField = el("div", "field");
      buyField.appendChild(el("label", null, "购买日期"));
      const buyInput = document.createElement("input");
      buyInput.type = "date";
      buyInput.className = "text";
      buyInput.value = it.purchaseDate || "";
      buyInput.addEventListener("change", async () => {
        const before = it.purchaseDate;
        it.purchaseDate = buyInput.value;
        const result = await save(it, { purchaseDate: buyInput.value });
        if (!result.ok && !result.conflict) {
          it.purchaseDate = before;
          buyInput.value = before || "";
        } else if (result.conflict) {
          buyInput.value = it.purchaseDate || "";
        }
      });
      buyField.appendChild(buyInput);
      grid.appendChild(buyField);

      const priceField = el("div", "field");
      priceField.appendChild(el("label", null, "购买价格"));
      const priceInput = document.createElement("input");
      priceInput.type = "number";
      priceInput.min = "0";
      priceInput.step = "0.01";
      priceInput.className = "text";
      priceInput.placeholder = "只填数字，例如 198";
      priceInput.value = it.price == null ? "" : it.price;
      priceInput.addEventListener("change", async () => {
        const before = it.price;
        const raw = priceInput.value.trim();
        it.price = raw === "" ? null : Number(raw);
        priceInput.value = it.price == null ? "" : it.price;
        const result = await save(it, { price: it.price == null ? "" : it.price });
        if (!result.ok && !result.conflict) {
          it.price = before;
          priceInput.value = before == null ? "" : before;
        } else if (result.conflict) {
          priceInput.value = it.price == null ? "" : it.price;
        }
      });
      priceField.appendChild(priceInput);
      grid.appendChild(priceField);
    }

    /* 备注 */
    const noteField = el("div", "field wide");
    noteField.appendChild(el("label", null, "我的评价"));
    const ta = document.createElement("textarea");
    ta.placeholder = "写两句…（自动保存）";
    const savedDraft = commentDrafts.get(it.path);
    ta.value = savedDraft ? savedDraft.value : (it.comment || "");
    const hint = el("div", "save-hint");
    if (savedDraft) {
      savedDraft.hint = hint;
      hint.textContent = "有未保存草稿，点此重试";
      hint.className = "save-hint err";
      hint.onclick = () => {
        if (current && current.path === it.path) flushComment();
      };
    }
    ta.addEventListener("input", () => {
      it.comment = ta.value;
      hint.textContent = "输入中…";
      hint.className = "save-hint";
      hint.onclick = null;
      commentDrafts.set(it.path, { path: it.path, value: ta.value, hint });
      clearTimeout(commentTimer);
      commentTimer = setTimeout(flushComment, 700);
    });
    ta.addEventListener("blur", flushComment);
    noteField.appendChild(ta);
    noteField.appendChild(hint);
    grid.appendChild(noteField);

    /* 操作 */
    const actions = el("div", "d-actions");
    const open = document.createElement("a");
    open.className = "btn primary";
    open.href = obsidianUri(it.path);
    if (!OBSIDIAN_MODE) open.style.display = "none";
    open.textContent = "在 Obsidian 中打开";
    // 窗口版里 obsidian:// 由原生层打开（WebView2 不认自定义协议）
    open.addEventListener("click", (ev) => {
      const api = window.pywebview && window.pywebview.api;
      if (api && typeof api.open_external === "function") {
        ev.preventDefault();
        api.open_external(open.href);
      }
    });
    actions.appendChild(open);

    // 删除（移到 .media-app/trash，可恢复）
    const del = el("button", "btn danger", "删除");
    del.onclick = async () => {
      await flushComment();
      if (saveQueues.has(it.path)) await saveQueues.get(it.path);
      const name = cleanTitle(it.title);
      if (!confirm(`确定删除「${name}」？\n\n笔记和未被其他条目共享的主封面会被移到 .media-app\\trash\\，需要时可以手动恢复。`)) return;
      del.disabled = true;
      try {
        const data = await apiJSON("/api/delete", {
          method: "POST",
          headers: WRITE_HEADERS,
          body: JSON.stringify({ path: it.path, revision: it.revision }),
        });
        closeDrawer(false);
        ITEMS = ITEMS.filter((x) => x.path !== it.path);
        sync();
        const coverText = data.attachmentMoved
          ? "，封面也已回收"
          : data.attachmentShared
            ? "，共享封面已保留"
            : "";
        toast("已删除：" + name + coverText);
      } catch (e) {
        del.disabled = false;
        toast("删除失败：" + e.message);
      }
    };
    actions.appendChild(del);

    // 封面坏了（网络地址 / 本地文件无效）→ 提供一键修复
    if (it.imageBroken || /^https?:\/\//i.test(it.image || "")) {
      const label = it.imageBroken ? "封面坏了，重新下载" : "把封面存到本地";
      const fix = el("button", "btn", label);
      fix.onclick = async () => {
        await flushComment();
        if (saveQueues.has(it.path)) await saveQueues.get(it.path);
        fix.disabled = true;
        fix.textContent = "下载中…";
        try {
          const data = await apiJSON("/api/fix-image", {
            method: "POST",
            headers: WRITE_HEADERS,
            body: JSON.stringify({ path: it.path, revision: it.revision }),
          });
          Object.assign(it, data.item);
          render();
          closeDrawer();
          openDrawer(it);
          toast("封面已修复");
        } catch (err) {
          toast("修复失败：" + err.message);
          fix.disabled = false;
          fix.textContent = label;
        }
      };
      actions.appendChild(fix);
    }

    const clearScore = el("button", "btn", "清除我的评分");
    clearScore.onclick = () => setMyScore(it, null, myWrap, num);
    actions.appendChild(clearScore);

    body.appendChild(actions);

    $("#drawer").classList.add("open");
    $("#drawer").setAttribute("aria-hidden", "false");
    $("#scrim").classList.add("open");
  }

  function closeDrawer(flush = true) {
    if (flush) flushComment();
    if (detailWidthObserver) {
      detailWidthObserver.disconnect();
      detailWidthObserver = null;
    }
    $("#drawer").classList.remove("open");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#scrim").classList.remove("open");
    current = null;
    renderConflictBar();
  }

  function obsidianUri(path) {
    const file = path.replace(/\.md$/i, "");
    return "obsidian://open?vault=" + encodeURIComponent(VAULT) + "&file=" + encodeURIComponent(file);
  }

  /* ---------------- 保存 ---------------- */
  function applyServerItem(serverItem) {
    if (!serverItem || !serverItem.path) return;
    const idx = ITEMS.findIndex((x) => x.path === serverItem.path);
    let target = idx >= 0 ? ITEMS[idx] : null;
    if (!target) {
      target = {};
      ITEMS.push(target);
    }
    Object.assign(target, serverItem);
    if (current && current.path === serverItem.path && current !== target) {
      Object.assign(current, serverItem);
    }
  }

  function cloneItem(item) {
    return item ? JSON.parse(JSON.stringify(item)) : null;
  }

  function renderConflictBar() {
    const bar = $("#conflictBar");
    if (!bar) return;
    const conflict = current ? fieldConflicts.get(current.path) : null;
    if (!conflict) {
      bar.hidden = true;
      bar.innerHTML = "";
      return;
    }
    bar.innerHTML = "";
    bar.appendChild(el("div", "msg",
      "检测到 Obsidian 修改了同一字段。本地草稿已保留，请选择处理方式。"));
    const keep = el("button", "btn primary", "保留本地并重试");
    keep.onclick = async () => {
      keep.disabled = true;
      const pending = conflict.patches.slice();
      try {
        for (const patch of pending) {
          const result = await save(current.path, patch, { force: true });
          if (!result.ok) throw new Error("重试失败");
        }
        fieldConflicts.delete(current.path);
        saveFailures.delete(current.path);
        toast("已按本地值保存");
        renderConflictBar();
        render();
      } catch (err) {
        toast(err.message || "重试失败");
      } finally {
        keep.disabled = false;
      }
    };
    const useLatest = el("button", "btn", "采用最新值");
    useLatest.onclick = () => {
      const latest = conflict.latest;
      fieldConflicts.delete(current.path);
      saveFailures.delete(current.path);
      baseItems.set(current.path, cloneItem(latest));
      applyServerItem(latest);
      if (commentDrafts.has(current.path)) commentDrafts.delete(current.path);
      renderConflictBar();
      closeDrawer(false);
      openDrawer(ITEMS.find((item) => item.path === latest.path) || latest);
    };
    bar.appendChild(keep);
    bar.appendChild(useLatest);
    bar.hidden = false;
  }

  function save(itemOrPath, patch, options = {}) {
    const path = typeof itemOrPath === "string" ? itemOrPath : itemOrPath && itemOrPath.path;
    if (!path) return Promise.resolve({ ok: false, conflict: false });
    const previous = saveQueues.get(path) || Promise.resolve();
    const task = previous.catch(() => false).then(async () => {
      const item = ITEMS.find((x) => x.path === path);
      if (!item) throw new Error("这条笔记已不在当前库中，请刷新后重试");
      const base = baseItems.get(path) || cloneItem(item);
      const groupKeys = conflictGroup(patch, item);
      const attempt = async (revision) => apiJSON("/api/item", {
        method: "POST",
        headers: WRITE_HEADERS,
        body: JSON.stringify(Object.assign({ path, revision }, patch)),
      });
      try {
        let data;
        try {
          data = await attempt(item.revision);
        } catch (err) {
          if (!err.data || !err.data.conflict || options.force) throw err;
          const latest = err.data.latest;
          if (!latest) throw err;
          const fieldConflict = groupKeys.length > 0
            && threeWayGroupDecision(base, item, latest, groupKeys) === "conflict";
          if (fieldConflict) {
            const dirtyKeys = dirtyKeysBetween(base, item, groupKeys);
            const merged = mergeLatestWithLocal(latest, item, dirtyKeys);
            applyServerItem(merged);
            baseItems.set(path, cloneItem(latest));
            const conflict = fieldConflicts.get(path) || { latest, patches: [] };
            conflict.latest = latest;
            conflict.patches.push(Object.assign({}, patch));
            fieldConflicts.set(path, conflict);
            saveFailures.set(path, "字段冲突");
            if (current && current.path === path) renderConflictBar();
            toast("检测到同字段冲突，本地草稿已保留");
            return { ok: false, conflict: true };
          }
          applyServerItem(mergeLatestWithLocal(
            latest,
            item,
            dirtyKeysBetween(base, item, groupKeys),
          ));
          baseItems.set(path, cloneItem(latest));
          data = await attempt(latest.revision);
        }
        applyServerItem(data.item);
        baseItems.set(path, cloneItem(data.item));
        if (!fieldConflicts.has(path)) saveFailures.delete(path);
        if (current && current.path === path) renderConflictBar();
        const warning = data.item && data.item.calloutWarning;
        if (!options.quiet) {
          toast(warning
            ? "字段已保存，但" + calloutWarningText(warning)
            : "已保存");
        }
        return { ok: true, conflict: false, warning: warning || null };
      } catch (err) {
        console.error(err);
        saveFailures.set(path, err.message || "保存失败");
        toast("保存失败：" + err.message);
        return { ok: false, conflict: false };
      }
    }).catch((err) => {
      console.error(err);
      saveFailures.set(path, err.message || "保存失败");
      toast("保存失败：" + (err.message || "未知错误"));
      return { ok: false, conflict: false };
    }).finally(() => {
      if (saveQueues.get(path) === task) saveQueues.delete(path);
    });
    saveQueues.set(path, task);
    return task;
  }

  function flushComment() {
    return flushCommentPath(current && current.path);
  }

  async function flushCommentPath(path) {
    clearTimeout(commentTimer);
    commentTimer = null;
    const pending = path ? commentDrafts.get(path) : null;
    if (!pending) return true;
    if (pending.inflight) return pending.inflight;
    const flight = (async () => {
      const item = ITEMS.find((x) => x.path === pending.path);
      const result = await save(item || pending.path, { comment: pending.value }, { quiet: true });
      if (result.ok && commentDrafts.get(pending.path) === pending) {
        commentDrafts.delete(pending.path);
      }
      if (pending.hint) {
        pending.hint.textContent = result.ok ? "已保存" : "保存失败";
        pending.hint.className = "save-hint " + (result.ok ? "ok" : "err");
        pending.hint.onclick = null;
        if (!result.ok) {
          pending.hint.textContent = "保存失败，点此重试";
          pending.hint.onclick = () => {
            if (current && current.path === pending.path && commentDrafts.has(pending.path)) {
              pending.inflight = null;
              flushComment();
            }
          };
        }
      }
      return result.ok;
    })();
    pending.inflight = flight;
    try {
      return await flight;
    } finally {
      if (pending.inflight === flight) pending.inflight = null;
    }
  }

  async function flushAllPending() {
    clearTimeout(commentTimer);
    commentTimer = null;
    const commentResults = [];
    const settledQueues = [];
    const flushedDrafts = new Map();
    for (let round = 0; round < 20; round++) {
      const unflushedComments = [...commentDrafts.entries()]
        .filter(([path, draft]) => flushedDrafts.get(path) !== draft);
      unflushedComments.forEach(([path, draft]) => flushedDrafts.set(path, draft));
      if (unflushedComments.length) {
        commentResults.push(...await Promise.all(
          unflushedComments.map(([path]) => flushCommentPath(path))
        ));
      }

      const queueEntries = [...saveQueues.entries()];
      if (queueEntries.length) {
        const settled = await Promise.allSettled(
          queueEntries.map((entry) => entry[1])
        );
        settledQueues.push(...settled);
        settled.forEach((entry, index) => {
          if (entry.status !== "fulfilled"
              || !entry.value
              || entry.value.ok !== true) {
            const path = queueEntries[index][0];
            const reason = entry.status === "rejected"
              ? entry.reason
              : "保存未成功";
            saveFailures.set(path, String(reason && reason.message || reason || "保存失败"));
          }
        });
      }
      const allDraftsFlushed = [...commentDrafts.entries()]
        .every(([path, draft]) => flushedDrafts.get(path) === draft);
      if (!saveQueues.size && allDraftsFlushed) break;
    }
    const pending = commentDrafts.size;
    const outcome = window.MediaVaultSaveOutcome.assessSaveOutcome({
      commentResults,
      settledQueues,
      pendingDrafts: pending,
      failedSaves: saveFailures.size,
      fieldConflicts: fieldConflicts.size,
    });
    return {
      ok: outcome.ok,
      pending,
      failures: outcome.failures,
    };
  }

  /* ---------------- 添加媒体 ---------------- */
  const CREATOR_LABEL = { movie: "导演", teleplay: "导演", game: "发行商", book: "作者" };

  function setNewType(t) {
    newType = t;
    document.querySelectorAll("#newType button").forEach((b) => b.classList.toggle("on", b.dataset.v === t));
    const label = CREATOR_LABEL[t] || "导演";
    $("#creatorLabel").textContent = label;
    $("#newCreator").placeholder = t === "game" ? "例如：Team Cherry" : t === "book" ? "例如：刘慈欣" : "例如：丹尼斯·维伦纽瓦";
  }

  function setNewStatus(done) {
    newStatus = done;
    document.querySelectorAll("#newStatus button").forEach((b) => {
      b.classList.toggle("on", b.dataset.v === (done ? "done" : "todo"));
    });
  }

  function showPreview(src) {
    const host = $("#imgPreview");
    host.innerHTML = "";
    if (!src) return;
    const img = document.createElement("img");
    img.alt = "封面预览";
    img.onerror = () => { host.textContent = "图片无法加载"; };
    img.src = src;
    host.appendChild(img);
  }

  function readImageFile(file) {
    if (!file || !/^image\//.test(file.type || "")) return;
    if (file.size > 12 * 1024 * 1024) { toast("图片太大了（上限 12MB）"); return; }
    const fr = new FileReader();
    fr.onload = () => {
      newImageData = String(fr.result);
      $("#newImgUrl").value = "";
      showPreview(newImageData);
    };
    fr.readAsDataURL(file);
  }

  function openModal() {
    if (dbSearchTimer) {
      clearTimeout(dbSearchTimer);
      dbSearchTimer = null;
    }
    searchSequence += 1;
    if (searchController) searchController.abort();
    searchController = null;
    dbLastInput = "";
    lastRenderedSearch = { key: "", items: [] };
    newImageData = null;
    ["newTitle", "newDate", "newScore", "newCreator", "newTags", "newDesc", "newImgUrl", "dbQ"].forEach((id) => {
      const n = document.getElementById(id);
      if (n) n.value = "";
    });
    showPreview("");
    $("#newHint").textContent = "";
    $("#dbResults").innerHTML = "";
    $("#dbHint").textContent = "";
    $("#dbHint").className = "db-hint";
    setNewType("movie");
    setNewStatus(false);
    setDbType("movie");
    toggleManual(false);
    if (doubanLogin) {
      $("#dbSession").hidden = true;
    } else {
      loadDoubanSession();
    }
    $("#modal").classList.add("open");
    $("#modal").setAttribute("aria-hidden", "false");
    $("#modalScrim").classList.add("open");
    setTimeout(() => $("#dbQ").focus(), 60);
  }

  function closeModal() {
    if (dbSearchTimer) {
      clearTimeout(dbSearchTimer);
      dbSearchTimer = null;
    }
    searchSequence += 1;
    if (searchController) searchController.abort();
    searchController = null;
    dbLastInput = "";
    lastRenderedSearch = { key: "", items: [] };
    $("#modal").classList.remove("open");
    $("#modal").setAttribute("aria-hidden", "true");
    $("#modalScrim").classList.remove("open");
  }

  async function submitNew() {
    const title = $("#newTitle").value.trim();
    if (!title) { toast("请先填写标题"); $("#newTitle").focus(); return; }

    const scoreRaw = $("#newScore").value;
    let score = null;
    if (scoreRaw !== "") {
      score = Number(scoreRaw);
      const maxScore = newType === "game" ? 100 : 10;
      if (!Number.isFinite(score) || score < 0 || score > maxScore) {
        toast(`评分要在 0–${maxScore} 之间`);
        return;
      }
    }

    const payload = {
      type: newType,
      title,
      autoRatings: true,
      status: newStatus,
      score,
      datePublished: $("#newDate").value || "",
      creator: $("#newCreator").value.trim(),
      tags: $("#newTags").value.split(/[,，、\s]+/).map((s) => s.trim()).filter(Boolean),
      desc: $("#newDesc").value.trim(),
      imageData: newImageData || null,
      imageUrl: $("#newImgUrl").value.trim() || null,
    };

    const btn = $("#newSubmit");
    btn.disabled = true;
    btn.textContent = "创建中…";
    $("#newHint").textContent = "";
    try {
      const data = await apiJSON("/api/create", {
        method: "POST",
        headers: WRITE_HEADERS,
        body: JSON.stringify(payload),
      });
      ITEMS.push(data.item);
      closeModal();
      render();
      openDrawer(data.item);
      const ratingQueued = data.ratingJob
        && data.ratingJob.status !== "queue_failed";
      toast(ratingQueued
        ? `已创建《${data.item.title}》，外部评分将在后台更新`
        : data.ratingJob
          ? `已创建《${data.item.title}》，但评分任务保存失败`
        : `已创建《${data.item.title}》`);
      if (ratingQueued) watchRatingJob(data.item.path);
    } catch (err) {
      $("#newHint").textContent = "创建失败：" + err.message;
      $("#newHint").className = "save-hint err";
    } finally {
      btn.disabled = false;
      btn.textContent = "创建";
    }
  }

  /* ---------------- 星级控件（半星可点，值 0–10） ---------------- */
  function paintStars(glyphs, value) {
    const list = glyphs.children;
    for (let i = 0; i < list.length; i++) {
      const full = (i + 1) * 2;
      const s = list[i];
      s.classList.toggle("full", value != null && value >= full);
      s.classList.toggle("half", value != null && value === full - 1);
    }
  }

  function renderStars(host, getValue, onChange) {
    const old = host.querySelector(".starg");
    if (old) old.remove();

    const glyphs = el("div", "starg");
    for (let i = 0; i < 5; i++) {
      const s = el("span", "st");
      s.textContent = "★";
      const left = el("span", "hit l");
      const right = el("span", "hit r");
      left.addEventListener("mouseenter", () => paintStars(glyphs, i * 2 + 1));
      right.addEventListener("mouseenter", () => paintStars(glyphs, i * 2 + 2));
      left.addEventListener("click", (e) => { e.stopPropagation(); onChange(i * 2 + 1); });
      right.addEventListener("click", (e) => { e.stopPropagation(); onChange(i * 2 + 2); });
      s.append(left, right);
      glyphs.appendChild(s);
    }
    glyphs.addEventListener("mouseleave", () => paintStars(glyphs, getValue()));
    host.insertBefore(glyphs, host.firstChild);
    host._paint = (v) => paintStars(glyphs, v);
    paintStars(glyphs, getValue());
  }

  function updateNum(node, value) {
    if (!node) return;
    node.textContent = value == null ? "未评分" : value.toFixed(1) + " / 10";
    node.classList.toggle("muted", value == null);
  }

  async function setMyScore(it, value, host, num) {
    const before = it.myScore;
    it.myScore = value;
    if (host && host._paint) host._paint(value);
    updateNum(num, value);
    const result = await save(it, { myScore: value });
    if (!result.ok && !result.conflict) {
      it.myScore = before;
      if (host && host._paint) host._paint(before);
      updateNum(num, before);
    } else if (result.conflict) {
      if (host && host._paint) host._paint(it.myScore);
      updateNum(num, it.myScore);
    }
    render();
  }

  /* ---------------- 豆瓣搜索添加 ---------------- */
  let dbType = "movie";
  let searchSequence = 0;
  let searchController = null;
  let dbSearchTimer = null;
  let dbLastInput = "";
  let lastRenderedSearch = { key: "", items: [] };

  async function loadDoubanSession() {
    const label = $("#dbSessionText");
    if (!label) return;
    const session = $("#dbSession");
    const loginButton = $("#dbLoginBtn");
    const importButton = $("#dbImportBtn");
    try {
      const data = await apiJSON("/api/douban/session");
      doubanLogin = !!data.loggedIn;
      session.hidden = doubanLogin;
      if (doubanLogin) return;
      loginButton.hidden = false;
      importButton.hidden = false;
      label.textContent = "未登录：公开搜索可用；如使用插件登录态，请手动选择 data.json，程序不会在后台读取";
      label.className = "db-session-state";
    } catch (err) {
      session.hidden = false;
      loginButton.hidden = false;
      importButton.hidden = false;
      label.textContent = "登录状态检查失败：" + err.message;
      label.className = "db-session-state";
    }
  }

  async function saveDoubanCookie(cookie) {
    const data = await apiJSON("/api/douban/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookie }),
    });
    doubanLogin = !!data.loggedIn;
    await loadDoubanSession();
    return data;
  }

  async function openDoubanLogin() {
    try {
      const wv = window.chrome && window.chrome.webview;
      if (wv && wv.postMessage) {
        wv.postMessage({ type: "doubanLogin" });
        return;
      }
      const cookie = window.prompt(
        "请粘贴豆瓣 Cookie。至少需要包含 dbcl2："
      );
      if (!cookie) return;
      await saveDoubanCookie(cookie);
      toast("豆瓣登录状态已保存");
      if (($("#dbQ").value || "").trim()) runSearch();
    } catch (err) {
      $("#dbHint").textContent = "豆瓣登录失败：" + err.message;
      $("#dbHint").className = "db-hint err";
    }
  }

  async function importDoubanPluginSession(event) {
    const input = event.target;
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) return;
    if (file.size > 128 * 1024) {
      toast("插件配置文件过大");
      return;
    }
    try {
      const raw = JSON.parse(await file.text());
      let cookie = String(raw.loginCookiesContent || "").trim();
      if (!cookie && String(raw.loginHeadersContent || "").trim().startsWith("{")) {
        const headers = JSON.parse(raw.loginHeadersContent);
        cookie = String(headers.Cookie || headers.cookie || "").trim();
      }
      if (!cookie) throw new Error("文件中没有豆瓣登录 Cookie");
      await saveDoubanCookie(cookie);
      toast("已从 Obsidian 豆瓣插件导入登录态");
      if (($("#dbQ").value || "").trim()) runSearch();
    } catch (err) {
      $("#dbHint").textContent = "导入登录态失败：" + err.message;
      $("#dbHint").className = "db-hint err";
    }
  }

  function setDbType(t) {
    if (dbType === t) return;
    dbType = t;
    document.querySelectorAll("#dbType button").forEach((b) => b.classList.toggle("on", b.dataset.v === t));
    if (($("#dbQ").value || "").trim()) runSearch();
  }

  async function runSearch() {
    if (dbSearchTimer) {
      clearTimeout(dbSearchTimer);
      dbSearchTimer = null;
    }
    const q = ($("#dbQ").value || "").trim();
    if (!q) return;
    dbLastInput = q;
    const searchKey = `${dbType}\u0000${q}`;
    const requestId = ++searchSequence;
    const requestType = dbType;
    if (searchController) searchController.abort();
    searchController = new AbortController();
    const controller = searchController;
    $("#dbHint").textContent = "搜索中…";
    $("#dbHint").className = "db-hint";
    try {
      const data = await apiJSON(
        `/api/search?q=${encodeURIComponent(q)}&type=${encodeURIComponent(requestType)}`,
        { signal: controller.signal },
      );
      if (requestId !== searchSequence
          || ($("#dbQ").value || "").trim() !== q
          || dbType !== requestType) return;
      const items = data.items || [];
      if (!items.length
          && lastRenderedSearch.key === searchKey
          && lastRenderedSearch.items.length) {
        $("#dbHint").textContent = "豆瓣这次没有返回结果，已保留上一次结果，可以稍后重试";
        return;
      }
      renderDbResults(items);
      if (items.length) {
        lastRenderedSearch = { key: searchKey, items: items.slice() };
      } else {
        lastRenderedSearch = { key: searchKey, items: [] };
      }
    } catch (err) {
      if (err.name === "AbortError" || requestId !== searchSequence) return;
      const hint = requestType === "game" && !doubanLogin
        ? "游戏搜索需要豆瓣登录：先在 Obsidian 豆瓣插件中登录，再手动点击“导入 Obsidian 登录态”选择 data.json。"
        : `搜索失败：${err.message}（可以改用「手动填写」）`;
      $("#dbHint").textContent = hint;
      $("#dbHint").className = "db-hint err";
    } finally {
      if (searchController === controller) searchController = null;
    }
  }

  function renderDbResults(list) {
    const host = $("#dbResults");
    host.innerHTML = "";
    if (!list.length) {
      $("#dbHint").textContent = "没搜到。换个关键词，或点底部「自检」看看豆瓣那边返回了什么。";
      return;
    }
    $("#dbHint").textContent = `找到 ${list.length} 条，点一下即可添加`;
    list.forEach((it) => {
      const row = el("div", "db-item");
      const img = document.createElement("img");
      img.className = "db-cover";
      img.loading = "lazy";
      img.alt = "";
      img.onerror = () => { img.style.visibility = "hidden"; };
      // 走本地代理：豆瓣图床对浏览器直连会 403
      img.src = it.cover ? "/media?p=" + encodeURIComponent(it.cover) : "";
      row.appendChild(img);

      const main = el("div", "db-main");
      main.appendChild(el("div", "db-title", cleanTitle(it.title)));
      const sub = [it.year, it.sub].filter(Boolean).join(" · ");
      main.appendChild(el("div", "db-sub", sub));
      row.appendChild(main);
      row.appendChild(el("span", "db-add", "添加"));

      row.addEventListener("click", () => importItem(it, row));
      host.appendChild(row);
    });
  }

  async function importItem(it, row) {
    row.classList.add("busy");
    const badge = row.querySelector(".db-add");
    if (badge) badge.textContent = "添加中…";
    try {
      const data = await apiJSON("/api/import", {
        method: "POST",
        headers: WRITE_HEADERS,
        body: JSON.stringify(it),
      });
      const item = data.item;
      if (item.existed) {
        const ratingQueued = item.ratingJob
          && item.ratingJob.status !== "queue_failed";
        toast(ratingQueued
          ? `《${item.title}》已存在，已加入后台补分`
          : item.ratingJob
            ? `《${item.title}》已存在，但评分任务保存失败`
          : `《${item.title}》已经在库里了`);
        if (ratingQueued) watchRatingJob(item.path);
      } else {
        ITEMS.push(item);
        const ratingQueued = item.ratingJob
          && item.ratingJob.status !== "queue_failed";
        toast(ratingQueued
          ? `已添加《${item.title}》，外部评分将在后台更新`
          : item.ratingJob
            ? `已添加《${item.title}》，但评分任务保存失败`
          : `已添加《${item.title}》`);
        if (ratingQueued) watchRatingJob(item.path);
      }
      closeModal();
      render();
      openDrawer(item);
    } catch (err) {
      row.classList.remove("busy");
      if (badge) badge.textContent = "添加";
      $("#dbHint").textContent = "添加失败：" + err.message;
      $("#dbHint").className = "db-hint err";
    }
  }

  function toggleManual(force) {
    const showManual = force === undefined ? $("#manualPane").hidden : force;
    $("#manualPane").hidden = !showManual;
    $("#searchPane").hidden = showManual;
    $("#newSubmit").hidden = !showManual;
    $("#toggleManual").textContent = showManual ? "← 返回搜索" : "找不到？手动填写";
    if (showManual) setTimeout(() => $("#newTitle").focus(), 50);
  }

  /* ---------------- 小工具 ---------------- */
  let toastTimer = null;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 1600);
  }
})();
