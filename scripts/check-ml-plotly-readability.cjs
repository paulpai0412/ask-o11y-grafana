#!/usr/bin/env node
// Real component SSR regression; not a substitute for browser/render acceptance.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const root = path.resolve(__dirname, "..");
const panel = path.join(root, "grafana-panels/asko11y-plotly-panel");
const panelRequire = Module.createRequire(path.join(panel, "package.json"));
const appRequire = Module.createRequire(
  path.join(root, ".scratch/ask-o11y-release-build/package.json"),
);
const React = appRequire("react");
const { renderToStaticMarkup } = appRequire("react-dom/server");
const { transformSync } = panelRequire("@swc/core");
const filename = path.join(panel, "src/module.tsx");
let Component;
const theme = {
  colors: { text: { primary: "#ddd" }, border: { weak: "#444" } },
  typography: { fontFamily: "sans-serif" },
};
const compiled = new Module(filename);
compiled.filename = filename;
const sourceRequire = Module.createRequire(filename);
compiled.require = (name) => {
  if (name === "react") return React;
  if (name === "@grafana/ui") return { useTheme2: () => theme };
  if (name === "@grafana/data")
    return {
      PanelPlugin: class {
        constructor(component) {
          Component = component;
        }
        setPanelOptions() {
          return this;
        }
      },
    };
  if (name === "plotly.js-dist-min") return {};
  return sourceRequire(name);
};
compiled._compile(
  transformSync(fs.readFileSync(filename, "utf8"), {
    filename,
    jsc: {
      parser: { syntax: "typescript", tsx: true },
      transform: { react: { runtime: "classic" } },
    },
    module: { type: "commonjs" },
  }).code,
  filename,
);
const narrative = {
  headline: "Evidence retained",
  evidence: [],
  observation: "Observation",
  interpretation: "Interpretation",
  limitation: "Limitations",
  next_step: "Next",
  cross_chart_context: "Context",
};
const options = {
  figure: {
    data: [{ type: "scatter", mode: "lines", x: [1, 2], y: [3, 4] }],
    layout: {},
  },
  viewNarratives: [
    {
      ...narrative,
      view_id: "view-1",
      data_observation: "Full data retained",
      visual_observation: null,
    },
  ],
};
const original = JSON.stringify(options);
const small = renderToStaticMarkup(
  React.createElement(Component, { options, height: 300 }),
);
const large = renderToStaticMarkup(
  React.createElement(Component, { options, height: 800 }),
);
assert.match(small, /height:320px/);
assert.match(large, /height:700px/);
assert.match(large, /minmax\(min\(100%, 480px\), 1fr\)/);
assert.match(large, /<details><summary[^>]*>解讀與證據<\/summary>/);
assert.doesNotMatch(large, /<details[^>]*\bopen(?:[ =>])/);
assert.match(large, /Full data retained/);
assert.doesNotMatch(large, /grid-template-rows/);
assert.equal(
  JSON.stringify(options),
  original,
  "rendering must not rewrite evidence",
);
// The main explanation must survive when optional per-view captions are absent.
const concise = { ...narrative };
delete concise.cross_chart_context;
delete concise.next_step;
for (const mode of ["plotly", "image"]) {
  const minimal =
    mode === "plotly"
      ? { ...options, narrative: concise, viewNarratives: [] }
      : {
          renderMode: "image",
          fallbackUrl: "synthetic.png",
          narrative: concise,
          viewNarratives: [],
        };
  const html = renderToStaticMarkup(
    React.createElement(Component, { options: minimal, height: 600 }),
  );
  for (const content of [
    "Evidence retained",
    "Observation",
    "Interpretation",
    "Limitations",
  ])
    assert.ok(html.includes(content), `${mode}: missing ${content}`);
  assert.doesNotMatch(html, /跨图关系|下一步|跨圖解讀與證據/);
}
const view = { ...options.viewNarratives[0] };
delete view.next_step;
const focused = renderToStaticMarkup(
  React.createElement(Component, {
    options: { ...options, viewNarratives: [view] },
    height: 600,
  }),
);
assert.match(focused, /Full data retained/);
assert.doesNotMatch(focused, /下一步/);
if (process.env.REPORT_SYNTHESIS_FIXTURE_OUT) {
  let fixtures;
  try {
    fixtures = JSON.parse(
      fs.readFileSync(process.env.REPORT_SYNTHESIS_FIXTURE_OUT, "utf8"),
    );
  } catch (error) {
    throw new Error("invalid report producer fixture", { cause: error });
  }
  for (const [kind, settings] of Object.entries(fixtures)) {
    const html = renderToStaticMarkup(
      React.createElement(Component, { options: settings, height: 600 }),
    );
    assert.ok(
      html.includes(renderToStaticMarkup(settings.narrative.interpretation)),
      `${kind}: producer interpretation lost`,
    );
    assert.ok(html.includes("42.0%"), `${kind}: producer fact lost`);
    if (process.env.REPORT_TEXT_HTML_OUT) {
      const { JSDOM } = appRequire("jsdom");
      const dom = new JSDOM(
        html + fs.readFileSync(process.env.REPORT_TEXT_HTML_OUT, "utf8"),
      );
      const body = dom.window.document.body;
      assert.ok(body.textContent.includes(settings.narrative.interpretation));
      assert.ok(
        body.textContent.includes("p < 0.05"),
        "retained fact text lost",
      );
      assert.equal(
        body.querySelectorAll("script, a, img, svg, iframe, object").length,
        0,
      );
      assert.ok(html.includes("&lt;"), "React text was not escaped");
      dom.window.close();
    }
    assert.ok(
      html.includes(renderToStaticMarkup(settings.narrative.limitation)),
      `${kind}: producer limitation lost`,
    );
  }
}
for (const file of [
  "patches/ask-o11y-nlap-authority-and-effects.patch",
  "scripts/configure-ask-o11y-workflow-tools.py",
]) {
  const policy = fs.readFileSync(path.join(root, file), "utf8");
  for (const phrase of [
    "not a fixed workflow or layout template",
    "A successful write proves persistence only",
    "visual verification is pending",
    "do not silently drop series",
  ]) {
    assert.ok(policy.includes(phrase), `${file}: missing ${phrase}`);
  }
}
console.log(
  "ok: real component sizing, disclosure, evidence preservation and completion guidance (SSR, not visual acceptance)",
);
