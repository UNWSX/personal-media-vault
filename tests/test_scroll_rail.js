"use strict";

const assert = require("assert");
const {
  metrics,
  thumbOffset,
  scrollTopForOffset,
  scrollTopForPointer,
} = require("../app/web/scroll_rail.js");

const longPage = metrics(5000, 800, 600);
assert.strictEqual(longPage.maxScroll, 4200);
assert.strictEqual(longPage.thumbHeight, 96);
assert.strictEqual(longPage.travel, 504);
assert.strictEqual(thumbOffset(0, longPage), 0);
assert.strictEqual(thumbOffset(2100, longPage), 252);
assert.strictEqual(thumbOffset(4200, longPage), 504);
assert.strictEqual(Math.round(scrollTopForOffset(252, longPage)), 2100);
assert.strictEqual(Math.round(scrollTopForPointer(300, 0, longPage)), 2100);

const shortPage = metrics(600, 800, 600);
assert.strictEqual(shortPage.maxScroll, 0);
assert.strictEqual(shortPage.travel, 0);
assert.strictEqual(thumbOffset(100, shortPage), 0);
assert.strictEqual(scrollTopForOffset(100, shortPage), 0);

console.log("scroll-rail-tests: PASS");
