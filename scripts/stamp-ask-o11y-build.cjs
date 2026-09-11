#!/usr/bin/env node
// Grafana caches module.js by plugin version; bind that version to the shipped build.
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

assert(
  process.argv[2],
  "Usage: stamp-ask-o11y-build.cjs <built-plugin-directory>",
);
const directory = process.argv[2];
const manifestPath = path.join(directory, "plugin.json");
let manifest;
try {
  manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
} catch (cause) {
  throw new Error(`Cannot stamp invalid build manifest: ${manifestPath}`, {
    cause,
  });
}
assert.equal(manifest.id, "consensys-asko11y-app");
const version = manifest.info.version.split("+")[0];
assert.match(
  version,
  /^\d+\.\d+\.\d+(?:-[\w.-]+)?$/,
  "Build version must be resolved before stamping",
);
const digest = createHash("sha256");
for (const name of ["module.js", "gpx_consensys-asko11y-app_linux_amd64"]) {
  digest.update(fs.readFileSync(path.join(directory, name)));
}
manifest.info.version = `${version}+local.${digest.digest("hex").slice(0, 16)}`;
fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
process.stdout.write(`${manifest.info.version}\n`);
