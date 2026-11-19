(function () {
  "use strict";

  var $ = function (id) {
    return document.getElementById(id);
  };

  var profiles = {};
  try {
    profiles = JSON.parse($("profiles-data").textContent || "{}");
  } catch (err) {
    profiles = {};
  }
  var defaultProfile = window.__DEFAULT_PROFILE__ || "emoji";

  var el = {
    banner: $("banner"),
    link: $("link"),
    paste: $("paste"),
    hint: $("hint"),
    profiles: $("profiles"),
    go: $("go"),
    goText: $("go-text"),
    width: $("opt-width"),
    fps: $("opt-fps"),
    maxmb: $("opt-maxmb"),
    start: $("opt-start"),
    duration: $("opt-duration"),
    colors: $("opt-colors"),
    dither: $("opt-dither"),
    progress: $("progress"),
    stage: $("stage"),
    pct: $("pct"),
    bar: $("bar"),
    pmsg: $("pmsg"),
    error: $("error"),
    errmsg: $("errmsg"),
    retry: $("retry"),
    result: $("result"),
    badge: $("r-badge"),
    img: $("r-img"),
    meta: $("r-meta"),
    dl: $("r-dl"),
    warn: $("r-warn"),
    guide: $("r-guide")
  };

  var isIOS =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var isAndroid = /Android/i.test(navigator.userAgent);

  var current = defaultProfile;
  var busy = false;

  /* ---------------- 预设 ---------------- */

  function renderProfiles() {
    el.profiles.innerHTML = "";
    Object.keys(profiles).forEach(function (key) {
      var p = profiles[key];
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "profile";
      btn.setAttribute("aria-pressed", key === current ? "true" : "false");
      btn.dataset.key = key;

      var strong = document.createElement("strong");
      strong.textContent = p.label || key;
      var small = document.createElement("small");
      small.textContent = p.desc || "";
      btn.appendChild(strong);
      btn.appendChild(small);

      btn.addEventListener("click", function () {
        current = key;
        Array.prototype.forEach.call(el.profiles.children, function (node) {
          node.setAttribute("aria-pressed", node.dataset.key === key ? "true" : "false");
        });
        applyProfileDefaults();
      });

      el.profiles.appendChild(btn);
    });
  }

  function applyProfileDefaults() {
    var p = profiles[current] || {};
    if (p.width != null) el.width.value = p.width;
    if (p.fps != null) el.fps.value = p.fps;
    if (p.max_mb != null) el.maxmb.value = p.max_mb;
    if (p.colors != null) el.colors.value = String(p.colors);
    if (p.dither != null) el.dither.value = p.dither;
  }

  /* ---------------- 提示 ---------------- */

  function setHint(text, kind) {
    el.hint.textContent = text;
    el.hint.className = "hint" + (kind ? " " + kind : "");
  }

  var defaultHintText = el.hint.textContent;

  function resetHint() {
    setHint(defaultHintText, "");
  }

  /* ---------------- 剪贴板 ---------------- */

  var STATUS_RE = /https?:\/\/(?:www\.|mobile\.)?(?:x|twitter)\.com\/[^\s"'<>]*?\/status(?:es)?\/\d+/i;
  var MEDIA_RE = /https?:\/\/video\.twimg\.com\/[^\s"'<>]+/i;

  function extractLink(text) {
    var status = text.match(STATUS_RE);
    if (status) return status[0];
    var media = text.match(MEDIA_RE);
    if (media) return media[0];
    var any = text.match(/https?:\/\/[^\s"'<>]+/);
    return any ? any[0] : text.trim();
  }

  function fillLink(text) {
    if (!text) return;
    el.link.value = extractLink(text);
    el.link.focus();
    el.link.setSelectionRange(el.link.value.length, el.link.value.length);
  }

  el.paste.addEventListener("click", function () {
    var clip = navigator.clipboard;
    if (!clip || typeof clip.readText !== "function" || !window.isSecureContext) {
      setHint("当前是局域网 http 访问，浏览器不允许网页读剪贴板。在输入框里长按就能粘贴。", "warn");
      el.link.focus();
      return;
    }
    clip
      .readText()
      .then(function (text) {
        if (!text || !text.trim()) {
          setHint("剪贴板是空的。", "warn");
          return;
        }
        fillLink(text);
        resetHint();
      })
      .catch(function () {
        setHint("没读到剪贴板内容（可能没授权），在输入框里长按手动粘贴即可。", "warn");
        el.link.focus();
      });
  });

  /* ---------------- 进度 ---------------- */

  function showProgress() {
    el.progress.hidden = false;
    el.error.hidden = true;
    el.result.hidden = true;
    setProgress(0, "准备中", "");
  }

  function setProgress(value, stage, message) {
    var pct = Math.max(0, Math.min(100, Math.round(value * 100)));
    el.bar.style.width = pct + "%";
    el.pct.textContent = pct + "%";
    if (stage) el.stage.textContent = stage;
    el.pmsg.textContent = message || "";
  }

  function hideProgress() {
    el.progress.hidden = true;
  }

  /* ---------------- 提交 ---------------- */

  function collectPayload() {
    return {
      input: el.link.value.trim(),
      profile: current,
      width: el.width.value,
      fps: el.fps.value,
      max_mb: el.maxmb.value,
      start: el.start.value || 0,
      duration: el.duration.value || 0,
      colors: el.colors.value,
      dither: el.dither.value
    };
  }

  function setBusy(flag) {
    busy = flag;
    el.go.disabled = flag;
    el.go.classList.toggle("loading", flag);
    el.goText.textContent = flag ? "转换中…" : "生成微信 GIF";
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  async function start() {
    if (busy) return;
    var payload = collectPayload();
    if (!payload.input) {
      setHint("先把推文链接粘进来。", "error");
      el.link.focus();
      return;
    }

    resetHint();
    showProgress();
    setBusy(true);

    try {
      var res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      var data = await res.json().catch(function () {
        return {};
      });
      if (!res.ok) {
        throw new Error(data.error || "提交失败（HTTP " + res.status + "）");
      }

      rememberJob(data.id);
      var job = await poll(data.id);
      hideProgress();
      renderResult(job.result);
    } catch (err) {
      hideProgress();
      showError(err && err.message ? err.message : "未知错误");
    } finally {
      setBusy(false);
    }
  }

  async function poll(jobId) {
    var started = Date.now();
    for (;;) {
      var res = await fetch("/api/jobs/" + jobId, { cache: "no-store" });
      if (!res.ok) {
        if (res.status === 404) throw new Error("任务已失效（服务可能重启过），重新生成一次即可。");
        throw new Error("查询进度失败（HTTP " + res.status + "）");
      }
      var job = await res.json();
      setProgress(job.progress || 0, job.stage || "处理中", job.message || "");

      if (job.state === "done") return job;
      if (job.state === "error") throw new Error(job.error || "转换失败");
      if (Date.now() - started > 8 * 60 * 1000) throw new Error("等了 8 分钟还没完成，先放弃。可以换更小的宽度或更短的时长再试。");

      await sleep(700);
    }
  }

  /* ---------------- 结果 ---------------- */

  function showError(message) {
    el.result.hidden = true;
    el.error.hidden = false;
    el.errmsg.textContent = message;
    el.error.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function metaItem(label, value) {
    var wrap = document.createElement("div");
    wrap.className = "meta-item";
    var span = document.createElement("span");
    span.textContent = label;
    var strong = document.createElement("strong");
    strong.textContent = value;
    wrap.appendChild(span);
    wrap.appendChild(strong);
    return wrap;
  }

  function renderResult(result) {
    el.error.hidden = true;
    el.result.hidden = false;

    var bust = result.url + "?t=" + Date.now();
    el.img.src = bust;
    el.img.alt = "生成的 GIF，长按可保存";

    el.badge.textContent = result.size_text || formatSize(result.size);
    el.badge.className = "badge" + (result.oversized ? " over" : "");

    el.dl.href = bust;
    if (isIOS) {
      el.dl.removeAttribute("download");
      el.dl.setAttribute("target", "_blank");
      el.dl.setAttribute("rel", "noopener");
      el.dl.textContent = "打开原图（长按保存）";
    } else {
      el.dl.setAttribute("download", result.download_name || "wechat.gif");
      el.dl.removeAttribute("target");
      el.dl.textContent = "保存到手机";
    }

    el.meta.innerHTML = "";
    el.meta.appendChild(metaItem("尺寸", (result.width || "?") + "×" + (result.height || "?")));
    el.meta.appendChild(metaItem("体积", result.size_text || formatSize(result.size)));
    el.meta.appendChild(metaItem("时长", (result.duration || 0).toFixed(1) + " 秒"));
    el.meta.appendChild(metaItem("帧数", String(result.frames || 0)));
    el.meta.appendChild(metaItem("压缩轮次", String(result.rounds || 1)));
    if (result.source && result.source.width) {
      el.meta.appendChild(
        metaItem("原片", result.source.width + "×" + result.source.height + " · " + (result.source.duration || 0).toFixed(1) + "s")
      );
    }

    var limitMB = result.limit_bytes ? result.limit_bytes / 1048576 : 0;
    if (result.oversized) {
      el.warn.hidden = false;
      el.warn.className = "hint warn";
      el.warn.textContent =
        "已经压到参数下限，成品 " +
        (result.size_text || formatSize(result.size)) +
        " 仍超过设定的 " +
        limitMB.toFixed(1) +
        "MB。微信表情库大概率导入失败；把宽度调到 240、时长截到 4 秒再试一次。";
    } else if (result.trimmed) {
      el.warn.hidden = false;
      el.warn.className = "hint warn";
      el.warn.textContent =
        "原片 " +
        result.src_duration +
        " 秒，只转了前 " +
        result.duration +
        " 秒。GIF 体积随时长膨胀，长视频整段转出来会糊到没法看；需要更长可以在「高级设置」里把截取秒数调大。";
    } else {
      el.warn.hidden = true;
    }

    renderGuide(result);
    el.result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function rememberJob(jobId) {
    try {
      history.replaceState(null, "", "#job=" + jobId);
    } catch (err) {
      /* 某些内置浏览器不允许改 history，忽略 */
    }
  }

  function restoreJob() {
    var match = /#job=([0-9a-f]{12})/.exec(location.hash || "");
    if (!match) return;
    fetch("/api/jobs/" + match[1], { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("gone");
        return res.json();
      })
      .then(function (job) {
        if (job.state === "done" && job.result) {
          renderResult(job.result);
        } else if (job.state === "error") {
          showError(job.error || "这次转换失败了。");
        }
      })
      .catch(function () {
        /* 任务已过期很常见，安静忽略 */
      });
  }

  function renderGuide(result) {
    var steps = [];
    var big = result.size > 5 * 1048576;

    if (isIOS) {
      steps.push("在上面那张 GIF 上长按，选「存储到照片」。");
      steps.push("打开微信聊天 → 左下角 + → 相册 → 选中这张 GIF 发送。");
    } else if (isAndroid) {
      steps.push("点「保存到手机」，或在图片上长按选「下载图片」，文件会进相册/下载目录。");
      steps.push("打开微信聊天 → 左下角 + → 相册 → 选中这张 GIF 发送。");
    } else {
      steps.push("点「保存到手机」把 GIF 存下来，再用手机微信发送。");
      steps.push("微信聊天 → 左下角 + → 相册 → 选中这张 GIF。");
    }

    steps.push("想变成表情：在聊天里长按刚发出的 GIF → 添加，就能进表情库；也可以走「我 → 表情 → 添加的单个表情 → +」从相册选。");

    if (big) {
      steps.push("注意：微信表情库对单个动图有约 5MB 的上限，这张超过 5MB 只能当普通图片发，想进表情库请用「微信表情」预设重转一次。");
    }

    el.guide.innerHTML = "";
    steps.forEach(function (text) {
      var li = document.createElement("li");
      li.textContent = text;
      el.guide.appendChild(li);
    });
  }

  function formatSize(bytes) {
    if (!bytes && bytes !== 0) return "?";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(2) + "MB";
    return Math.max(1, Math.round(bytes / 1024)) + "KB";
  }

  /* ---------------- 事件绑定 ---------------- */

  el.go.addEventListener("click", start);

  el.link.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      start();
    }
  });

  el.retry.addEventListener("click", function () {
    el.error.hidden = true;
    start();
  });

  function checkHealth() {
    fetch("/api/health", { cache: "no-store" })
      .then(function (res) {
        return res.json();
      })
      .then(function (h) {
        var msgs = [];
        if (!h.ffmpeg) {
          msgs.push("服务器上找不到 ffmpeg，转码会直接失败。装好 ffmpeg，或用 FFMPEG_PATH 指定可执行文件路径。");
        }
        if (h.network === false) {
          msgs.push(
            h.proxy
              ? "服务器连不上 video.twimg.com。已经检测到代理环境变量，检查代理是否放行这个域名（ffmpeg 也要走同一个代理）。"
              : "服务器连不上 video.twimg.com。需要设置 HTTPS_PROXY 走代理，或者把服务部署到能访问 X 的网络里。"
          );
        }
        if (msgs.length) {
          el.banner.hidden = false;
          el.banner.className = "banner bad";
          el.banner.textContent = msgs.join(" ");
        }
      })
      .catch(function () {
        /* 健康检查失败不影响主流程 */
      });
  }

  renderProfiles();
  applyProfileDefaults();
  checkHealth();
  restoreJob();

  if (isIOS) {
    // iOS 上没法用 download 直接进相册，改走长按保存，先在提示里讲清楚
    el.hint.textContent =
      "支持 x.com、twitter.com 的推文链接，也支持 video.twimg.com 的 mp4 直链。生成后长按图片即可存进相册。";
  }
})();
