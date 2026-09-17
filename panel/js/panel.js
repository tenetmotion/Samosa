(function () {
  "use strict";

  var cs = new CSInterface();
  var nodeRequire = typeof require === "function" ? require : (window.cep_node && window.cep_node.require);
  if (!nodeRequire) throw new Error("CEP Node integration is unavailable");
  var fs = nodeRequire("fs");
  var path = nodeRequire("path");
  var os = nodeRequire("os");
  var crypto = nodeRequire("crypto");
  var child = nodeRequire("child_process");
  var http = nodeRequire("http");
  function normalizeCepPath(value) {
    var normalized = decodeURI(String(value || ""));
    normalized = normalized.replace(/^file:[\\/]+/i, "");
    normalized = normalized.replace(/^[/\\]([A-Za-z]:)/, "$1");
    return normalized.replace(/[\\/]+/g, path.sep);
  }
  var extensionPath = normalizeCepPath(cs.getSystemPath(SystemPath.EXTENSION));
  var configPath = path.join(extensionPath, "config.json");
  if (!fs.existsSync(configPath)) throw new Error("Samosa is not configured. Run the Samosa installer first.");
  var configText = fs.readFileSync(configPath, "utf8").replace(/^\uFEFF/, "");
  var config = JSON.parse(configText);
  var baseUrl = "http://127.0.0.1:" + config.port;
  var serviceScript = path.join(extensionPath, "backend", "service.py");
  var clientToken = crypto.randomBytes(16).toString("hex");
  var serviceRegistered = false;

  function normalizeRepo(value) {
    var normalized = normalizeCepPath(value);
    if (!normalized) return "";
    try { normalized = path.resolve(normalized); } catch (e) {}
    normalized = path.normalize(normalized).replace(/[\\/]+/g, path.sep);
    return process.platform === "win32" ? normalized.toLowerCase() : normalized;
  }

  function localBackendSha256() {
    var digest = crypto.createHash("sha256");
    ["service.py", "sam3_bridge.py", "sam3_worker.py"].forEach(function (name) {
      var file = path.join(extensionPath, "backend", name);
      if (!fs.existsSync(file)) return;
      digest.update(name, "utf8");
      digest.update(Buffer.from([0]));
      digest.update(fs.readFileSync(file));
    });
    return digest.digest("hex").toLowerCase();
  }

  var expectedBackendSha256 = localBackendSha256();
  var expectedRepo = normalizeRepo(config.repo);
  var runtimeLogDir = process.platform === "darwin"
    ? path.join(os.homedir(), "Library", "Logs", "Samosa")
    : path.join(process.env.APPDATA || os.homedir(), "Samosa");
  try { if (!fs.existsSync(runtimeLogDir)) fs.mkdirSync(runtimeLogDir, { recursive: true }); } catch (e) {}
  var runtimeLog = path.join(runtimeLogDir, "panel-runtime.log");
  var state = null;
  var frame = 0;
  var positive = true;
  var currentMode = "object";
  var currentView = "edit";
  var activeJob = null;
  var latestOutput = null;
  var selectedLayer = null;
  var renderToken = 0;
  var viewerScales = [100, 75, 50];
  var viewerScaleIndex = 0;
  var sam3Status = null;
  var sam3ModelEntry = null;
  var sam3PromptMode = "point";
  var sam3StatusTimer = null;
  var sam3Drag = null;
  var suppressNextViewerClick = false;

  function el(id) { return document.getElementById(id); }
  function number(id) { return Number(el(id).value); }
  function finiteNumber(value, fallback) {
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : fallback;
  }
  function sam3ModelSelected(model) {
    return String(model || "") === "SAM3.1";
  }
  function sam3ControlsVisible(model, status, entry) {
    return sam3ModelSelected(model);
  }
  function sam3Availability(status, entry) {
    var source = status || entry || {};
    if (source.availability !== undefined) return source.availability === true || source.availability === "available" || source.availability === "ready" || source.availability === "installed";
    if (source.available !== undefined) return source.available === true;
    if (source.installed !== undefined) return source.installed === true;
    if (source.ready !== undefined) return source.ready === true;
    if (source.status !== undefined) return source.status === "ready" || source.status === "available" || source.status === "installed";
    return false;
  }
  function sam3CapabilitySupported(capabilities, mode) {
    if (!capabilities) return true;
    var source = capabilities.prompts || capabilities.modes || capabilities;
    if (Array.isArray(source)) return source.indexOf(mode) >= 0 || source.indexOf("all") >= 0;
    if (typeof source === "object") {
      if (source[mode] === false) return false;
      if (source[mode] === true) return true;
      if (source.all === false) return false;
    }
    return true;
  }
  function clamp01(value) {
    return Math.max(0, Math.min(1, finiteNumber(value, 0)));
  }
  function normalizeCanvasPoint(clientX, clientY, rect) {
    var width = Math.max(1, finiteNumber(rect && rect.width, 1));
    var height = Math.max(1, finiteNumber(rect && rect.height, 1));
    return {
      x: clamp01((finiteNumber(clientX, 0) - finiteNumber(rect && rect.left, 0)) / width),
      y: clamp01((finiteNumber(clientY, 0) - finiteNumber(rect && rect.top, 0)) / height)
    };
  }
  function normalizeCanvasRect(start, current, rect) {
    var first = normalizeCanvasPoint(start.x, start.y, rect);
    var last = normalizeCanvasPoint(current.x, current.y, rect);
    return {
      x1: Math.min(first.x, last.x),
      y1: Math.min(first.y, last.y),
      x2: Math.max(first.x, last.x),
      y2: Math.max(first.y, last.y)
    };
  }
  function buildSam3PromptPayload(mode, objectId, frameNumber, prompt, label) {
    var payload = { type: mode, object_id: Number(objectId), frame: Number(frameNumber) };
    if (mode === "point") {
      payload.x = clamp01(prompt.x);
      payload.y = clamp01(prompt.y);
      payload.label = label;
    } else if (mode === "box" || mode === "example") {
      payload.x1 = clamp01(prompt.x1);
      payload.y1 = clamp01(prompt.y1);
      payload.x2 = clamp01(prompt.x2);
      payload.y2 = clamp01(prompt.y2);
      payload.label = mode === "box" ? "positive" : label;
    } else if (mode === "text") {
      payload.text = String(prompt.text || "").trim();
    }
    return payload;
  }
  function redactSam3Message(message, secret) {
    var safe = String(message || "");
    if (secret) safe = safe.split(String(secret)).join("[redacted]");
    return safe.replace(/hf_[A-Za-z0-9_-]+/g, "[redacted]");
  }
  function friendlySam3Message(message, secret) {
    var safe = redactSam3Message(message, secret);
    var lowered = safe.toLowerCase();
    if (lowered.indexOf("403 client error") >= 0 && (
      lowered.indexOf("gated repo") >= 0 ||
      lowered.indexOf("not in the authorized list") >= 0 ||
      lowered.indexOf("access to model facebook/sam3.1 is restricted") >= 0
    )) {
      return "Hugging Face has not granted this token's account SAM 3.1 access. Open the access page with the same account, submit the Meta form, wait for access to be granted, then retry with a read token from that account.";
    }
    if (lowered.indexOf("401 client error") >= 0 || lowered.indexOf("invalid user token") >= 0) {
      return "Hugging Face rejected this token. Use a valid read token from the same account that has SAM 3.1 access.";
    }
    return safe;
  }

  function frameAtSourceTime(seconds, fps) {
    var product = finiteNumber(seconds, 0) * finiteNumber(fps, 0);
    var nearest = Math.round(product);
    if (Math.abs(product - nearest) <= 1e-7) product = nearest;
    return Math.floor(product);
  }
  function log(message) {
    try { fs.appendFileSync(runtimeLog, new Date().toISOString() + " " + message + "\r\n"); } catch (e) {}
  }
  function setStatus(message, type) {
    el("status").textContent = message;
    el("serviceLight").className = "status-light " + (type || "online");
  }
  function applyViewerScale() {
    var scale = viewerScales[viewerScaleIndex];
    el("viewer").parentNode.style.width = scale + "%";
    el("viewerScale").textContent = scale + "%";
    el("viewerScale").title = scale === 50 ? "Reset viewer size" : "Make viewer smaller";
    try { localStorage.setItem("samosa.viewerScale", String(scale)); } catch (e) {}
  }
  function restoreViewerScale() {
    var saved = 100;
    try { saved = Number(localStorage.getItem("samosa.viewerScale")) || 100; } catch (e) {}
    var index = viewerScales.indexOf(saved);
    viewerScaleIndex = index >= 0 ? index : 0;
    applyViewerScale();
  }
  function setExportDestination(value) {
    var destination = value ? normalizeCepPath(value) : "";
    el("outputDestination").value = destination;
    el("outputDestination").title = destination || "Sammie-Roto-2 temp/ae_exports";
    try { localStorage.setItem("samosa.exportDestination", destination); } catch (e) {}
  }
  function restoreExportDestination() {
    var saved = "";
    try { saved = localStorage.getItem("samosa.exportDestination") || ""; } catch (e) {}
    setExportDestination(saved);
  }
  function sourceFileName(value) {
    var fileName = path.basename(String(value || ""));
    var extension = path.extname(fileName);
    return extension ? fileName.slice(0, -extension.length) : fileName;
  }
  function setDefaultOutputName(value) {
    el("outputName").value = sourceFileName(value);
  }
  function chooseExportDestination() {
    try {
      if (!window.cep || !window.cep.fs || !window.cep.fs.showOpenDialogEx) {
        throw new Error("CEP folder selection is unavailable");
      }
      var initial = el("outputDestination").value || config.repo;
      var result = window.cep.fs.showOpenDialogEx(false, true, "Choose Samosa export folder", initial);
      if (result.err !== 0) throw new Error("Folder selection failed (" + result.err + ")");
      if (result.data && result.data.length) {
        setExportDestination(result.data[0]);
        setStatus("Export folder selected");
      }
    } catch (e) { setStatus(e.message, "error"); }
  }
  function confirmRestrictedModel(key, label, terms) {
    if (config.accepted_restricted_models) return true;
    var storageKey = "samosa.modelLicense." + key;
    try { if (localStorage.getItem(storageKey) === "accepted") return true; } catch (e) {}
    var accepted = window.confirm(
      label + " is provided under " + terms + " and is not licensed for unrestricted commercial use.\n\n" +
      "Review THIRD_PARTY_NOTICES.md before continuing. Download and use this model?"
    );
    if (accepted) {
      try { localStorage.setItem(storageKey, "accepted"); } catch (e2) {}
    }
    return accepted;
  }
  function escapeJs(value) {
    return JSON.stringify(String(value));
  }
  function evalHost(code) {
    return new Promise(function (resolve, reject) {
      var complete = false;
      var timer = setTimeout(function () {
        if (!complete) reject(new Error("After Effects host bridge timed out"));
      }, 10000);
      cs.evalScript(code, function (result) {
        complete = true;
        clearTimeout(timer);
        if (String(result).indexOf("EvalScript error") === 0) reject(new Error(result));
        else resolve(result);
      });
    });
  }
  function api(method, endpoint, data) {
    return new Promise(function (resolve, reject) {
      var body = data ? JSON.stringify(data) : "";
      var request = http.request({
        hostname: "127.0.0.1", port: config.port, path: endpoint, method: method,
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) }
      }, function (response) {
        var chunks = "";
        response.setEncoding("utf8");
        response.on("data", function (chunk) { chunks += chunk; });
        response.on("end", function () {
          var payload;
          try { payload = JSON.parse(chunks || "{}"); }
          catch (e) { return reject(new Error("Invalid service response")); }
          if (response.statusCode >= 200 && response.statusCode < 300) resolve(payload);
          else reject(new Error(payload.error || ("Service error " + response.statusCode)));
        });
      });
      request.on("error", function (error) { reject(new Error("Samosa service unavailable: " + error.message)); });
      if (endpoint === "/health") {
        request.setTimeout(3000, function () { request.destroy(new Error("health check timed out")); });
      }
      if (body) request.write(body);
      request.end();
    });
  }
  function sleep(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  function serviceMatches(health) {
    if (!health || health.service !== "samosa-ae") return false;
    if (normalizeRepo(health.repo) !== expectedRepo) return false;
    if (!health.backend_sha256) return false;
    return String(health.backend_sha256).toLowerCase() === expectedBackendSha256;
  }

  async function shutdownService(reason) {
    log("Shutting down " + reason + " service");
    try { await api("POST", "/shutdown", {}); } catch (e) { log("Shutdown request failed: " + e.message); }
    for (var i = 0; i < 20; i++) {
      await sleep(150);
      try { await api("GET", "/health"); } catch (e2) { return; }
    }
    throw new Error("The stale Samosa service did not shut down");
  }

  async function registerServiceClient() {
    await api("POST", "/api/client/register", {
      client_token: clientToken,
      parent_pid: process.pid
    });
    serviceRegistered = true;
  }

  function releaseServiceClient() {
    if (!serviceRegistered) return;
    serviceRegistered = false;
    var payload = JSON.stringify({ client_token: clientToken });
    try {
      if (navigator && typeof navigator.sendBeacon === "function" &&
          navigator.sendBeacon(baseUrl + "/api/client/release", payload)) {
        return;
      }
    } catch (beaconError) {
      log("Service client beacon release failed: " + beaconError.message);
    }
    try {
      var request = new XMLHttpRequest();
      request.open("POST", baseUrl + "/api/client/release", false);
      request.setRequestHeader("Content-Type", "application/json");
      request.send(payload);
    } catch (e) { log("Service client release on panel close failed: " + e.message); }
  }

  async function ensureService() {
    log("Checking service at " + baseUrl);
    var existingHealth = null;
    try {
      existingHealth = await api("GET", "/health");
    } catch (e) {
      log("Initial health check failed: " + e.message);
    }
    if (existingHealth) {
      if (serviceMatches(existingHealth)) {
        log("Service already running on " + existingHealth.device);
        try {
          await registerServiceClient();
          setStatus("Processing service connected");
          return;
        } catch (registerError) {
          log("Existing service disappeared during client registration: " + registerError.message);
          existingHealth = null;
        }
      }
      if (existingHealth && existingHealth.service !== "samosa-ae") {
        throw new Error("Port " + config.port + " is already used by another service.");
      }
      if (existingHealth) await shutdownService("stale");
    }
    setStatus("Starting processing service...", "");
    var logDir = runtimeLogDir;
    if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });
    var out = fs.openSync(path.join(logDir, "service.log"), "a");
    var spawnEnv = Object.assign({}, process.env, config.environment || {});
    var spawnArgs = [
      serviceScript, "--repo", config.repo, "--port", String(config.port),
      "--parent-pid", String(process.pid), "--client-token", clientToken
    ];
    var proc;
    try {
      proc = child.spawn(config.python, spawnArgs, {
        cwd: config.repo, detached: true, windowsHide: true, stdio: ["ignore", out, out], env: spawnEnv
      });
    } finally {
      try { fs.closeSync(out); } catch (closeError) {}
    }
    proc.on("error", function (error) { log("Service spawn error: " + error.message); });
    proc.unref();
    for (var i = 0; i < 60; i++) {
      await sleep(500);
      try {
        var spawnedHealth = await api("GET", "/health");
        if (!serviceMatches(spawnedHealth)) throw new Error("Spawned service identity does not match this panel");
        log("Spawned service is healthy");
        serviceRegistered = true;
        setStatus("Processing service connected");
        return;
      } catch (e2) {}
    }
    throw new Error("Service did not start. Check " + path.join(logDir, "service.log"));
  }

  async function loadHostScript() {
    var host = path.join(extensionPath, "host", "host.jsx").replace(/\\/g, "/");
    await evalHost("$.evalFile(" + escapeJs(host) + ")");
  }

  function settingsPayload() {
    return {
      sam_model: el("samModel").value,
      holes: number("holes"), dots: number("dots"), border_fix: number("borderFix"), grow: number("grow"),
      show_masks: el("showMasks").checked, show_outlines: el("showOutlines").checked, antialias: el("antialias").checked,
      selected_object_id: number("objectId"),
      matany_model: el("mattingModel").value, matany_res: number("mattingResolution"),
      matany_chunk: number("mattingChunk"), matany_overlap: number("mattingOverlap"),
      matany_grow: number("mattingGrow"), matany_gamma: number("mattingGamma"), matany_combined: el("combineObjects").checked,
      inpaint_method: el("opencvMethod").value, inpaint_radius: number("opencvRadius"), inpaint_grow: number("inpaintGrow"),
      minimax_resolution: number("minimaxResolution"), minimax_steps: number("minimaxSteps"), minimax_vae_tiling: el("vaeTiling").checked
    };
  }
  async function saveSettings() {
    state = await api("POST", "/api/settings", settingsPayload());
  }

  function updateObjects() {
    var ids = state ? state.object_ids.slice() : [];
    var current = number("objectId") || 0;
    if (ids.indexOf(current) < 0) ids.push(current);
    if (!ids.length) ids.push(0);
    ids.sort(function (a, b) { return a - b; });
    ["objectId", "exportObject"].forEach(function (id) {
      var select = el(id);
      var previous = select.value;
      select.innerHTML = id === "exportObject" ? '<option value="-1">All objects</option>' : "";
      ids.forEach(function (objectId) {
        var option = document.createElement("option");
        option.value = String(objectId);
        option.textContent = "Object " + (objectId + 1);
        select.appendChild(option);
      });
      if ([].some.call(select.options, function (o) { return o.value === previous; })) select.value = previous;
    });
  }
  function updatePointList() {
    var list = el("pointList");
    list.innerHTML = "";
    if (!state || !state.points.length) return;
    state.points.filter(function (p) { return !p.synthetic; }).slice().reverse().slice(0, 20).forEach(function (p) {
      var row = document.createElement("div");
      row.className = "point-row";
      row.innerHTML = "<b>F" + (p.frame + 1) + "</b><span>Object " + (p.object_id + 1) + "</span><span>" + (p.positive ? "Include" : "Exclude") + "</span>";
      list.appendChild(row);
    });
  }
  function renderState() {
    if (!state) return;
    if (state.settings && state.settings.sam_model &&
        [].some.call(el("samModel").options, function (option) { return option.value === state.settings.sam_model; })) {
      el("samModel").value = state.settings.sam_model;
    }
    el("frameSlider").max = Math.max(0, state.total_frames - 1);
    frame = Math.max(0, Math.min(frame, state.total_frames - 1));
    el("frameSlider").value = frame;
    el("frameReadout").textContent = (frame + 1) + " / " + state.total_frames;
    el("viewerEmpty").style.display = state.loaded ? "none" : "grid";
    var tracked = state.tracking || { frames: 0, complete: false };
    el("trackingStatus").textContent = tracked.complete ? "Tracked: complete" : (tracked.frames ? "Masks: " + tracked.frames + " / " + state.total_frames + " frames" : "Not tracked");
    updateObjects();
    updatePointList();
    updateSam3Controls();
    refreshFrame();
  }
  function refreshFrame() {
    if (!state || !state.loaded) return;
    var token = ++renderToken;
    var image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = function () {
      if (token !== renderToken) return;
      var canvas = el("viewer");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      canvas.parentNode.style.aspectRatio = image.naturalWidth + "/" + image.naturalHeight;
      canvas.getContext("2d").drawImage(image, 0, 0);
      el("viewerBadge").textContent = currentView.replace(/-/g, " ").toUpperCase();
    };
    image.onerror = function () { setStatus("Preview unavailable", "error"); };
    image.src = baseUrl + "/api/frame?frame=" + frame + "&view=" + encodeURIComponent(currentView) + "&object_id=" + encodeURIComponent(el("objectId").value) + "&_=" + Date.now();
  }

  async function loadSelection() {
    try {
      setStatus("Reading selected layer...");
      var raw = await evalHost("$._samosaAE.getSelectedLayerInfo()");
      var info = JSON.parse(raw);
      if (!info.ok) throw new Error(info.error);
      var sourceStart = finiteNumber(info.sourceStart, 0);
      var sourceEnd = finiteNumber(info.sourceEnd, 0);
      var sourceFps = finiteNumber(info.sourceFrameRate, 0);
      var sourceNativeFps = finiteNumber(info.sourceNativeFrameRate, 0);
      if (!(sourceEnd > sourceStart)) throw new Error("The selected layer has no positive source-time range.");
      selectedLayer = info;
      el("clipName").textContent = info.layerName;
      setDefaultOutputName(info.sourceName || info.path);
      setStatus("Decoding " + info.sourceName + "...");
      var loadPayload = { path: info.path, source_start: sourceStart, source_end: sourceEnd };
      if (sourceFps > 0) loadPayload.source_fps = sourceFps;
      if (sourceNativeFps > 0) loadPayload.source_native_fps = sourceNativeFps;
      state = await api("POST", "/api/load", loadPayload);
      state.source_start_time = finiteNumber(state.source_start_time, sourceStart);
      state.source_end_time = finiteNumber(state.source_end_time, sourceEnd);
      state.source_start_frame = finiteNumber(
        state.source_start_frame, frameAtSourceTime(sourceStart, state.fps)
      );
      frame = frameAtSourceTime(info.sourceTime, state.fps) - state.source_start_frame;
      frame = Math.max(0, Math.min(state.total_frames - 1, frame));
      renderState();
      setStatus("Loaded " + state.total_frames + " frames");
    } catch (e) { setStatus(e.message, "error"); }
  }

  async function addPoint(event) {
    if (!state || !state.loaded || activeJob) return;
    if (suppressNextViewerClick) {
      suppressNextViewerClick = false;
      return;
    }
    if (sam3ModelSelected(el("samModel").value) && sam3PromptMode !== "point") {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    var canvas = el("viewer");
    var rect = canvas.getBoundingClientRect();
    var isPositive = event.type === "contextmenu" ? false : positive;
    if (sam3ModelSelected(el("samModel").value)) {
      var normalized = normalizeCanvasPoint(event.clientX, event.clientY, rect);
      await sendSam3Prompt(
        buildSam3PromptPayload("point", number("objectId"), frame, normalized, isPositive ? "positive" : "negative"),
        isPositive ? "Include point submitted" : "Exclude point submitted"
      );
      return;
    }
    var x = Math.round((event.clientX - rect.left) * canvas.width / rect.width);
    var y = Math.round((event.clientY - rect.top) * canvas.height / rect.height);
    try {
      setStatus(state.segmentation_ready ? "Updating mask..." : "Loading segmentation model...");
      await saveSettings();
      state = await api("POST", "/api/points", { frame: frame, object_id: number("objectId"), positive: isPositive, x: x, y: y });
      renderState();
      setStatus(isPositive ? "Include point added" : "Exclude point added");
    } catch (e) { setStatus(e.message, "error"); }
  }

  async function monitorJob(job, label, onComplete, refreshState) {
    activeJob = job;
    el("jobBar").classList.remove("hidden");
    el("jobLabel").textContent = label;
    try {
      while (activeJob && ["queued", "running"].indexOf(activeJob.status) >= 0) {
        el("jobLabel").textContent = activeJob.message || label;
        el("jobProgress").value = activeJob.progress || 0;
        el("jobPercent").textContent = (activeJob.progress || 0) + "%";
        await sleep(500);
        activeJob = await api("GET", "/api/job?id=" + activeJob.id);
      }
      if (!activeJob) return;
      if (activeJob.status === "failed") throw new Error(activeJob.message || "Processing failed");
      if (activeJob.status === "cancelled") throw new Error("Processing cancelled");
      var result = activeJob.result;
      activeJob = null;
      el("jobBar").classList.add("hidden");
      if (refreshState !== false) {
        state = await api("GET", "/api/state");
        refreshFrame();
      }
      setStatus(label + " complete");
      if (onComplete) await onComplete(result);
    } catch (error) {
      activeJob = null;
      el("jobBar").classList.add("hidden");
      throw error;
    }
  }

  async function startJob(endpoint, body, label, onComplete) {
    try {
      await saveSettings();
      var job = await api("POST", endpoint, body || {});
      await monitorJob(job, label, onComplete, true);
    } catch (e) {
      activeJob = null;
      el("jobBar").classList.add("hidden");
      setStatus(e.message, "error");
    }
  }

  async function importOutput(result) {
    latestOutput = result;
    var referenceIndex = selectedLayer ? selectedLayer.layerIndex : -1;
    var resultFps = finiteNumber(result && result.fps, finiteNumber(state && state.fps, 0));
    var resultTrimmed = result && result.trimmed !== undefined
      ? (result.trimmed === true || result.trimmed === 1 || result.trimmed === "true")
      : !!(state && state.trimmed);
    var raw = await evalHost("$._samosaAE.importResult(" + escapeJs(result.path) + "," + (result.sequence ? "true" : "false") + "," + referenceIndex + "," + resultFps + "," + (resultTrimmed ? "true" : "false") + ")");
    var response = JSON.parse(raw);
    if (!response.ok) throw new Error(response.error);
    setStatus("Added " + response.layerName + " to comp");
  }

  function sam3ModelEntryFrom(payload) {
    var entries = Array.isArray(payload) ? payload : payload && (payload.models || payload.entries || payload.data);
    if (!Array.isArray(entries) && entries && typeof entries === "object") {
      entries = Object.keys(entries).map(function (key) {
        var value = entries[key];
        return typeof value === "object" ? Object.assign({ id: key }, value) : { id: key, availability: value };
      });
    }
    if (!Array.isArray(entries)) return null;
    return entries.filter(function (entry) { return entry && String(entry.id || entry.name || "") === "SAM3.1"; })[0] || null;
  }
  function sam3StatusReason(status, entry) {
    var source = status || entry || {};
    var job = source.job || source.install_job;
    var failedJobMessage = job && String(job.status || "").toLowerCase() === "failed" && job.message;
    return friendlySam3Message(failedJobMessage || source.reason || source.message || "Install the gated model to enable prompts.");
  }
  function sam3JobActive(status) {
    var job = status && (status.job || status.install_job);
    return !!(job && ["queued", "running", "installing", "downloading"].indexOf(String(job.status || "").toLowerCase()) >= 0);
  }
  function updateSam3Controls() {
    var controls = el("sam3Controls");
    if (!controls) return;
    var selected = sam3ModelSelected(el("samModel").value);
    controls.classList.toggle("hidden", !selected);
    el("promptBoxOverlay").classList.add("hidden");
    if (!selected) return;
    var available = sam3Availability(sam3Status, sam3ModelEntry);
    var supported = !sam3Status || sam3Status.supported !== false;
    var capabilities = (sam3Status && sam3Status.capabilities) || (sam3ModelEntry && sam3ModelEntry.capabilities);
    var labels = {
      point: "Click to add an include or exclude point.",
      box: "Drag a box to add one positive geometry prompt.",
      text: "Describe an object, then submit the text prompt.",
      example: positive ? "Drag a box to add an include example." : "Drag a box to add an exclude example."
    };
    var loaded = !!(state && state.loaded);
    var promptReady = available && loaded;
    var currentSupported = promptReady && sam3CapabilitySupported(capabilities, sam3PromptMode);
    var hint = !available
      ? sam3StatusReason(sam3Status, sam3ModelEntry)
      : (!loaded ? "Load footage to enable SAM 3.1 prompts." : (currentSupported ? labels[sam3PromptMode] : "This prompt mode is not supported by the installed SAM 3.1 backend."));
    el("sam3PromptHint").textContent = hint;
    [].forEach.call(document.querySelectorAll("#sam3PromptModes button"), function (button) {
      var mode = button.dataset.promptMode;
      var supported = promptReady && sam3CapabilitySupported(capabilities, mode);
      button.disabled = !supported;
      button.title = supported ? "" : (available ? "This prompt mode is not supported by the installed SAM 3.1 backend." : "Install SAM 3.1 to enable prompts.");
      button.classList.toggle("selected", mode === sam3PromptMode);
    });
    el("sam3TextPrompt").classList.toggle("hidden", sam3PromptMode !== "text");
    el("sam3TextInput").disabled = !promptReady;
    el("sam3TextSubmit").disabled = !promptReady || !el("sam3TextInput").value.trim();
    el("sam3Setup").classList.toggle("hidden", available);
    el("sam3SetupTitle").textContent = supported ? "SAM 3.1 is not installed" : "SAM 3.1 is unavailable";
    el("sam3SetupReason").textContent = sam3JobActive(sam3Status) ? "SAM 3.1 setup is in progress." : sam3StatusReason(sam3Status, sam3ModelEntry);
    el("sam3Install").disabled = !supported || sam3JobActive(sam3Status) || !el("sam3LicenseAccepted").checked || !el("sam3Token").value.trim();
    el("sam3Token").disabled = sam3JobActive(sam3Status);
    el("sam3LicenseAccepted").disabled = !supported || sam3JobActive(sam3Status);
    el("sam3AccessPage").disabled = sam3JobActive(sam3Status);
  }
  async function refreshSam3Status() {
    if (!sam3ModelSelected(el("samModel").value)) {
      if (sam3StatusTimer) clearTimeout(sam3StatusTimer);
      sam3StatusTimer = null;
      updateSam3Controls();
      return;
    }
    try {
      var models = await api("GET", "/api/models");
      sam3ModelEntry = sam3ModelEntryFrom(models);
    } catch (e) {
      sam3ModelEntry = null;
    }
    try {
      sam3Status = await api("GET", "/api/sam3/status");
    } catch (e2) {
      sam3Status = { availability: false, reason: "SAM 3.1 status unavailable." };
    }
    updateSam3Controls();
    if (sam3JobActive(sam3Status)) {
      if (sam3StatusTimer) clearTimeout(sam3StatusTimer);
      sam3StatusTimer = setTimeout(function () { refreshSam3Status(); }, 1000);
    }
  }
  function sam3Ready() {
    return sam3ModelSelected(el("samModel").value) && sam3Availability(sam3Status, sam3ModelEntry);
  }
  async function sendSam3Prompt(payload, successMessage) {
    if (!state || !state.loaded) {
      setStatus("Load footage before adding a SAM 3.1 prompt", "error");
      return;
    }
    if (!sam3Ready()) {
      updateSam3Controls();
      setStatus("SAM 3.1 is not ready", "error");
      return;
    }
    try {
      setStatus("Submitting SAM 3.1 prompt...");
      await saveSettings();
      var response = await api("POST", "/api/sam3/prompt", payload);
      if (response && response.state) state = response.state;
      else state = await api("GET", "/api/state");
      renderState();
      setStatus(successMessage);
    } catch (e) {
      setStatus(redactSam3Message(e.message), "error");
    }
  }
  function submitSam3Text() {
    var textPrompt = el("sam3TextInput").value.trim();
    if (!textPrompt || sam3PromptMode !== "text") {
      updateSam3Controls();
      return;
    }
    sendSam3Prompt(
      buildSam3PromptPayload("text", number("objectId"), frame, { text: textPrompt }, positive ? "positive" : "negative"),
      "Text prompt submitted"
    );
  }
  function updatePromptBoxOverlay(start, current, rect) {
    var box = normalizeCanvasRect(start, current, rect);
    var shellRect = el("viewer").parentNode.getBoundingClientRect();
    var overlay = el("promptBoxOverlay");
    overlay.style.left = (rect.left - shellRect.left + box.x1 * rect.width) + "px";
    overlay.style.top = (rect.top - shellRect.top + box.y1 * rect.height) + "px";
    overlay.style.width = ((box.x2 - box.x1) * rect.width) + "px";
    overlay.style.height = ((box.y2 - box.y1) * rect.height) + "px";
    overlay.classList.remove("hidden");
  }
  function beginSam3Drag(event) {
    if (!state || !state.loaded || activeJob || !sam3Ready() || (sam3PromptMode !== "box" && sam3PromptMode !== "example") || event.button !== 0) return;
    event.preventDefault();
    var canvas = el("viewer");
    var rect = canvas.getBoundingClientRect();
    sam3Drag = { start: { x: event.clientX, y: event.clientY }, rect: rect, pointerId: event.pointerId };
    updatePromptBoxOverlay(sam3Drag.start, sam3Drag.start, rect);
    try { canvas.setPointerCapture(event.pointerId); } catch (e) {}
  }
  function moveSam3Drag(event) {
    if (!sam3Drag || event.pointerId !== sam3Drag.pointerId) return;
    event.preventDefault();
    updatePromptBoxOverlay(sam3Drag.start, { x: event.clientX, y: event.clientY }, sam3Drag.rect);
  }
  function finishSam3Drag(event) {
    if (!sam3Drag || event.pointerId !== sam3Drag.pointerId) return;
    event.preventDefault();
    var drag = sam3Drag;
    sam3Drag = null;
    try { el("viewer").releasePointerCapture(event.pointerId); } catch (e) {}
    var prompt = normalizeCanvasRect(drag.start, { x: event.clientX, y: event.clientY }, drag.rect);
    el("promptBoxOverlay").classList.add("hidden");
    suppressNextViewerClick = true;
    setTimeout(function () { suppressNextViewerClick = false; }, 0);
    if ((prompt.x2 - prompt.x1) < 0.002 || (prompt.y2 - prompt.y1) < 0.002) {
      setStatus("Drag a larger prompt box", "error");
      return;
    }
    var label = sam3PromptMode === "box" ? "positive" : (positive ? "positive" : "negative");
    sendSam3Prompt(
      buildSam3PromptPayload(sam3PromptMode, number("objectId"), frame, prompt, label),
      sam3PromptMode === "box" ? "Box prompt submitted" : "Example submitted"
    );
  }

  function cancelSam3Drag(event) {
    if (!sam3Drag || event.pointerId !== sam3Drag.pointerId) return;
    sam3Drag = null;
    el("promptBoxOverlay").classList.add("hidden");
    try { el("viewer").releasePointerCapture(event.pointerId); } catch (e) {}
  }

  function setSam3PromptMode(mode) {
    if (["point", "box", "text", "example"].indexOf(mode) < 0) return;
    sam3PromptMode = mode;
    el("promptBoxOverlay").classList.add("hidden");
    updateSam3Controls();
  }
  function openSam3AccessPage() {
    var url = "https://huggingface.co/facebook/sam3.1";
    try {
      if (!window.cep || !window.cep.util || typeof window.cep.util.openURLInDefaultBrowser !== "function") {
        throw new Error("CEP browser opening is unavailable");
      }
      window.cep.util.openURLInDefaultBrowser(url);
    } catch (e) {
      setStatus(redactSam3Message(e.message), "error");
    }
  }
  async function installSam3() {
    var tokenInput = el("sam3Token");
    var token = String(tokenInput.value || "").trim();
    if (!token) {
      setStatus("Enter a Hugging Face token to install SAM 3.1", "error");
      return;
    }
    if (!el("sam3LicenseAccepted").checked) {
      setStatus("Accept the Meta SAM License before installing SAM 3.1", "error");
      return;
    }
    tokenInput.value = "";
    try {
      setStatus("Starting SAM 3.1 setup...");
      var setupJob = await api("POST", "/api/sam3/setup", { hf_token: token, accept_license: true });
      token = "";
      sam3Status = { availability: false, job: setupJob, reason: "SAM 3.1 setup is in progress." };
      updateSam3Controls();
      setStatus("SAM 3.1 setup started");
      await monitorJob(setupJob, "SAM 3.1 setup", refreshSam3Status, false);
    } catch (e) {
      setStatus(friendlySam3Message(e.message, token), "error");
    } finally {
      token = "";
      tokenInput.value = "";
    }
  }

  function switchMode(mode) {
    currentMode = mode;
    [].forEach.call(document.querySelectorAll(".mode"), function (button) { button.classList.toggle("active", button.dataset.mode === mode); });
    [].forEach.call(document.querySelectorAll(".mode-panel"), function (panel) { panel.classList.toggle("active", panel.id === mode + "Panel"); });
    currentView = mode === "object" ? "edit" : mode === "matting" ? "matting-alpha" : mode === "remove" ? "removal" : outputView();
    refreshFrame();
  }
  function outputView() {
    var value = el("outputType").value;
    if (value === "ObjectRemoval") return "removal";
    return value.toLowerCase();
  }
  function updateRemovalFields() {
    var cv = el("removalMethod").value === "OpenCV";
    [].forEach.call(document.querySelectorAll(".opencv-field"), function (x) { x.style.display = cv ? "flex" : "none"; });
    [].forEach.call(document.querySelectorAll(".minimax-field"), function (x) { x.style.display = cv ? "none" : "flex"; });
  }

  function bind() {
    el("loadSelection").onclick = loadSelection;
    el("viewerScale").onclick = function () {
      viewerScaleIndex = (viewerScaleIndex + 1) % viewerScales.length;
      applyViewerScale();
    };
    el("viewer").addEventListener("click", addPoint);
    el("viewer").addEventListener("contextmenu", addPoint);
    el("viewer").addEventListener("pointerdown", beginSam3Drag);
    el("viewer").addEventListener("pointermove", moveSam3Drag);
    el("viewer").addEventListener("pointerup", finishSam3Drag);
    el("viewer").addEventListener("pointercancel", cancelSam3Drag);
    el("positiveTool").onclick = function () { positive = true; this.classList.add("selected"); el("negativeTool").classList.remove("selected"); updateSam3Controls(); };
    el("negativeTool").onclick = function () { positive = false; this.classList.add("selected"); el("positiveTool").classList.remove("selected"); updateSam3Controls(); };
    el("frameSlider").oninput = function () { frame = Number(this.value); renderState(); };
    el("prevFrame").onclick = function () { frame = Math.max(0, frame - 1); renderState(); };
    el("nextFrame").onclick = function () { frame = Math.min(state ? state.total_frames - 1 : 0, frame + 1); renderState(); };
    el("addObject").onclick = function () {
      var ids = state ? state.object_ids.slice() : [];
      var next = ids.length ? Math.max.apply(Math, ids) + 1 : 1;
      var option = document.createElement("option");
      option.value = String(next);
      option.textContent = "Object " + (next + 1);
      el("objectId").appendChild(option);
      el("objectId").value = String(next);
    };
    el("undoPoint").onclick = async function () { state = await api("POST", "/api/points/undo", {}); renderState(); };
    el("clearPoints").onclick = async function () { state = await api("POST", "/api/points/clear", {}); renderState(); };
    el("clearTracking").onclick = async function () { state = await api("POST", "/api/tracking/clear", {}); renderState(); setStatus("Tracking cleared; correction points kept"); };
    el("propagate").onclick = function () { startJob("/api/propagate", {}, "Tracking"); };
    el("deduplicate").onclick = function () { startJob("/api/dedupe", { threshold: number("dedupeThreshold") }, "Deduplication"); };
    el("runMatting").onclick = function () {
      var model = el("mattingModel").value;
      var terms = model === "VideoMaMa"
        ? "CC BY-NC 4.0 terms plus separate Stability AI Community License terms for its SVD VAE dependency"
        : "the S-Lab noncommercial license";
      if (confirmRestrictedModel(model.toLowerCase(), model, terms)) startJob("/api/matting", {}, "Matting");
    };
    el("runRemoval").onclick = function () {
      var method = el("removalMethod").value;
      if (method !== "MiniMax-Remover" || confirmRestrictedModel("minimax", method, "noncommercial terms that must be reviewed at the current model host")) {
        startJob("/api/removal", { method: method }, "Object removal");
      }
    };
    el("exportImport").onclick = function () {
      startJob("/api/export", { output_type: el("outputType").value, format: el("outputFormat").value, object_id: number("exportObject"), quality: number("quality"), name: el("outputName").value, output_dir: el("outputDestination").value }, "Export", importOutput);
    };
    el("chooseExportDestination").onclick = chooseExportDestination;
    el("clearExportDestination").onclick = function () { setExportDestination(""); setStatus("Using default export folder"); };
    el("cancelJob").onclick = function () { if (activeJob) api("POST", "/api/job/cancel", { id: activeJob.id }); };
    window.addEventListener("beforeunload", releaseServiceClient, false);
    el("revealOutput").onclick = function () {
      if (!latestOutput) return;
      if (process.platform === "darwin") child.spawn("open", ["-R", latestOutput.path], { detached: true });
      else child.spawn("explorer.exe", ["/select,", latestOutput.path], { detached: true });
    };
    el("samModel").onchange = async function () {
      setSam3PromptMode("point");
      updateSam3Controls();
      if (state) {
        try { await saveSettings(); renderState(); }
        catch (e) { setStatus(e.message, "error"); }
      }
      if (sam3ModelSelected(this.value)) refreshSam3Status();
    };
    [].forEach.call(document.querySelectorAll("#sam3PromptModes button"), function (button) {
      button.onclick = function () { if (!this.disabled) setSam3PromptMode(this.dataset.promptMode); };
    });
    el("sam3TextInput").oninput = updateSam3Controls;
    el("sam3TextInput").onkeydown = function (event) {
      if (event.key === "Enter" && !el("sam3TextSubmit").disabled) {
        event.preventDefault();
        submitSam3Text();
      }
    };
    el("sam3TextSubmit").onclick = submitSam3Text;
    el("sam3AccessPage").onclick = openSam3AccessPage;
    el("sam3Token").oninput = updateSam3Controls;
    el("sam3LicenseAccepted").onchange = updateSam3Controls;
    el("sam3Install").onclick = installSam3;
    el("removalMethod").onchange = updateRemovalFields;
    el("outputType").onchange = function () { if (currentMode === "output") { currentView = outputView(); refreshFrame(); } };
    el("objectId").onchange = refreshFrame;
    [].forEach.call(document.querySelectorAll(".mode"), function (button) { button.onclick = function () { switchMode(button.dataset.mode); }; });
    [].forEach.call(document.querySelectorAll(".preview-switch button"), function (button) { button.onclick = function () { currentView = button.dataset.view; [].forEach.call(button.parentNode.children, function (x) { x.classList.toggle("active", x === button); }); refreshFrame(); }; });
    ["showMasks", "showOutlines", "antialias", "grow", "holes", "dots", "borderFix"].forEach(function (id) { el(id).onchange = async function () { if (state) { await saveSettings(); refreshFrame(); } }; });
  }

  async function init() {
    bind();
    restoreViewerScale();
    restoreExportDestination();
    updateRemovalFields();
    updateSam3Controls();
    try {
      log("Panel initialization started; extension=" + extensionPath);
      await ensureService();
      await loadHostScript();
      log("After Effects host bridge loaded");
      state = await api("GET", "/api/state");
      if (state.loaded) {
        setDefaultOutputName(state.path);
        renderState();
      }
      await refreshSam3Status();
    } catch (e) { log("Panel initialization failed: " + e.stack); setStatus(e.message, "error"); }
  }
  init();
})();
