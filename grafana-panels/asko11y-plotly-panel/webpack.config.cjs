const path = require("path");
const { execFileSync } = require("child_process");
// Use the JS shipped with the same installed Plotly that validates/produces figures.
// Isolated builds set PLOTLY_PYTHON to the repository's existing interpreter.
const nativePlotly = execFileSync(
  process.env.PLOTLY_PYTHON ||
    path.resolve(__dirname, "../../.venv/bin/python"),
  [
    "-c",
    "from pathlib import Path; import plotly; print(Path(plotly.__file__).parent / 'package_data' / 'plotly.min.js')",
  ],
  { encoding: "utf8" },
).trim();

module.exports = {
  mode: "production",
  entry: "./src/module.tsx",
  devtool: "source-map",
  output: {
    filename: "module.js",
    path: path.resolve(__dirname, "dist"),
    libraryTarget: "amd",
    globalObject: "globalThis",
    clean: true,
  },
  externals: {
    react: "react",
    "@grafana/data": "@grafana/data",
    "@grafana/ui": "@grafana/ui",
  },
  module: {
    rules: [
      {
        test: /\.tsx?$/,
        exclude: /node_modules/,
        use: {
          loader: "swc-loader",
          options: {
            jsc: {
              parser: { syntax: "typescript", tsx: true },
              transform: { react: { runtime: "classic" } },
            },
          },
        },
      },
    ],
  },
  resolve: {
    extensions: [".tsx", ".ts", ".mjs", ".js"],
    alias: { "plotly.js-dist-min$": nativePlotly },
  },
  optimization: { minimize: true },
};
