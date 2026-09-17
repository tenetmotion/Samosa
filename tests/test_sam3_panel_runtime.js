"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const panelPath = path.join(root, "panel", "js", "panel.js");
const htmlPath = path.join(root, "panel", "index.html");
const cssPath = path.join(root, "panel", "css", "panel.css");
const source = fs.readFileSync(panelPath, "utf8");
const html = fs.readFileSync(htmlPath, "utf8");
const css = fs.readFileSync(cssPath, "utf8");

function extractFunction(name) {
  const ordinary = source.indexOf("  function " + name + "(");
  const asynchronous = source.indexOf("  async function " + name + "(");
  const start = ordinary >= 0 ? ordinary : asynchronous;
  assert.notStrictEqual(start, -1, "Missing function " + name);
  let depth = 0;
  let opened = false;
  for (let index = start; index < source.length; index += 1) {
    if (source[index] === "{") {
      depth += 1;
      opened = true;
    } else if (source[index] === "}") {
      depth -= 1;
      if (opened && depth === 0) return source.slice(start + 2, index + 1);
    }
  }
  throw new Error("Unterminated function " + name);
}

function loadFunction(name, dependencies) {
  const body = (dependencies || []).map(extractFunction).join("\n");
  return new Function(body + "\n" + extractFunction(name) + "\nreturn " + name + ";")();
}

const sam3ModelSelected = loadFunction("sam3ModelSelected");
const sam3ControlsVisible = loadFunction("sam3ControlsVisible", ["sam3ModelSelected"]);
const sam3Availability = loadFunction("sam3Availability");
const sam3CapabilitySupported = loadFunction("sam3CapabilitySupported");
const clamp01 = loadFunction("clamp01", ["finiteNumber"]);
const normalizeCanvasPoint = loadFunction("normalizeCanvasPoint", ["finiteNumber", "clamp01"]);
const normalizeCanvasRect = loadFunction("normalizeCanvasRect", ["finiteNumber", "clamp01", "normalizeCanvasPoint"]);
const buildSam3PromptPayload = loadFunction("buildSam3PromptPayload", ["finiteNumber", "clamp01"]);
const redactSam3Message = loadFunction("redactSam3Message");
const friendlySam3Message = loadFunction("friendlySam3Message", ["redactSam3Message"]);

assert.strictEqual(sam3ModelSelected("Base"), false);
assert.strictEqual(sam3ModelSelected("SAM3.1"), true);
assert.strictEqual(sam3ControlsVisible("Base"), false);
assert.strictEqual(sam3ControlsVisible("SAM3.1", { availability: false }), true);
assert.strictEqual(sam3Availability({ availability: true }), true);
assert.strictEqual(sam3Availability({ availability: "installed" }), true);
assert.strictEqual(sam3Availability({ availability: false }), false);
assert.strictEqual(sam3CapabilitySupported({ prompts: { point: true, box: false } }, "point"), true);
assert.strictEqual(sam3CapabilitySupported({ prompts: { point: true, box: false } }, "box"), false);

const rect = { left: 100, top: 200, width: 200, height: 100 };
assert.deepStrictEqual(normalizeCanvasPoint(150, 250, rect), { x: 0.25, y: 0.5 });
assert.deepStrictEqual(normalizeCanvasPoint(-50, 400, rect), { x: 0, y: 1 });
assert.deepStrictEqual(
  normalizeCanvasRect({ x: 300, y: 260 }, { x: 100, y: 200 }, rect),
  { x1: 0, y1: 0, x2: 1, y2: 0.6 }
);

assert.deepStrictEqual(
  buildSam3PromptPayload("point", 2, 7, { x: 1.4, y: -0.2 }, "negative"),
  { type: "point", object_id: 2, frame: 7, x: 1, y: 0, label: "negative" }
);
assert.deepStrictEqual(
  buildSam3PromptPayload("box", 0, 3, { x1: 0.2, y1: 0.1, x2: 0.8, y2: 0.9 }, "negative"),
  { type: "box", object_id: 0, frame: 3, x1: 0.2, y1: 0.1, x2: 0.8, y2: 0.9, label: "positive" }
);
assert.deepStrictEqual(
  buildSam3PromptPayload("example", 1, 4, { x1: 0.1, y1: 0.2, x2: 0.5, y2: 0.7 }, "negative"),
  { type: "example", object_id: 1, frame: 4, x1: 0.1, y1: 0.2, x2: 0.5, y2: 0.7, label: "negative" }
);
assert.deepStrictEqual(
  buildSam3PromptPayload("text", 1, 4, { text: "  red jacket  " }, "positive"),
  { type: "text", object_id: 1, frame: 4, text: "red jacket" }
);

