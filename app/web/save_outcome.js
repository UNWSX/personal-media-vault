(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MediaVaultSaveOutcome = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function assessSaveOutcome({
    commentResults,
    settledQueues,
    pendingDrafts,
    failedSaves,
    fieldConflicts,
  }) {
    const queueFailures = settledQueues.filter((entry) =>
      entry.status !== "fulfilled"
      || !entry.value
      || entry.value.ok !== true
    ).length;
    const commentFailures = commentResults.filter((value) => value !== true).length;
    const failures = queueFailures
      + commentFailures
      + Math.max(0, pendingDrafts)
      + Math.max(0, failedSaves)
      + Math.max(0, fieldConflicts);
    return {
      ok: failures === 0,
      failures,
      queueFailures,
      commentFailures,
    };
  }

  return { assessSaveOutcome };
});
