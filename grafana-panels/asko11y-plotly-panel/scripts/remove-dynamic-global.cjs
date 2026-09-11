const fs = require("fs");
const path = require("path");

const bundlePath = path.resolve(__dirname, "../dist/module.js");
const source = fs.readFileSync(bundlePath, "utf8");
const unsafe = 'new Function("return this")()';
const occurrences = source.split(unsafe).length - 1;
if (occurrences !== 1) {
  throw new Error(
    `expected exactly one webpack global fallback, found ${occurrences}`,
  );
}
const cleaned = source.replace(unsafe, "globalThis");
if (cleaned.includes("new Function") || cleaned.includes("eval(")) {
  throw new Error("built panel still contains dynamic code execution");
}
fs.writeFileSync(bundlePath, cleaned);
const { createHash } = require("crypto");
const nativePath = require("../webpack.config.cjs").resolve.alias[
  "plotly.js-dist-min$"
];
const nativeSource = fs.readFileSync(nativePath);
const version = nativeSource
  .toString("utf8", 0, 200)
  .match(/plotly\.js v([\d.]+)/)?.[1];
if (!version) throw new Error("Native Plotly version is missing");
fs.writeFileSync(
  path.resolve(__dirname, "../dist/plotly-runtime.json"),
  JSON.stringify(
    {
      figure_format: "ask-o11y-ml-plotly-v2",
      plotly_js_version: version,
      source_sha256: createHash("sha256").update(nativeSource).digest("hex"),
      panel_sha256: createHash("sha256").update(cleaned).digest("hex"),
    },
    null,
    2,
  ) + "\n",
);
