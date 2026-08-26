const fs = require('fs');
const path = require('path');

const bundlePath = path.resolve(__dirname, '../dist/module.js');
const source = fs.readFileSync(bundlePath, 'utf8');
const unsafe = 'new Function("return this")()';
const occurrences = source.split(unsafe).length - 1;
if (occurrences !== 1) {
  throw new Error(`expected exactly one webpack global fallback, found ${occurrences}`);
}
const cleaned = source.replace(unsafe, 'globalThis');
if (cleaned.includes('new Function') || cleaned.includes('eval(')) {
  throw new Error('built panel still contains dynamic code execution');
}
fs.writeFileSync(bundlePath, cleaned);
