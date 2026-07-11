(function () {
  "use strict";

  var cs = new CSInterface();
  var nodeRequire = typeof require === "function" ? require : (window.cep_node && window.cep_node.require);
  if (!nodeRequire) throw new Error("CEP Node integration is unavailable");
  var fs = nodeRequire("fs");
  var path = nodeRequire("path");
  var os = nodeRequire("os");
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

  function el(id) { return document.getElementById(id); }
  function number(id) { return Number(el(id).value); }
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

  async function ensureService() {
    log("Checking service at " + baseUrl);
    try {
      var health = await api("GET", "/health");
      log("Service already running on " + health.device);
      setStatus("Processing service connected");
      return;
    } catch (e) { log("Initial health check failed: " + e.message); }
    setStatus("Starting processing service...", "");
    var script = path.join(extensionPath, "backend", "service.py");
    var logDir = runtimeLogDir;
    if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });
    var out = fs.openSync(path.join(logDir, "service.log"), "a");
    var spawnEnv = Object.assign({}, process.env, config.environment || {});
    var proc = child.spawn(config.python, [script, "--repo", config.repo, "--port", String(config.port)], {
      cwd: config.repo, detached: true, windowsHide: true, stdio: ["ignore", out, out], env: spawnEnv
    });
    proc.on("error", function (error) { log("Service spawn error: " + error.message); });
    proc.unref();
    for (var i = 0; i < 60; i++) {
      await sleep(500);
      try {
        await api("GET", "/health");
        log("Spawned service is healthy");
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
    state.points.slice().reverse().slice(0, 20).forEach(function (p) {
      var row = document.createElement("div");
      row.className = "point-row";
      row.innerHTML = "<b>F" + (p.frame + 1) + "</b><span>Object " + (p.object_id + 1) + "</span><span>" + (p.positive ? "Include" : "Exclude") + "</span>";
      list.appendChild(row);
    });
  }
  function renderState() {
    if (!state) return;
    el("frameSlider").max = Math.max(0, state.total_frames - 1);
    frame = Math.max(0, Math.min(frame, state.total_frames - 1));
    el("frameSlider").value = frame;
    el("frameReadout").textContent = (frame + 1) + " / " + state.total_frames;
    el("viewerEmpty").style.display = state.loaded ? "none" : "grid";
    var tracked = state.tracking || { frames: 0, complete: false };
    el("trackingStatus").textContent = tracked.complete ? "Tracked: complete" : (tracked.frames ? "Masks: " + tracked.frames + " / " + state.total_frames + " frames" : "Not tracked");
    updateObjects();
    updatePointList();
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
      selectedLayer = info;
      el("clipName").textContent = info.layerName;
      setDefaultOutputName(info.sourceName || info.path);
      setStatus("Decoding " + info.sourceName + "...");
      state = await api("POST", "/api/load", { path: info.path });
      frame = Math.max(0, Math.min(state.total_frames - 1, Math.round(info.sourceTime * state.fps)));
      renderState();
      setStatus("Loaded " + state.total_frames + " frames");
    } catch (e) { setStatus(e.message, "error"); }
  }

  async function addPoint(event) {
    if (!state || !state.loaded || activeJob) return;
    event.preventDefault();
    var canvas = el("viewer");
    var rect = canvas.getBoundingClientRect();
    var x = Math.round((event.clientX - rect.left) * canvas.width / rect.width);
    var y = Math.round((event.clientY - rect.top) * canvas.height / rect.height);
    var isPositive = event.type === "contextmenu" ? false : positive;
    try {
      setStatus(state.segmentation_ready ? "Updating mask..." : "Loading segmentation model...");
      await saveSettings();
      state = await api("POST", "/api/points", { frame: frame, object_id: number("objectId"), positive: isPositive, x: x, y: y });
      renderState();
      setStatus(isPositive ? "Include point added" : "Exclude point added");
    } catch (e) { setStatus(e.message, "error"); }
  }

  async function startJob(endpoint, body, label, onComplete) {
    try {
      await saveSettings();
      activeJob = await api("POST", endpoint, body || {});
      el("jobBar").classList.remove("hidden");
      el("jobLabel").textContent = label;
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
      state = await api("GET", "/api/state");
      refreshFrame();
      setStatus(label + " complete");
      if (onComplete) await onComplete(result);
    } catch (e) {
      activeJob = null;
      el("jobBar").classList.add("hidden");
      setStatus(e.message, "error");
    }
  }

  async function importOutput(result) {
    latestOutput = result;
    var referenceIndex = selectedLayer ? selectedLayer.layerIndex : -1;
    var raw = await evalHost("$._samosaAE.importResult(" + escapeJs(result.path) + "," + (result.sequence ? "true" : "false") + "," + referenceIndex + ")");
    var response = JSON.parse(raw);
    if (!response.ok) throw new Error(response.error);
    setStatus("Added " + response.layerName + " to comp");
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
    el("positiveTool").onclick = function () { positive = true; this.classList.add("selected"); el("negativeTool").classList.remove("selected"); };
    el("negativeTool").onclick = function () { positive = false; this.classList.add("selected"); el("positiveTool").classList.remove("selected"); };
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
    el("revealOutput").onclick = function () {
      if (!latestOutput) return;
      if (process.platform === "darwin") child.spawn("open", ["-R", latestOutput.path], { detached: true });
      else child.spawn("explorer.exe", ["/select,", latestOutput.path], { detached: true });
    };
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
    } catch (e) { log("Panel initialization failed: " + e.stack); setStatus(e.message, "error"); }
  }
  init();
})();