assert.match(html, /option value="SAM3\.1">SAM 3\.1</);
assert.match(html, /data-prompt-mode="point"/);
assert.match(html, /data-prompt-mode="box"/);
assert.match(html, /data-prompt-mode="text"/);
assert.match(html, /data-prompt-mode="example"/);
assert.match(html, /id="sam3AccessPage"/);
assert.match(html, /id="sam3Token" type="password"/);
assert.match(html, /id="sam3LicenseAccepted" type="checkbox"/);
assert.match(html, /id="promptBoxOverlay"/);

assert.match(source, /addEventListener\("pointerdown", beginSam3Drag\)/);
assert.match(source, /addEventListener\("pointermove", moveSam3Drag\)/);
assert.match(source, /addEventListener\("pointerup", finishSam3Drag\)/);
assert.match(source, /addEventListener\("pointercancel", cancelSam3Drag\)/);
assert.match(source, /event\.key === "Enter"/);
assert.match(source, /openURLInDefaultBrowser/);
assert.match(source, /api\("POST", "\/api\/sam3\/setup"/);
assert.match(source, /accept_license: true/);
assert.match(source, /monitorJob\(setupJob, "SAM 3\.1 setup", refreshSam3Status, false\)/);
assert.match(source, /activeJob = job/);
assert.ok(
  source.indexOf('state = await api("GET", "/api/state")') <
    source.lastIndexOf("await refreshSam3Status()"),
  "SAM 3.1 status should refresh after restored state selects its saved model"
);
assert.doesNotMatch(source, /localStorage\.setItem\([^\n]*sam3Token/);
assert.doesNotMatch(source, /localStorage\.setItem\([^\n]*hf_token/);
assert.strictEqual(redactSam3Message("download failed for hf_secret_token", "hf_secret_token"), "download failed for [redacted]");
assert.strictEqual(redactSam3Message("invalid hf_ABC-123 token"), "invalid [redacted] token");
assert.match(
  friendlySam3Message("403 Client Error. Cannot access gated repo; you are not in the authorized list."),
  /same account/
);
assert.doesNotMatch(
  friendlySam3Message("403 Client Error. Cannot access gated repo; you are not in the authorized list."),
  /403 Client Error/
);
assert.match(html, /Hugging Face has granted this account model access/);
assert.match(html, /placeholder="Token from approved account"/);

assert.match(css, /\.prompt-box-overlay[^\n]*pointer-events: none/);
assert.match(css, /\.prompt-modes[^\n]*grid-template-columns: repeat\(4/);
assert.match(css, /@media \(max-width: 380px\)/);

function monitorHarness(terminalJob) {
  return new Function(`
    var activeJob = null;
    var state = null;
    var hidden = true;
    var elements = {
      jobBar: { classList: {
        add: function (name) { if (name === "hidden") hidden = true; },
        remove: function (name) { if (name === "hidden") hidden = false; }
      } },
      jobLabel: { textContent: "" },
      jobProgress: { value: 0 },
      jobPercent: { textContent: "" }
    };
    function el(id) { return elements[id]; }
    async function sleep() {}
    async function api() { return ${JSON.stringify(terminalJob)}; }
    function refreshFrame() {}
    function setStatus() {}
    ${extractFunction("monitorJob")}
    return {
      run: monitorJob,
      active: function () { return activeJob; },
      hidden: function () { return hidden; }
    };
  `)();
}

(async function () {
  var failed = monitorHarness({ id: "setup", status: "failed", progress: 25, message: "setup failed" });
  await assert.rejects(
    failed.run({ id: "setup", status: "queued", progress: 0 }, "SAM 3.1 setup", null, false),
    /setup failed/
  );
  assert.strictEqual(failed.active(), null);
  assert.strictEqual(failed.hidden(), true);

  var cancelled = monitorHarness({ id: "setup", status: "cancelled", progress: 40 });
  await assert.rejects(
    cancelled.run({ id: "setup", status: "running", progress: 10 }, "SAM 3.1 setup", null, false),
    /cancelled/
  );
  assert.strictEqual(cancelled.active(), null);
  assert.strictEqual(cancelled.hidden(), true);
  process.stdout.write("SAM 3.1 panel runtime tests passed\n");
})().catch(function (error) {
  process.stderr.write((error.stack || String(error)) + "\n");
  process.exitCode = 1;
});
