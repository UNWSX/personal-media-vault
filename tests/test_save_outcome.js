"use strict";

const assert = require("assert");
const { assessSaveOutcome } = require("../app/web/save_outcome.js");

function assess(overrides = {}) {
  return assessSaveOutcome(Object.assign({
    commentResults: [],
    settledQueues: [],
    pendingDrafts: 0,
    failedSaves: 0,
    fieldConflicts: 0,
  }, overrides));
}

assert.strictEqual(assess().ok, true);

assert.strictEqual(assess({
  settledQueues: [{ status: "fulfilled", value: { ok: false, conflict: false } }],
}).ok, false);

assert.strictEqual(assess({
  settledQueues: [{ status: "rejected", reason: new Error("network") }],
}).ok, false);

assert.strictEqual(assess({
  settledQueues: [
    { status: "fulfilled", value: { ok: true, conflict: false } },
    { status: "fulfilled", value: { ok: false, conflict: false } },
  ],
}).ok, false);

assert.strictEqual(assess({ pendingDrafts: 1 }).ok, false);
assert.strictEqual(assess({ failedSaves: 1 }).ok, false);
assert.strictEqual(assess({ fieldConflicts: 1 }).ok, false);
const partial = assess({
  settledQueues: [
    { status: "fulfilled", value: { ok: true } },
    { status: "fulfilled", value: { ok: false, conflict: false } },
  ],
  failedSaves: 1,
});
assert.strictEqual(partial.ok, false);
assert.strictEqual(partial.queueFailures, 1);
assert.strictEqual(partial.failures, 2);

console.log("save-outcome-tests: PASS");
