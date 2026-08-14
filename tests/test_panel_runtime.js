"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const panelPath = path.join(__dirname, "..", "panel", "js", "panel.js");
const source = fs.readFileSync(panelPath, "utf8");

function extractFunction(name) {
  const start = source.indexOf("  function " + name + "(");
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

const finiteNumberSource = extractFunction("finiteNumber");
const frameAtSourceTimeSource = extractFunction("frameAtSourceTime");
const frameAtSourceTime = new Function(
  finiteNumberSource + "\n" + frameAtSourceTimeSource + "\nreturn frameAtSourceTime;"
)();

assert.strictEqual(frameAtSourceTime(0, 24), 0);
assert.strictEqual(frameAtSourceTime(2 / 24, 24), 2);
assert.strictEqual(frameAtSourceTime(5 / 24 - 1e-10, 24), 5);
assert.strictEqual(frameAtSourceTime(2.49 / 24, 24), 2);
assert.strictEqual(frameAtSourceTime(2.51 / 24, 24), 2);

process.stdout.write("panel timing runtime tests passed\n");
