// Real built panel + native Plotly in Chromium. Only the Grafana shell is stubbed.
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const os = require("node:os");
const assert = require("node:assert/strict");
const {
  chromium,
} = require("../.scratch/ask-o11y-release-build/node_modules/playwright");
const root = path.resolve(__dirname, "..");
const out = path.resolve(
  process.argv[2] || path.join(root, ".scratch/plotly-delivery-first"),
);
const build = path.join(out, "panel-build/dist");
const deps = path.join(root, ".scratch/ask-o11y-release-build/node_modules");

(async () => {
  const figures = JSON.parse(
    fs.readFileSync(path.join(out, "figures.json"), "utf8"),
  );
  assert.equal(
    figures.length,
    10,
    "five native producers, four retained originals, one inert-markup check",
  );
  const dashboard = JSON.parse(
    fs.readFileSync(path.join(out, "dashboard.json"), "utf8"),
  );
  const charts = dashboard.panels.filter(
    (p) => p.type === "asko11y-plotly-panel",
  );
  const options = figures.map((figure, i) =>
    i < 5
      ? { ...charts[i].options, figure }
      : {
          figure,
          figureFormat: "ask-o11y-ml-plotly-v2",
          alt: i < 9 ? `Retained original figure ${i - 4}` : "Inert text",
        },
  );
  options.push(charts.at(-1).options);
  const files = {
    "/react.js": path.join(deps, "react/umd/react.production.min.js"),
    "/react-dom.js": path.join(
      deps,
      "react-dom/umd/react-dom.production.min.js",
    ),
    "/module.js": path.join(build, "module.js"),
  };
  const server = http.createServer((req, res) => {
    res.setHeader(
      "Content-Security-Policy",
      "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; font-src 'self' data:",
    );
    if (req.url === "/") {
      res.setHeader("Content-Type", "text/html; charset=utf-8");
      return res.end(
        '<!doctype html><meta charset="utf-8"><style>body{margin:20px;background:#111827;color:#e2e8f0;font-family:sans-serif}section.panel{height:820px;margin-bottom:24px;border:1px solid #334155}</style><script src="/react.js"></script><script src="/react-dom.js"></script><main id="root"></main>',
      );
    }
    if (req.url === "/favicon.ico") {
      res.writeHead(204);
      return res.end();
    }
    if (!files[req.url]) {
      res.writeHead(404);
      return res.end();
    }
    res.setHeader("Content-Type", "text/javascript");
    fs.createReadStream(files[req.url]).pipe(res);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "native-plotly-browser-"));
  let browser;
  const errors = [],
    blocked = [],
    runs = [];
  try {
    browser = await chromium.launch({
      headless: true,
      chromiumSandbox: true,
      executablePath:
        process.env.CHROME_BIN ||
        "/home/timmypai/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome",
      env: { HOME: home, PATH: process.env.PATH, LANG: "C.UTF-8" },
    });
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    await context.route("**/*", (route) => {
      if (
        !route
          .request()
          .url()
          .startsWith(origin + "/")
      ) {
        blocked.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await page.goto(origin, { waitUntil: "load" });
    await page.evaluate(() => {
      const theme = {
        colors: { text: { primary: "#e2e8f0" }, border: { weak: "#334155" } },
        typography: { fontFamily: "sans-serif" },
      };
      class PanelPlugin {
        constructor(component) {
          this.component = component;
        }
        setPanelOptions() {
          return this;
        }
      }
      window.define = (deps, factory) => {
        const externals = {
          react: window.React,
          "@grafana/data": { PanelPlugin },
          "@grafana/ui": { useTheme2: () => theme },
        };
        window.panel = factory(
          ...deps.map((dep) => externals[dep]),
        ).plugin.component;
      };
      window.define.amd = {};
    });
    await page.addScriptTag({ url: origin + "/module.js" });
    await page.evaluate((options) => {
      window.retainedOptions = options;
      window.before = JSON.stringify(options);
      window.ReactDOM.createRoot(document.getElementById("root")).render(
        window.React.createElement(
          window.React.Fragment,
          null,
          ...options.map((options, i) =>
            window.React.createElement(
              "section",
              { className: "panel", id: `p${i}`, key: i },
              window.React.createElement(window.panel, {
                options,
                height: 820,
              }),
            ),
          ),
        ),
      );
    }, options);
    await page.waitForFunction(
      () =>
        [...document.querySelectorAll(".js-plotly-plot")].filter(
          (e) => e._fullLayout && e.querySelector(".main-svg"),
        ).length === 10,
      {},
      { timeout: 30000 },
    );
    for (const width of [1440, 768]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.waitForFunction(() =>
        [...document.querySelectorAll(".js-plotly-plot")].every(
          (e) => Math.abs(e._fullLayout.width - e.clientWidth) < 3,
        ),
      );
      const state = await page.evaluate(() => {
        const plots = [...document.querySelectorAll(".js-plotly-plot")];
        return {
          types: plots.map((p) => p._fullData.map((t) => t.type)),
          annotations: plots.map((p) => p._fullLayout.annotations?.length || 0),
          sourceUnchanged:
            JSON.stringify(window.retainedOptions) === window.before,
          gap: plots[0].data[0].y,
          connectgaps: plots[0]._fullData[0].connectgaps,
          paths: plots[0].querySelectorAll(".scatterlayer .js-line").length,
          errorBars: plots[7].querySelectorAll(".errorbar").length,
          originalMissing: plots[5].data[0].y.at(-1),
          originalLength: plots[5].data[0].y.length,
          alerts: [...document.querySelectorAll('[role="alert"]')].map(
            (e) => e.textContent,
          ),
          evidenceRetained: document
            .getElementById("p10")
            .textContent.includes("Observations: 4"),
          activeNodes: document.querySelectorAll(
            ".js-plotly-plot script, .js-plotly-plot iframe, .main-svg a[href]",
          ).length,
          executed: !!window.__executed,
          overflow: document.documentElement.scrollWidth > innerWidth,
        };
      });
      runs.push({ width, state });
      fs.writeFileSync(
        path.join(out, "browser.json"),
        JSON.stringify(
          {
            browser: browser.version(),
            runtime: JSON.parse(
              fs.readFileSync(path.join(build, "plotly-runtime.json"), "utf8"),
            ),
            runs,
            errors,
            blocked,
          },
          null,
          2,
        ),
      );
      assert.deepEqual(state.gap, [2, null, 4, 3]);
      assert.equal(state.connectgaps, false);
      assert.equal(state.paths, 2);
      assert.equal(state.originalMissing, null);
      assert.equal(state.originalLength, 139);
      assert.equal(state.annotations[3], 4);
      assert.equal(state.annotations[8], 4);
      assert(state.errorBars > 0);
      assert.deepEqual(state.types[4], ["scatterpolar"]);
      assert.equal(state.alerts.length, 1);
      assert(state.evidenceRetained && state.sourceUnchanged);
      assert(!state.executed && !state.activeNodes && !state.overflow);
      assert.deepEqual(errors, []);
      assert.deepEqual(blocked, []);
      for (const index of [3, 8, 10])
        await page
          .locator(`#p${index}`)
          .screenshot({
            path: path.join(out, `browser-${width}-panel-${index}.png`),
          });
    }
    await context.close();
    console.log(
      "PASS: compiled panel, native Plotly 3.7, ten intact figures, original missing gap/CI/subplots, polar, local error and facts, inert markup, no requests/eval, immutable options, desktop/tablet",
    );
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(home, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
