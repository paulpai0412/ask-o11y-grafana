#!/usr/bin/env node
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const directory = fs.mkdtempSync(
  path.join(os.tmpdir(), "asko11y-build-stamp-"),
);
const stamp = () =>
  execFileSync(
    process.execPath,
    [path.join(__dirname, "stamp-ask-o11y-build.cjs"), directory],
    { stdio: "pipe" },
  )
    .toString()
    .trim();
try {
  fs.writeFileSync(
    path.join(directory, "plugin.json"),
    JSON.stringify({ id: "consensys-asko11y-app", info: { version: "0.3.5" } }),
  );
  fs.writeFileSync(path.join(directory, "module.js"), "first frontend");
  assert.throws(stamp, "A missing backend must prevent stamping");
  fs.writeFileSync(
    path.join(directory, "gpx_consensys-asko11y-app_linux_amd64"),
    "first backend",
  );
  const first = stamp();
  assert.match(first, /^0\.3\.5\+local\.[a-f0-9]{16}$/);
  assert.equal(stamp(), first, "Same build must keep the same cache version");
  fs.writeFileSync(path.join(directory, "module.js"), "second frontend");
  const second = stamp();
  assert.notEqual(
    second,
    first,
    "Changed frontend must invalidate the cached entrypoint",
  );
  fs.writeFileSync(
    path.join(directory, "gpx_consensys-asko11y-app_linux_amd64"),
    "second backend",
  );
  assert.notEqual(
    stamp(),
    second,
    "Changed backend must identify a different deployment",
  );
  fs.writeFileSync(
    path.join(directory, "plugin.json"),
    JSON.stringify({
      id: "consensys-asko11y-app",
      info: { version: "%VERSION%" },
    }),
  );
  assert.throws(stamp, "Unresolved webpack placeholders must fail closed");
  fs.writeFileSync(path.join(directory, "plugin.json"), "{");
  assert.throws(stamp, "Malformed manifests must fail closed");
  process.stdout.write(
    "PASS: build identity, cache invalidation, idempotency and missing/invalid build rejection\n",
  );
} finally {
  fs.rmSync(directory, { recursive: true, force: true });
}
