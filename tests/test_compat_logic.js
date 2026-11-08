"use strict";

const assert = require("assert");
const {
  conflictGroup,
  dirtyKeysBetween,
  mergeLatestWithLocal,
  networkSortScore,
  threeWayDecision,
  threeWayGroupDecision,
  seriesSortKey,
} = require("../app/web/compat_logic.js");

assert.deepStrictEqual(
  conflictGroup({ progressStatus: "playing" }, { type: "game" }).sort(),
  ["progressStatus", "status"],
);

assert.deepStrictEqual(
  conflictGroup({ doubanScore: 8.7 }, { type: "movie" }).sort(),
  ["doubanScore", "score"],
);

assert.deepStrictEqual(
  conflictGroup({ metacriticScore: 90 }, { type: "game" }).sort(),
  ["metacriticScore", "score"],
);

const base = { comment: "old", status: false, progressStatus: "planned", revision: "r1" };
const local = { comment: "draft", status: true, progressStatus: "completed", revision: "r1" };
const latest = {
  comment: "remote",
  status: false,
  progressStatus: "planned",
  revision: "r2",
  title: "new title",
};

assert.strictEqual(threeWayDecision(base.comment, local.comment, latest.comment), "conflict");
assert.strictEqual(threeWayDecision(base.comment, base.comment, latest.comment), "accept_latest");
assert.strictEqual(threeWayDecision(base.comment, local.comment, base.comment), "accept_local");

const statusKeys = conflictGroup({ progressStatus: "completed" }, { type: "movie" });
const statusBase = { status: false, progressStatus: "planned" };
const statusLocal = { status: false, progressStatus: "completed" };
const statusRemote = { status: true, progressStatus: "planned" };
assert.strictEqual(
  threeWayGroupDecision(statusBase, statusLocal, statusRemote, statusKeys),
  "conflict",
);
assert.strictEqual(
  threeWayGroupDecision(statusBase, statusLocal, statusLocal, statusKeys),
  "accept_local",
);

const ratingKeys = conflictGroup({ doubanScore: 8.7 }, { type: "movie" });
const ratingBase = { score: 8.2, doubanScore: null };
const ratingLocal = { score: 8.2, doubanScore: 8.7 };
const ratingRemote = { score: 8.8, doubanScore: null };
assert.strictEqual(
  threeWayGroupDecision(ratingBase, ratingLocal, ratingRemote, ratingKeys),
  "conflict",
);

assert.strictEqual(
  seriesSortKey({ type: "movie", title: "蜘蛛侠" }),
  seriesSortKey({ type: "movie", title: "蜘蛛侠：英雄归来" }),
);
assert.strictEqual(
  seriesSortKey({ type: "movie", title: "雷神4：爱与雷霆" }),
  seriesSortKey({ type: "movie", title: "雷神" }),
);
assert.strictEqual(
  seriesSortKey({ type: "book", title: "三体Ⅱ" }),
  seriesSortKey({ type: "book", title: "三体" }),
);
assert.strictEqual(
  seriesSortKey({ type: "book", title: "三体Ⅲ" }),
  seriesSortKey({ type: "book", title: "三体" }),
);
assert.strictEqual(
  seriesSortKey({ type: "game", title: "漫威蜘蛛侠 复刻版" }),
  seriesSortKey({ type: "game", title: "漫威蜘蛛侠2" }),
);
assert.strictEqual(
  seriesSortKey({ type: "teleplay", title: "无职转生Ⅱ到了异世界就拿出真本事Part.1" }),
  seriesSortKey({ type: "teleplay", title: "无职转生Ⅱ到了异世界就拿出真本事Part2" }),
);
assert.strictEqual(
  networkSortScore({ imdbScore: 8.2, doubanScore: 9.5 }),
  82,
);
assert.strictEqual(
  networkSortScore({ imdbScore: null, doubanScore: 9.5 }),
  95,
);
assert.strictEqual(
  networkSortScore({ type: "game", score: 88, doubanScore: null }),
  88,
);

const dirty = dirtyKeysBetween(base, local, ["progressStatus", "status"]);
assert.deepStrictEqual(dirty.sort(), ["comment", "progressStatus", "status"]);

const merged = mergeLatestWithLocal(latest, local, dirty);
assert.strictEqual(merged.revision, "r2");
assert.strictEqual(merged.title, "new title");
assert.strictEqual(merged.comment, "draft");
assert.strictEqual(merged.progressStatus, "completed");

console.log("compat-logic-tests: PASS");
