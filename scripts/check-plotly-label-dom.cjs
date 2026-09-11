#!/usr/bin/env node
// Actual installed Plotly SVG/text code; jsdom geometry stubs, NOT browser QA.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { createRequire } = require("node:module");
const root = path.resolve(__dirname, "..");
const appRequire = createRequire(
  path.join(root, ".scratch/ask-o11y-release-build/package.json"),
);
const { JSDOM, ResourceLoader } = appRequire("jsdom");
const fixture = process.env.PLOTLY_LABEL_FIXTURE_OUT;
assert.ok(
  fixture,
  "generate PLOTLY_LABEL_FIXTURE_OUT with check-plotly-labels.py",
);
let figure;
try {
  figure = JSON.parse(fs.readFileSync(fixture, "utf8"));
} catch (error) {
  throw new Error("invalid Plotly label fixture", { cause: error });
}
let requests = 0;
class NoNetwork extends ResourceLoader {
  fetch() {
    requests++;
    throw new Error("unexpected external resource");
  }
}
const dom = new JSDOM('<div id="chart"></div>', {
  runScripts: "outside-only",
  resources: new NoNetwork(),
});
const w = dom.window;
w.URL.createObjectURL = () => "blob:local-fixture";
w.HTMLCanvasElement.prototype.getContext = () => ({
  measureText: (text) => ({ width: String(text).length * 7 }),
});
w.SVGElement.prototype.getBBox = function () {
  return { x: 0, y: 0, width: (this.textContent || "").length * 7, height: 12 };
};
w.SVGElement.prototype.getComputedTextLength = function () {
  return (this.textContent || "").length * 7;
};
w.fetch = () => {
  requests++;
  throw new Error("unexpected fetch");
};
const bundle = path.join(
  root,
  "grafana-panels/asko11y-plotly-panel/node_modules/plotly.js-dist-min/plotly.min.js",
);
w.eval(fs.readFileSync(bundle, "utf8"));
const chart = w.document.getElementById("chart");
(async () => {
  try {
    await w.Plotly.newPlot(
      chart,
      figure.data,
      { ...figure.layout, width: 900, height: 500 },
      { ...figure.config, displayModeBar: false },
    );
    const svg = chart.querySelector(".main-svg");
    assert.ok(svg);
    const text = [...chart.querySelectorAll("svg text")].map(
      (n) => n.textContent,
    );
    for (const label of [
      "p < 0.05",
      "Temperature > 100",
      "a < b",
      "1 < x < 3",
      "x <= 2",
      "x ≥ 0",
      "A & B",
      "<b>literal</b>",
    ]) {
      assert.ok(text.includes(label), `missing literal text: ${label}`);
    }
    assert.equal(
      svg.querySelectorAll("a, script, image, foreignObject").length,
      0,
    );
    assert.equal(requests, 0);
    assert.deepEqual(
      Array.from(chart.data[0].x),
      figure.data[0].x,
      "category identities changed",
    );
    assert.equal(new Set(chart.data[0].x).size, figure.data[0].x.length);
    console.log(
      `PASS installed Plotly ${w.Plotly.version}: literal SVG text, distinct raw/entity categories, no active nodes or external requests (jsdom, not browser)`,
    );
  } finally {
    w.Plotly.purge(chart);
    dom.window.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
